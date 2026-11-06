// The cloud machine the agent works on: whether it is awake, what it is, and what can be done with it.
// A machine sleeps between tasks and wakes by itself for the next one; waking it by hand is for the
// person who wants it ready before they start typing.

import { account, api, displayState, machineAction } from "../cloud.js";
import { fill, h, icon } from "../dom.js";
import { t } from "../i18n.js";
import { announce } from "../store.js";
import { machineDot } from "../views/titlebar.js";
import { toast } from "../views/toast.js";

const POLL_MS = 1500;
const archive = { phase: "idle", url: "" };

const poke = () => announce({ kind: "cloud" });

// "Export" asks the machine to pack its files; the server hands back a link when they are ready.
async function packFiles(machine) {
  archive.phase = "packing";
  poke();
  try {
    let job = await api(`/api/machines/${machine.id}/export`, { method: "POST" });
    while (job.status === "pending") {
      await new Promise((resolve) => setTimeout(resolve, POLL_MS));
      job = await api(`/api/exports/${job.id}`);
    }
    archive.phase = job.status === "ready" ? "ready" : "failed";
    archive.url = job.download_url ?? "";
  } catch (error) {
    archive.phase = "failed";
    toast(error.localized ?? String(error), "error");
  }
  poke();
}

function row(label, value) {
  return h("div", { class: "kv" }, h("span", { class: "kv-key" }, label), h("span", { class: "kv-value" }, value));
}

function run(action) {
  machineAction(action).catch((error) => toast(error.localized ?? String(error), "error"));
}

export const server = {
  id: "server",
  account: true,
  title: "server",
  icon: "server",
  badge: () => "",
  render(_agent, el) {
    const machine = account.machine;
    if (!machine) {
      fill(el, h("p", { class: "dim pad" }, t("no server")));
      return;
    }
    const status = displayState(machine);
    const busy = ["working", "waiting_for_you", "creating", "waking"].includes(status);
    const files = archive.phase === "ready"
      ? h("a", { class: "btn primary", href: archive.url, download: "" }, t("archive ready"))
      : h("button", { class: "btn", type: "button", disabled: archive.phase === "packing" || undefined, onclick: () => packFiles(machine) }, archive.phase === "packing" ? t("archive packing") : t("export archive"));
    fill(
      el,
      h("div", { class: "ctx-card px" },
        h("div", { class: "card-title" }, t("machine").toUpperCase()),
        h("div", { class: "server-status" }, h("span", { class: `state-dot ${machineDot(status)}` }), h("b", {}, t(`status ${status}`))),
        row("vCPU", String(machine.vcpu)),
        row("RAM", `${machine.memory_gb} GB`),
        row(t("files"), `${machine.disk_gb} GB`),
        machine.detail ? h("div", { class: "dim" }, machine.detail) : null,
      ),
      h("div", { class: "action-grid pad" },
        h("button", { class: "btn", type: "button", disabled: status !== "asleep" || undefined, onclick: () => run("wake"), title: t("wake") }, icon("bolt", 14), h("span", {}, t("wake"))),
        h("button", { class: "btn", type: "button", disabled: busy || status === "asleep" || undefined, onclick: () => run("sleep"), title: t("sleep") }, icon("pause", 14), h("span", {}, t("sleep"))),
      ),
      h("div", { class: "pad" }, files, archive.phase === "failed" ? h("p", { class: "dim" }, t("archive failed")) : null),
      h("p", { class: "dim pad" }, t("server sleeps note")),
    );
  },
};

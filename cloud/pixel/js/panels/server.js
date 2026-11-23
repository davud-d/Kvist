// The cloud machine the agent works on: whether it is awake, what it is, and what can be done with it.
// A machine sleeps between tasks and wakes by itself for the next one; waking it by hand is for the
// person who wants it ready before they start typing.

import { account, api, displayState, machineAction } from "../cloud.js";
import { fill, h, icon } from "../dom.js";
import { t } from "../i18n.js";
import { announce } from "../store.js";

const machineDot = (status) => ({ ready: "idle", asleep: "hollow", working: "running", waiting_for_you: "asks", creating: "starting", waking: "starting", error: "gone", none: "hollow" })[status] ?? "hollow";
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
  return h("div", { class: "kv" }, h("dt", { class: "kv-key" }, label), h("dd", { class: "kv-value" }, value));
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
    // One action at a time, the one that makes sense now: an asleep server is woken (the main thing to do),
    // an idle one may be put to sleep, a busy one has nothing to press.
    const action = status === "asleep"
      ? h("button", { class: "btn primary", type: "button", onclick: () => run("wake") }, icon("bolt", 16), h("span", {}, t("wake")))
      : busy ? null : h("button", { class: "btn", type: "button", onclick: () => run("sleep") }, icon("pause", 16), h("span", {}, t("sleep")));
    fill(
      el,
      h("div", { class: "server-status" }, h("span", { class: `state-dot ${machineDot(status)}` }), h("b", {}, t(`status ${status}`)), action),
      machine.detail ? h("p", { class: "dim" }, machine.detail) : null,
      h("dl", { class: "spec" },
        row(t("processor"), `${machine.vcpu} vCPU`),
        row(t("memory"), `${machine.memory_gb} ${t("gb")}`),
        row(t("disk"), `${machine.disk_gb} ${t("gb")}`)),
      h("p", { class: "dim" }, t("server sleeps note")),
      h("div", {}, files, archive.phase === "failed" ? h("p", { class: "dim" }, t("archive failed")) : null),
    );
  },
};

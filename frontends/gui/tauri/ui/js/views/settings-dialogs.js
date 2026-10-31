// The settings that need a window of their own: the model catalog, the list of
// sessions, renaming. Each ends in the same command the terminal runs.

import { runCommand } from "../actions.js";
import { sessionOp } from "../bridge.js";
import { fill, h } from "../dom.js";
import { t } from "../i18n.js";
import { subscribe } from "../store.js";
import { openModal } from "./modal.js";
import { toast } from "./toast.js";

/** Resolves with the `effects` of the agent's next `command_result` of that name. */
function resultOf(agent, name) {
  return new Promise((resolve) => {
    const off = subscribe((change) => {
      if (change.kind !== "agent" || change.id !== agent.id || agent.command?.name !== name) return;
      off();
      resolve(agent.command);
    });
  });
}

function button(label, onClick, variant = "") {
  return h("button", { class: `btn ${variant}`, type: "button", onclick: onClick }, label);
}

export async function openCatalog(agent) {
  runCommand(agent, "catalog");
  const result = await resultOf(agent, "catalog");
  const providers = result.effects.providers ?? [];
  if (!providers.length) return toast(result.message || "No providers", "error");
  const pick = { provider: providers.find((p) => p.current)?.provider ?? providers[0].provider, fast: "", smart: "" };
  const body = h("div", { class: "catalog" });
  const modal = openModal({ title: t("models"), content: body });
  const paint = () => {
    const entry = providers.find((p) => p.provider === pick.provider);
    pick.fast ||= entry.chosen.fast;
    pick.smart ||= entry.chosen.smart;
    const select = (role) => {
      const el = h("select", { class: "field", onchange: () => (pick[role] = el.value) }, entry.models.map((m) => h("option", { value: m, selected: m === pick[role] }, m)));
      return h("label", { class: "field-row" }, h("span", {}, role), el);
    };
    fill(body, 
      h("div", { class: "choice-list" }, providers.map((p) => h("div", { class: `choice${p.provider === pick.provider ? " on" : ""}`, onclick: () => { pick.provider = p.provider; pick.fast = ""; pick.smart = ""; paint(); } }, h("span", { class: "choice-label" }, p.label), p.current ? h("span", { class: "choice-note" }, "current") : null))),
      select("fast"),
      select("smart"),
      h("div", { class: "dialog-buttons" }, button(t("ok"), () => { runCommand(agent, "models", [pick.provider, pick.fast, pick.smart]); modal.close(); }, "primary"), button(t("cancel"), () => modal.close())),
    );
  };
  paint();
  return undefined;
}

export async function openSessions(agent) {
  const body = h("div", { class: "choice-list" });
  const modal = openModal({ title: t("sessions"), content: body });
  const paint = async () => {
    let reply;
    try {
      reply = await sessionOp(agent.workspace, "list");
    } catch (error) {
      fill(body, h("p", { class: "dim" }, String(error)));
      return;
    }
    const items = reply.session_items ?? (reply.sessions ?? []).map((name) => ({ name, title: name }));
    fill(body, 
      h("div", { class: "choice", onclick: () => { runCommand(agent, "new"); modal.close(); } }, h("span", { class: "choice-label" }, `+ ${t("new agent").toLowerCase()}`)),
      ...items.map((item) =>
        h("div", { class: `choice${item.name === agent.session ? " on" : ""}`, onclick: () => { if (item.name !== agent.session) runCommand(agent, "session", ["switch", item.name]); modal.close(); } }, h("span", { class: "choice-label" }, item.title || item.name), item.closed ? h("span", { class: "choice-note" }, t("closed")) : null, item.name === agent.session ? null : h("button", { class: "btn flat", type: "button", title: "delete", onclick: async (event) => { event.stopPropagation(); await sessionOp(agent.workspace, "delete", { sessionName: item.name }).catch((e) => toast(String(e), "error")); paint(); } }, "×")),
      ),
    );
  };
  paint();
}

export function openRename(agent) {
  const input = h("input", { class: "field", value: agent.title, spellcheck: "false" });
  const go = () => { if (input.value.trim()) runCommand(agent, "rename", input.value.trim().split(/\s+/)); modal.close(); };
  input.addEventListener("keydown", (event) => event.key === "Enter" && go());
  const modal = openModal({ title: t("rename"), content: [input, h("div", { class: "dialog-buttons" }, button(t("ok"), go, "primary"), button(t("cancel"), () => modal.close()))] });
}

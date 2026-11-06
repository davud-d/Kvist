// The settings popover: it grows out of the gear at the bottom left (a zoom
// from that corner) and closes the same way. Rows come from settings-defs.js.

import { fill, h } from "../dom.js";
import { t } from "../i18n.js";
import { animate, finished } from "../motion.js";
import { active, store, subscribe } from "../store.js";
import { SETTINGS } from "./settings-defs.js";

let popover = null;

// A row's label is a fixed phrase, or a function when it shows something that changes (the saved key).
const labelOf = (row) => (typeof row.label === "function" ? row.label() : t(row.label));

function segment(row, agent) {
  const current = String(row.get(agent, store.prefs) ?? "");
  const options = row.options(agent);
  return h("div", { class: `segment${row.compact ? " compact" : ""}` }, options.map((value) => h("button", { class: `seg${String(value) === current ? " on" : ""}`, type: "button", onclick: () => row.set(agent, value) }, t(row.words?.[value] ?? String(value)))));
}

function slider(row, agent) {
  const input = h("input", { type: "range", min: row.min, max: row.max, step: row.step, value: row.get(agent, store.prefs) });
  input.addEventListener("input", () => row.set(agent, Number(input.value)));
  return input;
}

function control(row, agent) {
  if (row.kind === "segment") return segment(row, agent);
  if (row.kind === "slider") return slider(row, agent);
  if (row.kind === "toggle") {
    const on = Boolean(row.get(agent, store.prefs));
    return h("button", { class: `switch${on ? " on" : ""}`, type: "button", role: "switch", "aria-checked": String(on), onclick: () => row.set(agent, !on) }, h("i"));
  }
  if (row.kind === "select") return h("select", { class: "field", onchange: (event) => row.set(agent, event.target.value) }, h("option", { value: "" }, "—"), row.options(agent).map((value) => h("option", { value }, value)));
  return h("button", { class: "btn", type: "button", onclick: () => { row.set(agent); closeSettings(); } }, labelOf(row));
}

function paint(body) {
  const agent = active();
  const busy = !agent || agent.state === "running";
  fill(body, 
    ...SETTINGS.map((group) =>
      h(
        "section",
        { class: "settings-group" },
        h("div", { class: "card-title" }, t(group.section).toUpperCase()),
        ...rowsOf(group.rows, agent, busy),
      ),
    ),
  );
}

function rowsOf(rows, agent, busy) {
  const build = (row) => {
          const locked = !row.local && (busy || agent?.state === "asleep" || agent?.state === "gone");
          const label = row.kind === "action" ? null : h("span", { class: "row-label" }, labelOf(row));
          const stacked = ["segment", "slider", "select"].includes(row.kind);
          return h("div", { class: `setting${stacked ? " stack" : ""}${locked ? " locked" : ""}`, title: locked ? t("available between turns") : "" }, label, control(row, agent));
  };
  const shown = rows.filter((row) => !row.when || row.when());
  const actions = shown.filter((row) => row.kind === "action");
  return [...shown.filter((row) => row.kind !== "action").map(build), actions.length ? h("div", { class: "action-grid" }, actions.map(build)) : null];
}

export function closeSettings() {
  if (!popover) return Promise.resolve();
  const el = popover.el;
  popover.off();
  document.removeEventListener("pointerdown", popover.outside, true);
  popover = null;
  return finished(animate(el, [{ opacity: 1, transform: "none" }, { opacity: 0, transform: "scale(0.9)" }], { curve: "in", ms: 150, fill: "forwards" })).then(() => el.remove());
}

export function toggleSettings(anchor) {
  if (popover) return closeSettings();
  const body = h("div", { class: "settings-body" });
  const el = h("div", { class: "settings px", role: "dialog", "aria-label": t("settings") }, body);
  document.getElementById("modals").append(el);
  paint(body);
  const outside = (event) => {
    if (!el.contains(event.target) && !anchor.contains(event.target)) closeSettings();
  };
  const off = subscribe(() => paint(body));
  popover = { el, off, outside };
  document.addEventListener("pointerdown", outside, true);
  el.addEventListener("keydown", (event) => event.key === "Escape" && closeSettings());
  // It unfolds from the gear's corner: origin bottom-left, a soft overshoot.
  animate(el, [{ opacity: 0, transform: "scale(0.82) translateY(12px)" }, { opacity: 1, transform: "none" }], { spring: "bouncy" });
  return undefined;
}

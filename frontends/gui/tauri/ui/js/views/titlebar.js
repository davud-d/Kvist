// The window has no system frame, so it draws its own: a drag strip, the
// buttons that matter, and thin edges that resize it.

import { windowControls } from "../bridge.js";
import { basename, frameThrottle, h, icon } from "../dom.js";
import { t } from "../i18n.js";
import { active, setMode, setPref, store, subscribe } from "./../store.js";

const EDGES = [
  ["North", "n"], ["South", "s"], ["West", "w"], ["East", "e"],
  ["NorthWest", "nw"], ["NorthEast", "ne"], ["SouthWest", "sw"], ["SouthEast", "se"],
];

export function mountTitlebar(root) {
  const crumb = h("div", { class: "crumb", "data-tauri-drag-region": "" });
  const overview = h("button", { class: "btn", type: "button", title: `${t("overview")} (Ctrl+\\)` }, icon("grid", 14));
  const inspector = h("button", { class: "btn", type: "button", title: t("inspector") }, icon("panel", 14));
  const frame = h(
    "div",
    { class: "frame-buttons" },
    h("button", { class: "btn flat", type: "button", onclick: () => windowControls.minimize() }, icon("min", 12)),
    h("button", { class: "btn flat", type: "button", onclick: () => windowControls.toggleMaximize() }, icon("max", 12)),
    h("button", { class: "btn flat close", type: "button", onclick: () => windowControls.close() }, icon("close", 12)),
  );
  root.append(
    h("div", { class: "brand", "data-tauri-drag-region": "" }, h("span", { class: "squirrel small" }), h("span", { class: "brand-name" }, "KVIST")),
    crumb,
    h("div", { class: "title-actions" }, overview, inspector),
    frame,
  );
  root.addEventListener("dblclick", (event) => {
    if (event.target.closest("button")) return;
    windowControls.toggleMaximize();
  });
  overview.addEventListener("click", () => setMode(store.mode === "overview" ? "chat" : "overview"));
  inspector.addEventListener("click", () => setPref("inspector", !store.prefs.inspector));

  for (const [direction, name] of EDGES) {
    document.body.append(h("div", { class: `edge edge-${name}`, onpointerdown: (event) => { event.preventDefault(); windowControls.startResizing(direction); } }));
  }

  const redraw = frameThrottle(() => {
    const agent = active();
    crumb.textContent = agent ? `${basename(agent.workspace)}${agent.title ? ` — ${agent.title}` : ""}` : "";
    overview.classList.toggle("on", store.mode === "overview");
    inspector.classList.toggle("on", store.prefs.inspector);
  });
  subscribe(redraw);
  redraw();
}

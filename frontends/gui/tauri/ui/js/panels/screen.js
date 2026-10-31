// The agent's own browser. The core serves a view-only MJPEG stream on a
// loopback address (docs/UI_API.md §8f); the page only ever shows it in an
// <img>, never loads it as a page.

import { openExternal } from "../bridge.js";
import { fill, h } from "../dom.js";
import { t } from "../i18n.js";

let shown = new Map();

export const screen = {
  id: "screen",
  title: "screen",
  icon: "globe",
  badge: (agent) => (agent.screen ? "●" : ""),
  render(agent, el) {
    const view = agent.screen;
    if (!view) {
      shown.delete(agent.id);
      fill(el, h("p", { class: "dim pad" }, t("no screen")));
      return;
    }
    // The same stream is not reattached on every redraw, or it would restart.
    if (shown.get(agent.id) === view.stream_url && el.querySelector(".screen-view")) return;
    shown.set(agent.id, view.stream_url);
    const picture = view.state === "unavailable" ? h("p", { class: "dim pad" }, view.state) : h("img", { class: "screen-view", src: view.stream_url, alt: view.title || "agent screen" });
    const open = view.url ? h("button", { class: "btn", type: "button", onclick: () => openExternal(view.url) }, t("open in browser")) : null;
    fill(el, h("div", { class: "screen-card px" }, h("div", { class: "screen-bar" }, h("span", { class: `state-dot ${view.state}` }), h("span", { class: "screen-title" }, view.title || view.skill), h("span", { class: "chip" }, view.state)), picture), open);
  },
};

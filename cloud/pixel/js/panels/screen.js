// The agent's own screens: the browser skill and the desktop skill draw on a virtual screen, and this is
// how the owner watches it.
//
// Privacy is the point of how it works. The picture never has an address a page could share: the cloud
// hands it only to the signed-in owner of the task (anyone else gets "not found"), keeps it in memory
// only, and the machine sends pictures only while this panel keeps asking for them. So the panel asks
// by polling, with the cookie and the client header, and shows each answer as a blob that dies with it.

import { fill, h } from "../dom.js";
import { t } from "../i18n.js";
import { loginUrl } from "../cloud.js";

const FRAME_PATH = /^\/api\/tasks\/\d+\/screens\/[\w.-]+\/frame\.jpg$/;
const POLL_VISIBLE_MS = 600;
const POLL_HIDDEN_MS = 3000;
/** `${agent id}:${screen id}` -> the card that keeps asking for pictures while it is on the page */
const watchers = new Map();

/** Asks for a picture now and then, for as long as `picture` is on the page. */
function watch(key, frameUrl, picture, waiting) {
  let previous = "";
  const tick = async () => {
    // The panel was redrawn for another agent or another tab: nobody looks, so stop asking.
    if (!picture.isConnected) {
      watchers.delete(key);
      if (previous) URL.revokeObjectURL(previous);
      return;
    }
    try {
      const reply = await fetch(frameUrl, { credentials: "same-origin", cache: "no-store", headers: { "X-Kvist-Client": "1" } });
      if (reply.status === 401) location.assign(loginUrl());
      else if (reply.status === 200) {
        const next = URL.createObjectURL(await reply.blob());
        picture.src = next;
        picture.hidden = false;
        waiting.hidden = true;
        if (previous) URL.revokeObjectURL(previous);
        previous = next;
      }
    } catch (error) {
      console.warn("screen picture", error);
    }
    setTimeout(tick, document.visibilityState === "visible" ? POLL_VISIBLE_MS : POLL_HIDDEN_MS);
  };
  // The card is attached to the page right after it is built; the first look happens a moment later.
  setTimeout(tick, 0);
}

function card(agent, view) {
  const key = `${agent.id}:${view.screen_id}`;
  const known = watchers.get(key);
  if (known && known.frameUrl === view.frame_url) return known.paint(view);
  const picture = h("img", { class: "screen-view", alt: view.title || view.skill, hidden: true });
  const waiting = h("p", { class: "dim pad" }, t("waiting picture"));
  const bar = h("div", { class: "screen-bar" });
  const el = h("div", { class: "screen-card px" }, bar, picture, waiting);
  const paint = (next) => {
    bar.replaceChildren(h("span", { class: `state-dot ${next.state}` }), h("span", { class: "screen-title" }, next.title || next.skill), h("span", { class: "chip" }, next.state));
    return el;
  };
  paint(view);
  // Only the cloud's own picture address is ever requested, whatever an event says.
  if (FRAME_PATH.test(view.frame_url)) {
    watchers.set(key, { frameUrl: view.frame_url, paint });
    watch(key, view.frame_url, picture, waiting);
  }
  return el;
}

export const screen = {
  id: "screen",
  title: "screen",
  icon: "globe",
  badge: (agent) => (agent.screens.size ? "●" : ""),
  render(agent, el) {
    const views = [...agent.screens.values()];
    if (!views.length) {
      fill(el, h("p", { class: "dim pad" }, t("no screen")));
      return;
    }
    fill(el, ...views.map((view) => card(agent, view)), h("p", { class: "dim pad privacy" }, t("private screen")));
  },
};

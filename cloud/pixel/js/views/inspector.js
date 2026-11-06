// The right-hand side: the hours, the server, the agent's screens, the files it changed, helpers, jobs.
// It opens by itself when something worth watching appears (a helper, a screen).

import { onAccount } from "../cloud.js";
import { fill, frameThrottle, h, icon } from "../dom.js";
import { t } from "../i18n.js";
import { panelById, panels } from "../panels/index.js";
import { active, setPref, store, subscribe } from "../store.js";

export function mountInspector(root) {
  const tabs = h("div", { class: "tabs" });
  const body = h("div", { class: "inspector-body" });
  root.append(h("div", { class: "inspector-inner" }, tabs, body));
  const seen = new Map();

  function reveal(agent) {
    const was = seen.get(agent.id) ?? { helpers: 0, screen: false };
    const now = { helpers: agent.helpers.size, screen: Boolean(agent.screen) };
    seen.set(agent.id, now);
    if (now.screen && !was.screen) openOn("screen");
    else if (now.helpers > was.helpers) openOn("helpers");
  }

  function openOn(tab) {
    if (store.prefs.inspectorTab !== tab) setPref("inspectorTab", tab);
    if (!store.prefs.inspector) setPref("inspector", true);
  }

  const redraw = frameThrottle(() => {
    const agent = active();
    if (agent) reveal(agent);
    const current = panelById(store.prefs.inspectorTab);
    fill(tabs, 
      ...panels.map((panel) => {
        const count = agent ? panel.badge(agent) : "";
        return h("button", { class: `tab${panel.id === current.id ? " on" : ""}`, type: "button", title: t(panel.title), onclick: () => setPref("inspectorTab", panel.id) }, icon(panel.icon, 14), count ? h("span", { class: "tab-badge" }, String(count)) : null);
      }),
    );
    // Hours and the server belong to the account, not to a task, so they show with or without one.
    if (agent || current.account) current.render(agent, body);
    else fill(body);
  });
  subscribe(redraw);
  onAccount(redraw);
  redraw();
}

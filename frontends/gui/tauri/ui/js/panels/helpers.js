// What the helpers are doing: one card each, its task, its last calls live.

import { stopHelper } from "../actions.js";
import { fill, h, icon } from "../dom.js";
import { t } from "../i18n.js";

const LIVE = new Set(["starting", "running", "paused"]);

function activityRow(entry) {
  const state = entry.phase === "step" ? "run" : entry.ok === false ? "fail" : "ok";
  return h("div", { class: `activity ${state}` }, state === "run" ? h("span", { class: "spin" }) : h("span", { class: `mark ${state}` }, state === "ok" ? "✓" : "✕"), h("span", { class: "activity-label" }, entry.label), entry.summary ? h("span", { class: "activity-summary" }, entry.summary) : null);
}

function card(agent, helper) {
  const live = LIVE.has(helper.state);
  return h(
    "div",
    { class: `helper px ${helper.state}` },
    h("div", { class: "helper-head" }, icon("bot", 14), h("span", { class: "helper-task" }, helper.task || helper.agent_id), live ? h("button", { class: "btn flat", type: "button", title: t("stop"), onclick: () => stopHelper(agent, helper.id) }, icon("stop", 12)) : h("span", { class: "chip" }, helper.state)),
    helper.activity.length ? h("div", { class: "activity-list" }, helper.activity.slice(-4).map(activityRow)) : null,
    helper.summary ? h("div", { class: "helper-summary" }, helper.summary) : null,
  );
}

export const helpers = {
  id: "helpers",
  title: "helpers",
  icon: "bot",
  badge: (agent) => [...agent.helpers.values()].filter((x) => LIVE.has(x.state)).length || "",
  render(agent, el) {
    const all = [...agent.helpers.values()].reverse();
    fill(el, ...(all.length ? all.map((helper) => card(agent, helper)) : [h("p", { class: "dim pad" }, t("no helpers"))]));
  },
};

// The start of a new session: what the agent is for, what is still to set up, and a few things to ask it.
// It is not a hero page. It says what happens when the person sends the task and gets out of the way.

import { account } from "./account.js";
import { h, icon } from "./dom.js";
import { t } from "./i18n.js";
import { setupSteps } from "./rules.js";

// What the agent can really do on its machine (read, change and run a repository), so each of these works as written.
const SUGGESTIONS = ["suggest tests", "suggest explain", "suggest review", "suggest readme"];

export function homeView({ projectId, onChooseProject, onSuggest, onSetup }) {
  const steps = setupSteps(account);
  const todo = steps.filter((step) => !step.done);
  const name = projectId ? account.projects.find((p) => String(p.id) === String(projectId))?.name : "";
  return h("section", { class: "home" },
    h("h1", {}, t("home title")),
    h("p", { class: "lead" }, t("home lead")),
    h("button", { class: "chip", type: "button", onclick: onChooseProject },
      icon("folder", 16), h("span", {}, name || t("no project")), icon("chevron-down", 16)),
    todo.length
      ? h("div", { class: "group setup" },
        h("div", { class: "group-label" }, t("before first")),
        steps.map((step, index) => h("button", { class: `row${step.done ? " done" : ""}`, type: "button", disabled: step.done, onclick: () => onSetup(step.id) },
          h("span", { class: "step-no" }, step.done ? icon("check", 14) : String(index + 1)),
          h("span", { class: "row-main" }, h("span", {}, t(`setup ${step.id}`)), h("small", { class: "dim" }, t(`setup ${step.id} hint`))),
          step.done ? null : icon("chevron-right", 18))))
      : h("div", { class: "group suggest" }, SUGGESTIONS.map((key) =>
        h("button", { class: "row", type: "button", onclick: () => onSuggest(t(key)) }, h("span", { class: "row-main" }, t(key)), icon("arrow-up", 16)))));
}

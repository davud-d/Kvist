// Funnel step 4, and the "new task" screen afterwards: model key, repository, task, Run.
// The machine has been preparing since sign-in; there is no "please wait" screen.
import { api } from "../api.js";
import { h, replace, skeleton } from "../dom.js";
import { t } from "../i18n.js";
import { keyWidget } from "../keywidget.js";
import { watchMachine } from "../machine.js";
import { errorBox, machinePill } from "../ui.js";

const MACHINE_TEXT = {
  ready: "onboarding.machineReady", asleep: "onboarding.machineReady", working: "onboarding.machineReady",
  waiting_for_you: "onboarding.machineReady", waking: "onboarding.machineWaking", awake: "onboarding.machineWaking",
  creating: "onboarding.machineCreating", error: "onboarding.machineError",
};

function repoSection(repos) {
  let choice = repos.length ? repos[0].url : "";
  const paste = h("input.input", { type: "url", placeholder: t("onboarding.repoPastePlaceholder"), id: "repo-link",
    "aria-label": t("onboarding.repoPaste") });
  const option = (value, label, extra) => h("li", {},
    h("label.choice", {},
      h("input", { type: "radio", name: "repo", value, checked: value === choice,
        onchange: () => { choice = value; if (value === "paste") paste.focus(); } }),
      h("span", { text: label }), extra || null));
  const list = h("ul.choice-list", {},
    repos.slice(0, 8).map((repo) => option(repo.url, repo.name)),
    option("paste", t("onboarding.repoPaste")),
    option("", t("onboarding.repoNone")));
  paste.addEventListener("focus", () => {
    choice = "paste";
    list.querySelector('input[value="paste"]').checked = true;
  });
  const element = h("div.stack", {},
    repos.length ? null : h("p.small", { text: t("onboarding.repoNoList") }),
    list, paste);
  return { element, value: () => (choice === "paste" ? paste.value.trim() : choice) };
}

export async function onboardingScreen(root, { navigate }) {
  replace(root, skeleton(6));
  const [subscription, providers, saved, repoList, tasks] = await Promise.all([
    api("/api/billing/subscription"),
    api("/api/model-providers"),
    api("/api/model-key"),
    api("/api/github/repos").catch(() => ({ repos: [] })),
    api("/api/tasks"),
  ]);
  if (subscription.status === "none") {
    navigate("/start", { replace: true });
    return null;
  }
  const first = tasks.length === 0;
  let keyValid = false;
  const run = h("button.btn.wide", { type: "button", text: t("onboarding.run") });
  const why = h("p.hint", { "aria-live": "polite" });
  const failure = h("div");
  const goal = h("textarea.input", { id: "goal", placeholder: t("onboarding.goalPlaceholder"), rows: 4 });
  const presetButtons = t("onboarding.presets").map((text) => h("button.preset", {
    type: "button", "aria-pressed": "false", text,
    onclick: (event) => {
      goal.value = text;
      for (const button of presetButtons) button.setAttribute("aria-pressed", String(button === event.currentTarget));
      update();
    },
  }));
  const update = () => {
    const ready = keyValid && goal.value.trim().length > 0;
    run.disabled = !ready;
    why.textContent = ready ? "" : !keyValid ? t("onboarding.needKey") : t("onboarding.needGoal");
  };
  goal.addEventListener("input", () => {
    for (const button of presetButtons) button.setAttribute("aria-pressed", String(button.textContent === goal.value));
    update();
  });
  const repo = repoSection(repoList.repos || []);
  const machineLine = h("p.row.small", { "aria-live": "polite" });

  run.addEventListener("click", async () => {
    run.disabled = true;
    run.textContent = t("onboarding.runStarting");
    replace(failure);
    try {
      const task = await api("/api/tasks", { method: "POST", body: { goal: goal.value.trim(), repo_url: repo.value() } });
      navigate(`/tasks/${task.id}`);
    } catch (error) {
      run.textContent = t("onboarding.run");
      update();
      replace(failure, errorBox(error));
    }
  });

  replace(root,
    h("section.stack-lg", {},
      h("div.stack", {},
        first ? h("p.kicker", { text: t("onboarding.kicker") }) : null,
        h("h1.h2", { text: first ? t("onboarding.title") : t("onboarding.newTitle") }),
        machineLine),
      h("div", {}, h("h2.section-title", { text: t("onboarding.modelTitle") }),
        keyWidget({ providers, saved, onValid: (valid) => { keyValid = valid; update(); } })),
      h("div", {}, h("h2.section-title", { text: t("onboarding.repoTitle") }), repo.element),
      h("div.stack", {},
        h("h2.section-title", { text: t("onboarding.taskTitle") }),
        h("div.presets", { role: "group", "aria-label": t("onboarding.taskTitle") }, presetButtons),
        h("label.field", { for: "goal" }, h("span", { text: t("onboarding.goalLabel") }), goal)),
      h("div.stack", {}, run, why, failure)));
  update();
  return watchMachine((machine) => {
    const state = machine ? machine.display_status : "creating";
    replace(machineLine, machinePill(state), h("span", { text: t(MACHINE_TEXT[state] || "onboarding.machineWaking") }));
  });
}

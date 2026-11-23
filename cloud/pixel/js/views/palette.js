// Every task, searchable (Ctrl+K or "Tasks" on the rail): the way back to earlier work. Waiting and running tasks
// come first, the rest newest first; typing filters by the task's name or its project's. Arrows move, Enter opens.

import { needsYou, rowSignal } from "../attention.js";
import { openTask } from "../actions.js";
import { WORKING, account, projectName, titleOf, workspaceOf } from "../cloud.js";
import { fill, h, icon, spinner } from "../dom.js";
import { lang, t } from "../i18n.js";
import { relativeTime } from "../attention.js";
import { animate, finished, popIn } from "../motion.js";
import { store } from "../store.js";

let open = null;

const timeOf = (task) => task.finished_at ?? task.started_at ?? task.created_at ?? "";
const rank = (task) => (needsYou(task) ? 0 : WORKING.has(task.status) ? 1 : 2);

function ordered(query) {
  const words = query.trim().toLowerCase();
  const all = account.tasks.filter((task) => {
    if (!words) return true;
    const haystack = `${titleOf(task)} ${projectName(workspaceOf(task), account.projects, "")}`.toLowerCase();
    return haystack.includes(words);
  });
  return all.sort((a, b) => rank(a) - rank(b) || (timeOf(a) < timeOf(b) ? 1 : -1));
}

/** One task as a row: its signal, its name, a line under it (the project unless `detail` says more), when. */
export function taskRow(task, { onclick, selected = false, detail = "" } = {}) {
  const signal = rowSignal(task, store.seen);
  const project = projectName(workspaceOf(task), account.projects, t("no project"));
  return h(
    "button",
    { class: `task-row${selected ? " on" : ""}`, type: "button", onclick, data: { id: String(task.id) } },
    h("span", { class: `signal ${signal}` }, signal === "running" ? spinner() : null),
    h("span", { class: "task-row-main" },
      h("span", { class: "task-row-title" }, titleOf(task) || t("new task")),
      h("span", { class: "task-row-sub" }, h("span", { class: "task-row-project" }, project), detail ? h("span", { class: "task-row-detail" }, detail) : null)),
    h("span", { class: "task-row-time" }, relativeTime(timeOf(task), lang.current)),
  );
}

export function closePalette() {
  if (!open) return Promise.resolve();
  const { scrim, card, onKey } = open;
  open = null;
  document.removeEventListener("keydown", onKey, true);
  animate(scrim, [{ opacity: 1 }, { opacity: 0 }], { ms: 140, fill: "forwards" });
  return finished(animate(card, [{ opacity: 1, transform: "none" }, { opacity: 0, transform: "scale(0.98)" }], { ms: 140, fill: "forwards" })).then(() => scrim.remove());
}

export function openPalette() {
  if (open) return closePalette();
  const input = h("input", { class: "palette-input", type: "search", placeholder: t("search tasks"), spellcheck: "false", "aria-label": t("search tasks") });
  const list = h("div", { class: "palette-list", role: "listbox" });
  const card = h("div", { class: "palette", role: "dialog", "aria-modal": "true", "aria-label": t("all tasks") }, h("div", { class: "palette-head" }, icon("search", 18), input, h("kbd", {}, "Esc")), list);
  const scrim = h("div", { class: "scrim top" }, card);
  let index = 0;
  let shown = [];

  const go = (task) => {
    closePalette();
    openTask(task);
  };
  const paint = () => {
    shown = ordered(input.value);
    index = Math.min(index, Math.max(0, shown.length - 1));
    fill(list, shown.length ? shown.map((task, i) => taskRow(task, { selected: i === index, onclick: () => go(task) })) : h("p", { class: "palette-empty" }, account.tasks.length ? t("nothing found") : t("no tasks yet")));
    list.querySelector(".task-row.on")?.scrollIntoView({ block: "nearest" });
  };
  const onKey = (event) => {
    if (event.key === "Escape") closePalette();
    else if (event.key === "ArrowDown") index = Math.min(shown.length - 1, index + 1);
    else if (event.key === "ArrowUp") index = Math.max(0, index - 1);
    else if (event.key === "Enter" && shown[index]) go(shown[index]);
    else return;
    event.preventDefault();
    event.stopPropagation();
    if (open) paint();
  };
  input.addEventListener("input", () => {
    index = 0;
    paint();
  });
  scrim.addEventListener("pointerdown", (event) => event.target === scrim && closePalette());
  document.addEventListener("keydown", onKey, true);
  document.getElementById("modals").append(scrim);
  open = { scrim, card, onKey };
  paint();
  animate(scrim, [{ opacity: 0 }, { opacity: 1 }], { ms: 160 });
  popIn(card, "50% 0");
  input.focus();
  return undefined;
}

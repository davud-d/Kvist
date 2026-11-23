// The home screen: where work starts and where the person comes back to. One question and one field to hand a
// task over (the project it goes to sits inside the field), and under it only what needs a look, in the order it
// needs it: tasks waiting for the person, tasks that finished since they last looked, tasks still running, and a
// few recent ones as the way back. A list with nothing in it is not drawn; a quiet account is just the field.

import { homeGroups } from "../attention.js";
import { openTask, pageCommand, pageCommandWithoutTask, startTask } from "../actions.js";
import { account, duration, firstLine, onAccount, projectName } from "../cloud.js";
import { fill, frameThrottle, h, icon } from "../dom.js";
import { t } from "../i18n.js";
import { setPref, store, subscribe } from "../store.js";
import { attachCommandHint, offeredCommands } from "./commands.js";
import { modelFamily } from "../models.js";
import { mountNotices } from "./notices.js";
import { taskRow } from "./palette.js";
import { openProjectDialog } from "./project-dialog.js";
import { fieldMenus, runPicks } from "./run-picks.js";

// Providers whose models Kvist picks itself, a fast one and a strong one, so there is no single model to name.
const KVIST_PICKS = new Set(["anthropic", "deepseek"]);
const KVIST_PICKS_LINES = ["model line kvist picks 1", "model line kvist picks 2"];
// One pick per page load: the line changes between visits, never while the screen redraws.
const VISIT = Math.floor(Math.random() * 1000);

/** The home question names the model the task will run on, and a line under it says what that model is for. */
function paintQuestion(title, line) {
  const key = account.key;
  const family = key?.set ? modelFamily(key.model) : null;
  title.textContent = family ? t("home title for").replace("{name}", family.short) : t("home title");
  const lines = family ? family.lines : key?.set && !key.model && KVIST_PICKS.has(key.provider) ? KVIST_PICKS_LINES : [];
  const words = lines.length ? t(lines[VISIT % lines.length]) : "";
  line.textContent = words;
  line.hidden = !words;
}

/** What a waiting task waits for, in the person's words. */
function waitingFor(task) {
  if (task.status === "waiting_for_user") return t("asks you");
  return task.reason ? `${t("paused")}: ${t(`pause: ${task.reason}`)}` : t("paused");
}

const plainLine = (text) => firstLine(String(text ?? "").replace(/[`*#>]/g, ""));

function section(title, rows) {
  if (!rows.length) return null;
  return h("section", { class: "home-section" }, h("h2", { class: "home-section-title" }, title, h("span", { class: "count" }, String(rows.length))), h("div", { class: "home-rows" }, rows));
}

// The project a new task goes to: the person's last choice while it exists, else the newest project.
function chosenProject() {
  const ids = account.projects.map((project) => String(project.id));
  return ids.includes(store.prefs.project) ? store.prefs.project : ids[0] ?? "";
}

function projectMenu(close) {
  const pick = (id) => {
    setPref("project", id);
    close();
  };
  return h("div", { class: "menu", role: "menu" },
    account.projects.map((project) => h("button", { class: `menu-item${String(project.id) === chosenProject() ? " on" : ""}`, type: "button", role: "menuitemradio", "aria-checked": String(String(project.id) === chosenProject()), onclick: () => pick(String(project.id)) }, icon("folder", 16), h("span", {}, project.name))),
    h("div", { class: "menu-sep" }),
    h("button", { class: "menu-item", type: "button", role: "menuitem", onclick: () => { close(); openProjectDialog(); } }, icon("plus", 16), h("span", {}, t("new project"))));
}

export function mountHome(root) {
  const input = h("textarea", { class: "composer-input", rows: "1", spellcheck: "true", placeholder: t("write your task"), "aria-label": t("write your task") });
  const send = h("button", { class: "btn primary send", type: "button", title: t("send"), "aria-label": t("send"), disabled: true }, icon("send", 18));
  const project = h("button", { class: "project-pick", type: "button", "aria-haspopup": "menu", title: t("project") });
  const foot = h("div", { class: "composer-foot" });
  const composer = h("div", { class: "composer home-composer" }, input, foot);
  // One menu at a time under the field: the project's, the model's or the effort's.
  const menus = fieldMenus(input);
  const picks = runPicks(composer, menus);
  foot.append(project, picks.model, picks.effort, h("span", { class: "spacer" }), send);
  // Before the field's own keys, so the hint answers Enter and the arrows while it is open.
  // Only what needs no task: the rest of Kvist's commands act on a task's conversation.
  attachCommandHint(input, composer, () => submit(), () =>
    offeredCommands(account.commands.manifest, account.commands.hidden).filter((command) => pageCommandWithoutTask(command.name)));
  const notices = h("div", { class: "notices" });
  const lists = h("div", { class: "home-lists" });
  const title = h("h1", { class: "home-title", id: "home-title" }, t("home title"));
  const line = h("p", { class: "home-line" });
  const inner = h("div", { class: "home-inner" }, title, line, composer, notices, lists);
  root.append(inner);
  mountNotices(notices);

  project.addEventListener("click", () => menus.toggle(project, () => {
    const built = projectMenu(menus.close);
    composer.append(built);
    return built;
  }));
  // `/provider` and `/models` typed here open this picker (actions.js); in a task, the task's own field does.
  document.addEventListener("kvist:model-picker", () => {
    if (store.mode === "home") queueMicrotask(picks.openModel);
  });

  const submit = async () => {
    const text = input.value.trim();
    if (!text) return;
    // A command about the account does not start a task.
    if (await pageCommand(text)) {
      input.value = "";
      send.disabled = true;
      return;
    }
    input.value = "";
    send.disabled = true;
    // The words come back if the task could not start (no key, no hours, no project).
    const sent = await startTask(chosenProject(), text);
    if (!sent && !input.value) {
      input.value = text;
      send.disabled = false;
    }
  };
  input.addEventListener("input", () => {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 280)}px`;
    send.disabled = !input.value.trim();
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      submit();
    } else if (event.key === "Escape") menus.close();
  });
  send.addEventListener("click", submit);

  const open = (task) => openTask(task);

  const redraw = frameThrottle(() => {
    root.hidden = store.mode !== "home";
    if (root.hidden) return;
    const workspace = chosenProject();
    fill(project, icon("folder", 16), h("span", {}, workspace ? projectName(workspace, account.projects, t("no project")) : t("pick project")), icon("chev", 14));
    paintQuestion(title, line);
    picks.redraw();
    const groups = homeGroups(account.tasks, store.seen);
    const content = [
      section(t("waiting for you"), groups.waiting.map((task) => taskRow(task, { detail: waitingFor(task), onclick: () => open(task) }))),
      section(t("while you were away"), groups.unseen.map((task) => taskRow(task, { detail: plainLine(task.final_message) || t(task.status), onclick: () => open(task) }))),
      section(t("in progress"), groups.running.map((task) => taskRow(task, { detail: t("running"), onclick: () => open(task) }))),
      section(t("recent"), groups.recent.map((task) => taskRow(task, { detail: task.used_seconds >= 60 ? duration(task.used_seconds) : "", onclick: () => open(task) }))),
    ].filter(Boolean);
    fill(lists, content);
    inner.classList.toggle("quiet", content.length === 0);
  });
  subscribe((change) => {
    redraw();
    if (change.kind === "mode" && store.mode === "home") queueMicrotask(() => input.focus());
  });
  onAccount(redraw);
  redraw();
  return { focus: () => input.focus() };
}

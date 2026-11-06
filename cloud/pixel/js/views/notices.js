// Warnings above the chat: said before the thing bites (hours nearly gone, no key, no project, a task that
// stopped and why). Each says what to do about it where there is something to do.

import { account, onAccount, warnings } from "../cloud.js";
import { fill, frameThrottle, h } from "../dom.js";
import { t } from "../i18n.js";
import { active, subscribe } from "../store.js";
import { openProjectDialog } from "./project-dialog.js";
import { openKeyDialog } from "./settings-dialogs.js";

/** What the page itself found (an address that is not the person's), as opposed to what the account says. */
let found = [];
const dismissed = new Set();
let redraw = () => {};

export function setNotices(list) {
  found = list;
  redraw();
}

const ACTIONS = {
  key: ["warn fix key", () => openKeyDialog()],
  plan: ["manage plan", () => location.assign("/dashboard")],
  start: ["start trial", () => location.assign("/start")],
  project: ["add project", () => openProjectDialog()],
};

function bar(item) {
  const text = [t(item.text).replace("{value}", item.value ?? ""), item.reason ? `(${t(item.reason)})` : "", item.value && !t(item.text).includes("{value}") ? `: ${item.value}` : ""].filter(Boolean).join(" ");
  const [label, run] = ACTIONS[item.action] ?? [];
  return h(
    "div",
    { class: `notice ${item.level}`, role: item.level === "error" ? "alert" : "status" },
    h("span", { class: "notice-text" }, text),
    run ? h("button", { class: "btn", type: "button", onclick: run }, t(label)) : null,
    h("button", { class: "btn flat", type: "button", title: t("dismiss"), onclick: () => { dismissed.add(item.id); redraw(); } }, "×"),
  );
}

export function mountNotices(root) {
  redraw = frameThrottle(() => {
    const shown = warnings(account, active(), found).filter((item) => !dismissed.has(item.id));
    fill(root, shown.map(bar));
  });
  subscribe(redraw);
  onAccount(redraw);
  redraw();
}


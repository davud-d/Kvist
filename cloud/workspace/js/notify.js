// Browser notifications: a task that ends while the person looks at another tab or window tells them so on the
// computer itself. The page asks for the permission when the person hands over a task (a browser asks only in
// answer to a click or a key, and that is the moment the question makes sense); a "no" is the browser's to keep.
// Only the moves that need the person are told: the task finished, failed, or asks something. A pause or a stop
// the person made, they already know about.

import { WORKING, account, onAccount, titleOf } from "./cloud.js";
import { t } from "./i18n.js";

const TOLD = { done: "notify done", failed: "notify failed", waiting_for_user: "notify asks" };

const supported = () => typeof Notification !== "undefined";

/** Asks once, from a click or a key; does nothing when the person already answered. */
export function askToNotify() {
  if (!supported() || Notification.permission !== "default") return;
  Notification.requestPermission().catch(() => {});
}

/** What changed for the person since the last look: the tasks that left work for one of the states in TOLD. */
export function newsOf(before, tasks) {
  return tasks.filter((task) => WORKING.has(before.get(task.id)) && TOLD[task.status]);
}

/** Watches the task list for the whole page; `open(task)` is what a click on a notification does. */
export function watchTasks(open) {
  let before = null;
  onAccount(() => {
    const now = new Map(account.tasks.map((task) => [task.id, task.status]));
    // The first list is what was there before the page opened: nothing in it is news.
    const news = before ? newsOf(before, account.tasks) : [];
    before = now;
    if (!news.length || !supported() || Notification.permission !== "granted") return;
    if (!document.hidden && document.hasFocus()) return;
    for (const task of news) tell(task, open);
  });
}

function tell(task, open) {
  const detail = task.status === "done" ? String(task.final_message ?? "").trim().split("\n")[0].slice(0, 140) : "";
  const note = new Notification(titleOf(task), {
    body: detail ? `${t(TOLD[task.status])}: ${detail}` : t(TOLD[task.status]),
    icon: "/workspace/icon-128.png",
    // One notification per task: a second tab of the workspace replaces it instead of adding another.
    tag: `kvist-task-${task.id}`,
  });
  note.onclick = () => {
    window.focus();
    open(task);
    note.close();
  };
}

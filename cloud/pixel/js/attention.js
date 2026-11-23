// What needs the person's eyes, from the task list alone (no DOM, no network), so the home screen, the rail's
// dots and the task list agree: yellow is "waiting for you", blue is "something happened since you last looked".
//
// "Seen" lives in this browser: when a task is opened its finish time is remembered, and a task that finished
// (or stopped) later than that is news. The first visit sets a baseline, so years of old tasks are not all news.

import { FINISHED, WORKING } from "./cloud.js";

const SEEN_KEY = "kvist.cloud.seen";
// Kept per task; the oldest are forgotten beyond this many, they are long past news.
const SEEN_LIMIT = 500;
const RECENT = 5;

export const needsYou = (task) => task.status === "waiting_for_user" || task.status === "paused";

/** When something last happened to a task that the person may not have seen (its finish or stop), in ms; 0 for none. */
function stampOf(task) {
  if (!FINISHED.has(task.status) && task.status !== "paused") return 0;
  return Date.parse(task.finished_at ?? "") || 0;
}

export function isUnseen(task, seen) {
  const stamp = stampOf(task);
  if (!stamp) return false;
  const looked = seen.tasks[String(task.id)] ?? seen.baseline;
  return stamp > looked;
}

const timeOf = (task) => task.finished_at ?? task.started_at ?? task.created_at ?? "";
const newestFirst = (a, b) => (timeOf(a) < timeOf(b) ? 1 : -1);

/**
 * The home screen's lists. A task sits in exactly one: waiting for you first, then news, then running, then the
 * few most recent of the rest (the way back to earlier work; the full list is the task search).
 */
export function homeGroups(tasks, seen) {
  const sorted = [...tasks].sort(newestFirst);
  const waiting = sorted.filter(needsYou);
  const unseen = sorted.filter((task) => !needsYou(task) && isUnseen(task, seen));
  const running = sorted.filter((task) => WORKING.has(task.status));
  const shown = new Set([...waiting, ...unseen, ...running]);
  const recent = sorted.filter((task) => !shown.has(task)).slice(0, RECENT);
  return { waiting, unseen, running, recent };
}

/** The rail's one dot: yellow wins over blue, nothing when all is quiet. */
export function railSignal(tasks, seen) {
  if (tasks.some(needsYou)) return "asks";
  return tasks.some((task) => isUnseen(task, seen)) ? "news" : "";
}

/** The signal a single row shows. */
export function rowSignal(task, seen) {
  if (needsYou(task)) return "asks";
  if (WORKING.has(task.status)) return "running";
  return isUnseen(task, seen) ? "news" : "";
}

// -- storage ------------------------------------------------------------------------------------------------------

export function loadSeen(storage = globalThis.localStorage, now = Date.now()) {
  let saved = null;
  try {
    saved = JSON.parse(storage?.getItem(SEEN_KEY) ?? "null");
  } catch {
    // Not JSON (or storage blocked): start a new baseline below.
    saved = null;
  }
  if (Number.isFinite(saved?.baseline) && saved.tasks) return saved;
  const fresh = { baseline: typeof now === "number" ? now : Date.parse(now), tasks: {} };
  saveSeen(fresh, storage);
  return fresh;
}

export function saveSeen(seen, storage = globalThis.localStorage) {
  try {
    storage?.setItem(SEEN_KEY, JSON.stringify(seen));
  } catch {
    // Storage blocked: the dots just come back after a reload.
  }
}

/** The person has looked at this task as it is now. True when that changed anything. */
export function markSeen(seen, task, storage = globalThis.localStorage) {
  const stamp = stampOf(task);
  const id = String(task.id);
  if (!stamp || (seen.tasks[id] ?? 0) >= stamp) return false;
  seen.tasks[id] = stamp;
  const ids = Object.keys(seen.tasks);
  if (ids.length > SEEN_LIMIT) {
    ids.sort((a, b) => (seen.tasks[a] < seen.tasks[b] ? -1 : 1)).slice(0, ids.length - SEEN_LIMIT).forEach((old) => delete seen.tasks[old]);
  }
  saveSeen(seen, storage);
  return true;
}

// -- time -----------------------------------------------------------------------------------------------------------

/** "5 min ago", "yesterday", "12 Nov" in the person's language. */
export function relativeTime(iso, language, now = Date.now()) {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  const minutes = Math.max(0, Math.round((now - then) / 60000));
  const ru = language === "ru";
  if (minutes < 1) return ru ? "только что" : "just now";
  if (minutes < 60) return ru ? `${minutes} мин назад` : `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return ru ? `${hours} ч назад` : `${hours} h ago`;
  if (hours < 48) return ru ? "вчера" : "yesterday";
  return new Date(iso).toLocaleDateString(ru ? "ru-RU" : "en-GB", { day: "numeric", month: "short" });
}

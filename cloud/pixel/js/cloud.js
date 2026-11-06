// What Kvist Cloud knows about the signed-in person: the server (machine), the hours, the model key and
// the task list. The rest of the page asks this module; only this module and bridge.js talk to the server.
// Pure helpers sit at the top (no DOM, no fetch) so node --test covers them.

import { lang, localized } from "./i18n.js";

export const FINISHED = new Set(["done", "cancelled", "failed"]);
export const WORKING = new Set(["queued", "starting", "running"]);
export const LOGIN_URL = "/auth/github/login?next=/pixel/";

// -- pure helpers -------------------------------------------------------------------------

export const wholeMinutes = (seconds) => Math.floor(Math.max(0, Number(seconds) || 0) / 60);

/** "4 h 48 min", from seconds; minutes are rounded down, as billed (cloud/docs/ARCHITECTURE.md). */
export function duration(seconds, language = lang.current) {
  const minutes = wholeMinutes(seconds);
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  const [h, m] = language === "ru" ? ["ч", "мин"] : ["h", "min"];
  if (hours === 0) return `${rest} ${m}`;
  return rest === 0 ? `${hours} ${h}` : `${hours} ${h} ${rest} ${m}`;
}

export function share(balance, total) {
  if (!total) return 0;
  return Math.max(0, Math.min(1, Math.max(0, balance) / total));
}

export const firstLine = (text) => String(text ?? "").trim().split("\n")[0].slice(0, 120);

/** "owner/repo" out of a repository link; empty for the empty folder. */
export function repoLabel(url) {
  if (!url) return "";
  const parts = String(url).replace(/\.git$/, "").split("/").filter(Boolean);
  return parts.slice(-2).join("/");
}

/** A task as the rail lists it. The id is the session name, so it stays stable across reloads. */
export function listItem(task) {
  return {
    name: String(task.id),
    title: firstLine(task.goal),
    updated_at: task.finished_at ?? task.started_at ?? task.created_at ?? "",
    closed: FINISHED.has(task.status),
  };
}

/** The tasks that belong to one repository ("" is the empty folder), newest first. */
export const tasksOf = (tasks, workspace) => tasks.filter((task) => (task.repo_url ?? "") === workspace);

export const repositories = (tasks) => [...new Set(tasks.map((task) => task.repo_url ?? ""))];

/** A paused task is continued by a button; any message continues it too, with the message. */
export const canResume = (cloud) => cloud?.status === "paused";

/** The reason a message cannot be sent yet, from what the account looks like; null when it can. */
export function cannotRun(account) {
  if (!account.sub || account.sub.status === "none") return "no_subscription";
  if (account.sub.balance_seconds <= 0) return "no_hours";
  if (!account.key?.set) return "no_key";
  return null;
}

export const displayState = (machine) => machine?.display_status ?? "none";

// -- the account ----------------------------------------------------------------------------

export const account = { me: null, machine: null, sub: null, feed: [], key: { set: false }, providers: [], tasks: [], ready: false };

const listeners = new Set();
export const onAccount = (fn) => (listeners.add(fn), () => listeners.delete(fn));
const changed = () => listeners.forEach((fn) => fn());

export class ApiError extends Error {
  constructor(status, code, message) {
    super(message || code);
    this.status = status;
    this.code = code;
    this.localized = localized(message || code);
  }
}

export async function api(path, { method = "GET", body } = {}) {
  let response;
  try {
    response = await fetch(path, {
      method,
      credentials: "same-origin",
      headers: { "X-Kvist-Client": "1", ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "network", "No connection. / Нет связи.");
  }
  if (response.status === 401) {
    location.assign(LOGIN_URL);
    throw new ApiError(401, "signed_out", "Signed out. / Вход не выполнен.");
  }
  if (response.status === 204) return null;
  let data = null;
  try {
    data = await response.json();
  } catch {
    if (!response.ok) throw new ApiError(response.status, "internal");
    return null;
  }
  if (!response.ok) {
    const error = data?.error ?? {};
    throw new ApiError(response.status, error.code ?? "fallback", error.message ?? "");
  }
  return data;
}

async function quietly(path) {
  try {
    return await api(path);
  } catch (error) {
    if (error.status === 401) throw error;
    console.warn(path, error.code);
    return undefined;
  }
}

export async function refreshMachine() {
  const machines = await quietly("/api/machines");
  if (machines !== undefined) account.machine = machines[0] ?? null;
  changed();
}

export async function refreshHours() {
  const ledger = await quietly("/api/ledger");
  if (ledger) {
    account.sub = ledger.subscription;
    account.feed = ledger.feed;
  }
  changed();
}

export async function refreshTasks() {
  const tasks = await quietly("/api/tasks");
  if (tasks) account.tasks = tasks;
  changed();
  return account.tasks;
}

export async function refreshKey() {
  const key = await quietly("/api/model-key");
  if (key) account.key = key;
  changed();
}

export async function loadAccount() {
  account.me = await api("/api/me");
  const providers = await quietly("/api/model-providers");
  if (providers) account.providers = providers;
  await Promise.all([refreshMachine(), refreshHours(), refreshTasks(), refreshKey()]);
  account.ready = true;
  changed();
}

const SETTLED = new Set(["ready", "working", "waiting_for_you", "error", "asleep"]);

/** Keeps the server and the hours fresh: quickly while the machine is being prepared or a task runs, calmly otherwise. */
export function keepFresh() {
  let timer = null;
  const tick = async () => {
    await Promise.all([refreshMachine(), refreshHours()]);
    const working = account.tasks.some((task) => WORKING.has(task.status) || task.status === "waiting_for_user");
    timer = setTimeout(tick, SETTLED.has(account.machine?.display_status) && !working ? 15000 : 3000);
  };
  timer = setTimeout(tick, 2000);
  return () => clearTimeout(timer);
}

/** Wake, sleep and archive export all go to the server; it does them on the machine in the background. */
export async function machineAction(action) {
  const machine = account.machine;
  if (!machine) return;
  await api(`/api/machines/${machine.id}/${action}`, { method: "POST" });
  await refreshMachine();
}

export async function signOut() {
  await api("/auth/logout", { method: "POST" }).catch(() => {});
  location.assign("/");
}

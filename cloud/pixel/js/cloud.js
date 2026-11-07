// What Kvist Cloud knows about the signed-in person: the server (machine), the hours, the model key and
// the task list. The rest of the page asks this module; only this module and bridge.js talk to the server.
// Pure helpers sit at the top (no DOM, no fetch) so node --test covers them.

import { lang, localized } from "./i18n.js";

export const FINISHED = new Set(["done", "cancelled", "failed"]);
export const WORKING = new Set(["queued", "starting", "running"]);
/** Sign in, then come back to exactly this address (a session's link works after signing in). */
export const loginUrl = () => `/auth/github/login?next=${encodeURIComponent(location.pathname)}`;

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

/** "owner/repo" out of a repository link. */
export function repoLabel(url) {
  if (!url) return "";
  const parts = String(url).replace(/\.git$/, "").split("/").filter(Boolean);
  return parts.slice(-2).join("/");
}

/** A task as the rail lists it. The id is the session name, so it stays stable across reloads. */
export function listItem(task) {
  return {
    name: String(task.id),
    public_id: task.public_id,
    title: firstLine(task.goal),
    updated_at: task.finished_at ?? task.started_at ?? task.created_at ?? "",
    closed: FINISHED.has(task.status),
  };
}

// -- addresses -------------------------------------------------------------------------------------------------
// A session is /pixel/s/<its own id>, a draft in a project /pixel/p/<project id>. The id in the address is an
// opaque one (not the row number) and opens nothing for anyone but the owner: the page asks the API, which
// answers "not found" to everyone else.

export const sessionUrl = (publicId) => `/pixel/s/${publicId}`;
export const projectUrl = (projectId) => `/pixel/p/${projectId}`;

/** What an address asks for: {kind: "session", id} | {kind: "project", id} | {kind: "task", id} (an old #/tasks/N link) | null. */
export function parseLocation(pathname, hash = "") {
  const session = pathname.match(/^\/pixel\/s\/([A-Za-z0-9_-]{6,40})\/?$/);
  if (session) return { kind: "session", id: session[1] };
  const project = pathname.match(/^\/pixel\/p\/(\d+)\/?$/);
  if (project) return { kind: "project", id: project[1] };
  const old = hash.match(/^#\/tasks\/(\d+)$/);
  return old ? { kind: "task", id: old[1] } : null;
}

// A "workspace" on this page is a project's id as text; "" holds the tasks from before projects existed.
export const workspaceOf = (task) => String(task.project_id ?? "");

/** The tasks of one project, newest first. */
export const tasksOf = (tasks, workspace) => tasks.filter((task) => workspaceOf(task) === workspace);

/** What a project is called: its name, or the old tasks' shared label. */
export function projectName(workspace, projects, fallback) {
  return projects.find((project) => String(project.id) === workspace)?.name ?? fallback;
}

// -- project names: the same rules the server applies, so the dialog can say no before asking ----------------

const NAME = /^[\p{L}\p{N}_][\p{L}\p{N}_ .\-]*$/u;
const RESERVED = /^task-\d+$/i;
export const NAME_LIMIT = 60;

/** The folder a name becomes on the server: spaces turn into hyphens. */
export const folderOf = (name) => String(name).trim().replace(/\s+/g, "-");

/** Why this cannot be a project name ("" when it can). Case and spacing do not make a new project. */
export function nameProblem(name, projects) {
  const folder = folderOf(name);
  if (!folder || folder.length > NAME_LIMIT || !NAME.test(String(name).trim()) || /[. ]$/.test(String(name).trim()) || RESERVED.test(folder)) return "bad_project_name";
  if (projects.some((project) => folderOf(project.name).toLowerCase() === folder.toLowerCase())) return "name_taken";
  return "";
}

/** "owner/repo" or "repo" from a clone link, to suggest as a project name. */
export const suggestedName = (repoName) => String(repoName).split("/").at(-1).replace(/\.git$/, "");

/** A paused task is continued by a button; any message continues it too, with the message. */
export const canResume = (cloud) => cloud?.status === "paused";

/** The reason a message cannot be sent yet, from what the account looks like; null when it can. */
export function cannotRun(account) {
  if (!account.sub || account.sub.status === "none") return "no_subscription";
  if (account.sub.balance_seconds <= 0) return "no_hours";
  if (!account.key?.set) return account.key?.provider === "chatgpt" ? "chatgpt_login" : "no_key";
  return null;
}

export const displayState = (machine) => machine?.display_status ?? "none";

/** What a run of Kvist says about opening or switching a session is plumbing, not news; it must not reach the feed. */
export const isPlumbing = (event, announced) =>
  (event.type === "hello" && announced) || (event.type === "command_result" && ["session", "new"].includes(event.name));

// -- warnings ---------------------------------------------------------------------------------------------------

const LOW_SECONDS = 30 * 60;
const LOW_SHARE = 0.1;
const MACHINE_REASONS = ["create_failed", "wake_failed", "wake_timeout", "sleep_failed", "delete_failed"];
const PAUSE_REASONS = ["hours_exhausted", "provider_unavailable", "invalid_key", "command_time_limit", "machine_restarted", "machine_sleeping"];

/**
 * What the person should be told before it bites: nothing to run on, hours nearly gone, the server in trouble, a
 * task that stopped and why. `extra` carries what the page itself found (a session that is not theirs, no project).
 * Each warning is {id, level: "warn"|"error", text, action?: "key"|"plan"|"project"|"start"} with `text` an i18n key.
 */
export function warnings(state, agent = null, extra = []) {
  const found = [...extra];
  const { sub, key, machine } = state;
  if (state.ready && !state.projects?.length) found.push({ id: "no-projects", level: "warn", text: "warn no projects", action: "project" });
  if (!sub || sub.status === "none") found.push({ id: "no-plan", level: "warn", text: "warn no plan", action: "start" });
  else if (sub.balance_seconds <= 0) found.push({ id: "no-hours", level: "error", text: "warn no hours", action: "plan" });
  else if (sub.balance_seconds < LOW_SECONDS || share(sub.balance_seconds, sub.plan_seconds) < LOW_SHARE) {
    found.push({ id: "low-hours", level: "warn", text: "warn low hours", action: "plan", value: duration(sub.balance_seconds) });
  }
  if (!key?.set) found.push({ id: "no-key", level: "warn", text: "warn no key", action: "key" });
  if (machine?.display_status === "error") {
    // A person cannot act on "wake_failed", so each known reason says what happened and what to try; the code
    // stays at the end of the sentence for whoever they write to.
    const detail = machine.detail ?? "";
    const known = MACHINE_REASONS.includes(detail);
    found.push({ id: "machine-error", level: "error", text: known ? `warn machine ${detail}` : "warn machine error",
      value: detail, action: detail === "create_failed" ? undefined : "machine" });
  }
  const cloud = agent?.cloud;
  if (cloud?.status === "paused" && PAUSE_REASONS.includes(cloud.reason)) {
    found.push({ id: `paused-${cloud.reason}`, level: "warn", text: "warn paused", reason: `pause: ${cloud.reason}`, action: cloud.reason === "invalid_key" ? "key" : cloud.reason === "hours_exhausted" ? "plan" : undefined });
  }
  if (cloud?.status === "failed") found.push({ id: "failed", level: "error", text: "warn failed", reason: cloud.reason ? `pause: ${cloud.reason}` : "" });
  return found;
}

// -- the account ----------------------------------------------------------------------------

export const account = { me: null, machine: null, sub: null, feed: [], key: { set: false }, providers: [], tasks: [], projects: [], ready: false };

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
    location.assign(loginUrl());
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

/** "Try again" on a machine in trouble: ask the server to start it, then look at it. */
export async function retryMachine() {
  if (!account.machine) return;
  await api(`/api/machines/${account.machine.id}/wake`, { method: "POST" });
  await refreshMachine();
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

export async function refreshProjects() {
  const projects = await quietly("/api/projects");
  if (projects) account.projects = projects;
  changed();
  return account.projects;
}

/** A new project: a folder of that name on the server, empty or a clone of `repoUrl`. */
export async function createProject(name, repoUrl = "") {
  const project = await api("/api/projects", { method: "POST", body: { name, repo_url: repoUrl } });
  await refreshProjects();
  return project;
}

/** A session by the id in an address: the task, or null when it is not there or not yours (the cloud says the same either way). */
export async function resolveSession(publicId) {
  try {
    return await api(`/api/sessions/${encodeURIComponent(publicId)}`);
  } catch (error) {
    if (error.status === 404) return null;
    throw error;
  }
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
  await Promise.all([refreshMachine(), refreshHours(), refreshTasks(), refreshKey(), refreshProjects()]);
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

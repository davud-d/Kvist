// The one place that talks to Kvist Cloud on behalf of an "agent" (the page's word for a task).
// Views call the same functions they called in the desktop app; here they become the cloud's HTTP API and
// the task's WebSocket, and what comes back is the same stream of core events (docs/UI_API.md §7), because
// the cloud relays Kvist's events unchanged.
//
// A task has one goal and cannot be given a second one, so an agent in this page is a *draft* until its
// first goal is sent, and a task from then on. A draft needs no server at all.

import { account, api, ApiError, firstLine, isPlumbing, listItem, loginUrl, refreshHours, refreshTasks, tasksOf, WORKING } from "./cloud.js";
import { t } from "./i18n.js";

const listeners = new Set();
/** task id -> {agent, workspace, stop, lastSeq} */
const live = new Map();
/** agent id -> {workspace, taskId} */
const agents = new Map();
/** task id -> the last event seq the page already holds (from a history load), so a stream resumes after it */
const seen = new Map();

const emit = (agent, event) => listeners.forEach((fn) => fn({ agent, event }));

/** `fn({agent, event})` for every event of every task. */
export function onCore(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

const hello = (agent) => ({
  type: "hello",
  protocol_version: 1,
  provider: account.key?.provider ?? "",
  models: account.key?.model ? { fast: account.key.model, smart: account.key.model } : {},
  role: "",
  effort: "",
  session: String(agents.get(agent)?.taskId ?? ""),
});

// -- the task's stream ----------------------------------------------------------------------

function connect(agent, taskId) {
  const known = seen.get(taskId) ?? 0;
  const entry = { agent, lastSeq: known, socket: null, stopped: false, delay: 1000, announced: false };
  live.set(taskId, entry);
  const open = () => {
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    const socket = new WebSocket(`${scheme}://${location.host}/api/tasks/${taskId}/stream?after=${entry.lastSeq}`);
    entry.socket = socket;
    socket.onopen = () => {
      entry.delay = 1000;
      if (entry.announced) emit(agent, { type: "remote_state", state: "connected", host: "cloud" });
    };
    socket.onmessage = (message) => handle(agent, taskId, entry, JSON.parse(message.data));
    socket.onclose = (close) => {
      if (entry.stopped) return;
      if (close.code === 4401) {
        location.assign(loginUrl());
        return;
      }
      emit(agent, { type: "remote_state", state: "reconnecting", host: "cloud", reason: t("connection lost") });
      setTimeout(open, entry.delay);
      entry.delay = Math.min(entry.delay * 2, 15000);
    };
  };
  open();
}

// The core names its own session inside; on this page a task is its id, so that is what stays the session.
const SESSION_EVENTS = new Set(["hello", "session"]);


function handle(agent, taskId, entry, data) {
  if (data.type === "event") {
    if (data.seq) {
      if (data.seq <= entry.lastSeq) return;
      entry.lastSeq = data.seq;
    }
    // The core's own hello is plumbing for the feed, but it is the only place that names the models it really uses.
    if (data.event.type === "hello" && entry.announced) {
      const { provider, models, role } = data.event;
      emit(agent, { type: "core_info", provider, models, role });
      return;
    }
    if (isPlumbing(data.event, entry.announced)) return;
    emit(agent, SESSION_EVENTS.has(data.event.type) ? { ...data.event, session: String(taskId) } : data.event);
  } else if (data.type === "task") {
    onTask(agent, entry, data.task);
  } else if (data.type === "usage") {
    // The meter moved: the hours left are now a little smaller.
    refreshHours();
  } else if (data.type === "resync") {
    entry.socket?.close();
  }
}

/** The task's status as an event of its own, so the same reducer shows it as it shows everything else. */
function onTask(agent, entry, task) {
  const known = account.tasks.findIndex((item) => item.id === task.id);
  if (known >= 0) account.tasks[known] = { ...account.tasks[known], ...task };
  if (!entry.announced) {
    entry.announced = true;
    emit(agent, hello(agent));
  }
  emit(agent, taskEvent(task));
  if (!WORKING.has(task.status)) refreshHours();
}

export function openAgent(id, workspace, session = null) {
  const taskId = session ? Number(session) : null;
  agents.set(id, { workspace, taskId });
  if (taskId === null) {
    // A draft: nothing to connect to until a goal is sent.
    setTimeout(() => emit(id, hello(id)), 0);
    return Promise.resolve();
  }
  connect(id, taskId);
  return Promise.resolve();
}

export function closeAgent(id) {
  const entry = agents.get(id);
  agents.delete(id);
  const stream = entry?.taskId ? live.get(entry.taskId) : null;
  if (!stream) return Promise.resolve();
  stream.stopped = true;
  stream.socket?.close();
  live.delete(entry.taskId);
  return Promise.resolve();
}

// -- what the person can do -----------------------------------------------------------------

function taskEvent(task) {
  return { type: "cloud_task", task: { id: task.id, public_id: task.public_id, goal: task.goal, repo_url: task.repo_url, status: task.status, reason: task.reason, used_seconds: task.used_seconds } };
}

/** Another message in the conversation: the cloud starts the agent again if it had stopped, or queues it if it is busy. */
async function sendMessage(id, taskId, text) {
  const task = await api(`/api/tasks/${taskId}/messages`, { method: "POST", body: { text } });
  emit(id, taskEvent(task));
  refreshHours();
}

async function startTask(id, text) {
  const entry = agents.get(id);
  // `workspace` is the project's id; "" is a task outside any project (only the older tasks are).
  const body = { goal: text, project_id: entry.workspace ? Number(entry.workspace) : null };
  const task = await api("/api/tasks", { method: "POST", body });
  entry.taskId = task.id;
  seen.set(task.id, 0);
  account.tasks.unshift(task);
  emit(id, { type: "session", session: String(task.id), title: firstLine(task.goal) });
  connect(id, task.id);
  refreshTasks();
  refreshHours();
}

/** Answers to the agent's questions go to the task; `request_id` marks them. */
function answerRequest(taskId, message) {
  const { request_id: requestId, approved, value } = message;
  const body = approved !== undefined ? { request_id: requestId, approved } : { request_id: requestId, value: value ?? null };
  return api(`/api/tasks/${taskId}/input`, { method: "POST", body });
}

export async function sendToAgent(id, message) {
  const entry = agents.get(id);
  if (!entry) return;
  if (message.type === "goal") return entry.taskId ? sendMessage(id, entry.taskId, message.text) : startTask(id, message.text);
  if (!entry.taskId) return undefined;
  if (message.request_id) return answerRequest(entry.taskId, message);
  if (message.type === "pause") return api(`/api/tasks/${entry.taskId}/pause`, { method: "POST" });
  if (message.type === "cancel") return api(`/api/tasks/${entry.taskId}/cancel`, { method: "POST" });
  if (message.type === "command" && message.name === "resume") return api(`/api/tasks/${entry.taskId}/resume`, { method: "POST" });
  return undefined;
}

// -- the list and the history (what the desktop app asked its core for) ----------------------------

const HISTORY_PAGES = 40;

async function history(taskId) {
  const task = await api(`/api/tasks/${taskId}`);
  const events = [];
  let after = 0;
  for (let page = 0; page < HISTORY_PAGES; page += 1) {
    const reply = await api(`/api/tasks/${taskId}/events?after=${after}`);
    for (const row of reply.events) {
      if (isPlumbing(row.event, true)) {
        after = row.seq;
        continue;
      }
      events.push({ seq: row.seq, event: SESSION_EVENTS.has(row.event.type) ? { ...row.event, session: String(taskId) } : row.event });
      after = row.seq;
    }
    if (!reply.more) break;
  }
  seen.set(taskId, after);
  // Tasks from before the conversation was kept in the history have no first message in it.
  if (events[0]?.event.type !== "user") events.unshift({ event: { type: "user", text: task.goal } });
  return { ok: true, has_more: false, events };
}

/** `op` is one of the desktop app's session operations; only the ones the cloud has an answer for are here. */
export async function sessionOp(workspace, op, { sessionName = null } = {}) {
  if (op === "list" || op === "status") {
    await refreshTasks();
    const items = tasksOf(account.tasks, workspace).map(listItem);
    return { ok: true, sessions: items.map((item) => item.name), session_items: items };
  }
  if (op === "history" && sessionName) return history(Number(sessionName));
  if (op === "input-history") return { ok: true, entries: [...new Set(account.tasks.map((task) => firstLine(task.goal)))].reverse().slice(-50) };
  if (op === "input-history-add") return { ok: true };
  throw new ApiError(0, "unsupported", `Not available in the cloud: ${op}. / В облаке недоступно: ${op}.`);
}

export function openExternal(url) {
  window.open(url, "_blank", "noopener");
}

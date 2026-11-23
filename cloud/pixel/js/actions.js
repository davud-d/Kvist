// What the person can make an agent (a cloud task) do. Views call these; they talk to the cloud through the
// bridge and feed what comes back into the store.

import * as bridge from "./bridge.js";
import { account, api, cannotRun, projectUrl, refreshKey, refreshTasks, sessionUrl, titleOf, workspaceOf } from "./cloud.js";
import { t } from "./i18n.js";
import { addNote, beginTurn, clearFeed, createAgent, replayHistory } from "./reducer.js";
import { askToNotify } from "./notify.js";
import { addAgent, announce, dispatch, removeAgent, setActive, setMode, setPref, store } from "./store.js";
import { toast } from "./views/toast.js";

const ready = new Map();
let openKeyDialog = () => {};
let openProjectDialog = () => {};

/** The dialogs live in views; this is how an action asks for one without importing the view. */
export function onNeedKey(open) {
  openKeyDialog = open;
}

export function onNeedProject(open) {
  openProjectDialog = open;
}

function newId() {
  return `a${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
}

const TASK_CHANGES = new Set(["cloud_task", "session", "final", "turn_end", "input_request", "task_state"]);
// A burst of events (a turn ending) asks the server once.
const REFRESH_DELAY_MS = 800;
let refreshTimer = null;

function refreshTasksSoon() {
  if (refreshTimer) return;
  refreshTimer = setTimeout(() => {
    refreshTimer = null;
    refreshTasks();
  }, REFRESH_DELAY_MS);
}

export function startListening() {
  bridge.onCore(({ agent: id, event }) => {
    dispatch(id, event);
    if (event.type === "hello") ready.get(id)?.();
    const agent = store.agents.get(id);
    // The task list is what the home screen and the dots read; these events change a task's status there.
    if (TASK_CHANGES.has(event.type)) refreshTasksSoon();
    // The first message gives the draft its session id: the address becomes the session's own.
    // Only while the task is on screen: the person may already be back home, and that address must stay.
    if (agent && event.type === "cloud_task" && id === store.activeId && store.mode === "chat") syncAddress(agent);
  });
}

function fail(agent, error) {
  agent.state = "gone";
  addNote(agent, String(error), "error");
  announce({ kind: "agent", id: agent.id });
}

/** The tasks a repository holds, as the cloud lists them; the rail shows them under it. */
export async function refreshSessions(workspace) {
  try {
    const reply = await bridge.sessionOp(workspace, "list");
    store.listings.set(workspace, reply.session_items ?? []);
  } catch (error) {
    console.warn("task list", error);
    store.listings.set(workspace, []);
  }
  announce({ kind: "structure" });
}

/** A new project becomes where the next task goes, and the home screen is where it is written. */
export function addProject(workspace) {
  setPref("project", workspace);
  goHome();
}

/** A stored task: the agent that already shows it, or one made for it (its stream opens on first use). */
export async function openSession(workspace, name, title, publicId = "") {
  const open = [...store.agents.values()].find((agent) => agent.workspace === workspace && agent.session === name);
  if (open) return activate(open.id);
  const agent = restoreTab({ workspace, session: name, title, publicId });
  return activate(agent.id);
}

/** A draft in a project: nothing exists on the server until its first message is sent. */
export function newAgent(workspace) {
  const agent = createAgent(newId(), workspace);
  addAgent(agent);
  setActive(agent.id);
  startCore(agent, null);
  return agent;
}

function startCore(agent, session) {
  agent.state = "starting";
  const opened = new Promise((resolve) => ready.set(agent.id, resolve));
  bridge.openAgent(agent.id, agent.workspace, session).catch((error) => fail(agent, error));
  announce({ kind: "agent", id: agent.id });
  return opened;
}

/** A restored task has no stream until the person looks at it: its history first, then the live stream after it. */
export async function wake(agent) {
  if (agent.state !== "asleep") return;
  agent.state = "starting";
  await loadHistory(agent);
  await startCore(agent, agent.session || null);
}

export function restoreTab({ workspace, session, title, publicId = "" }) {
  const agent = createAgent(newId(), workspace, session);
  agent.state = "asleep";
  agent.title = title ?? "";
  agent.publicId = publicId;
  addAgent(agent);
  return agent;
}

export async function loadHistory(agent) {
  if (!agent.session) return;
  try {
    const reply = await bridge.sessionOp(agent.workspace, "history", { sessionName: agent.session });
    clearFeed(agent);
    replayHistory(agent, reply.events ?? []);
  } catch (error) {
    addNote(agent, error.localized ?? String(error), "error");
  }
  announce({ kind: "agent", id: agent.id });
}

/** The address that shows this agent: its session's own, or the project it is a draft in. */
export const addressOf = (agent) => (agent.publicId ? sessionUrl(agent.publicId) : agent.workspace ? projectUrl(agent.workspace) : "/workspace/");

/** The address bar follows what is in front, so a link to it works anywhere; back and forward move between them. */
export function syncAddress(agent, { push = false } = {}) {
  if (!agent) return;
  const address = addressOf(agent);
  if (location.pathname === address && !location.hash) return;
  history[push ? "pushState" : "replaceState"](null, "", address);
}

/** A task comes into focus: the whole stage is its. */
export async function activate(id) {
  setActive(id);
  setMode("chat");
}

/** A task from the cloud's list (home screen, task search): its agent, made on first use. */
export async function openTask(task) {
  const workspace = workspaceOf(task);
  refreshSessions(workspace);
  await openSession(workspace, String(task.id), titleOf(task), task.public_id);
  announce({ kind: "agent", id: store.activeId });
}

/** Back to the home screen; the open tasks keep running and keep their place. */
export function goHome() {
  setMode("home");
  history.pushState(null, "", store.prefs.project ? projectUrl(store.prefs.project) : "/workspace/");
}

/**
 * A new task from the home screen: a draft in the chosen project gets the words and comes into focus. False
 * (and the words stay where they were typed) when the account cannot run it yet or there is no project.
 */
export async function startTask(workspace, text) {
  if (!workspace) {
    explain("no_project");
    return false;
  }
  askToNotify();
  const problem = cannotRun(account);
  if (problem) {
    explain(problem);
    return false;
  }
  const draft = [...store.agents.values()].find((agent) => agent.workspace === workspace && !agent.session && !agent.items.length);
  const agent = draft ?? newAgent(workspace);
  await activate(agent.id);
  const sent = await sendGoal(agent, text);
  if (sent) refreshTasks();
  return sent;
}

export async function closeTab(id) {
  const agent = store.agents.get(id);
  if (!agent) return;
  removeAgent(id);
  if (agent.state !== "asleep") await bridge.closeAgent(id).catch(() => {});
}

// What the account lacks, said in the person's words, with the way to fix it.
function explain(problem) {
  toast(t(problem), "error");
  if (problem === "no_project") openProjectDialog();
  else if (problem === "no_key" || problem === "chatgpt_login") openKeyDialog();
  else location.assign(problem === "no_subscription" ? "/start" : "/dashboard");
}

// Commands of Kvist's that the page runs itself: the cloud owns what they change (the model key and the model, the
// effort it starts Kvist with, the task's name), so its Kvist is started without them (backend commands.py).
// `task`: needs a task to act on, so the home field does not offer it.
const PAGE_RUNS = {
  provider: { task: false, run: () => openModelPicker() },
  models: { task: false, run: () => openModelPicker() },
  "change-api": { task: false, run: () => openKeyDialog() },
  effort: { task: false, run: (_agent, args) => effortCommand(args[0]?.toLowerCase()) },
  rename: { task: true, run: (agent, args) => (args.length ? renameTask(agent, args.join(" ")) : toast(t("rename usage"), "error")) },
  new: { task: true, run: () => goHome() },
};
export const PAGE_COMMANDS = new Set(Object.keys(PAGE_RUNS));
/** The page's own commands that make sense with no task (the home field). */
export const pageCommandWithoutTask = (name) => PAGE_RUNS[name]?.task === false;

// The model picker is in the field the command was typed into, the home one or a task's.
function openModelPicker() {
  document.dispatchEvent(new CustomEvent("kvist:model-picker"));
}

const parseCommand = (text) => {
  const found = text.trim().match(/^\/([a-z][a-z0-9-]*)(?:\s+([\s\S]*))?$/i);
  return found ? { name: found[1].toLowerCase(), args: (found[2] ?? "").split(/\s+/).filter(Boolean) } : null;
};

/**
 * A command the page runs itself (`/effort high`, `/provider`), where it is typed. True when `text` was one;
 * `agent` is the task it was typed in, or null in the home field.
 */
export async function pageCommand(text, agent = null) {
  const command = parseCommand(text);
  const page = command && PAGE_RUNS[command.name];
  if (!page || (page.task && !agent)) return false;
  await page.run(agent, command.args);
  return true;
}

/** True when `name` is a command Kvist carries out for this task (its manifest, less what the cloud takes away). */
function kvistCommand(agent, name) {
  const manifest = agent.manifest ?? account.commands.manifest;
  const entry = manifest?.slash_commands?.find((command) => command.name === name);
  return Boolean(entry && entry.via === "core" && !account.commands.hidden.includes(name));
}

/**
 * The first message starts the task; every later one goes into the same conversation (the cloud starts the agent
 * again if it had stopped, or queues the message if it is busy). The person's words reach the feed from the
 * task's own history, the way they do after a reload. True when the message was sent.
 */
export async function sendGoal(agent, text) {
  const line = text.trim();
  if (!line || agent.state === "gone") return false;
  // Still inside the person's click or key: the only moment a browser lets a page ask.
  if (!line.startsWith("/")) askToNotify();
  if (await pageCommand(line, agent)) return true;
  const command = parseCommand(line);
  if (command && kvistCommand(agent, command.name)) return runKvistCommand(agent, command);
  // A task lives in a project (a folder on the server); a draft with none cannot start.
  const problem = !agent.session && !agent.workspace ? "no_project" : cannotRun(account);
  if (problem) {
    explain(problem);
    return false;
  }
  try {
    await bridge.sendToAgent(agent.id, { type: "goal", text: line });
  } catch (error) {
    toast(error.localized ?? String(error), "error");
    if (error.code === "no_key" || error.code === "chatgpt_login") openKeyDialog();
    else if (error.code === "no_hours") location.assign("/dashboard");
    else if (error.code === "no_subscription") location.assign("/start");
    return false;
  }
  // The cloud's own "task started" news can mark the task running first; the turn's clock starts here all the same.
  if (!agent.turn.startedAt) beginTurn(agent);
  announce({ kind: "agent", id: agent.id });
  return true;
}

// Kvist's scale of how hard the model thinks; the server keeps the person's choice and starts every core with it.
export const EFFORT_LEVELS = ["false", "low", "medium", "high", "xhigh", "max"];
export const effortNow = () => account.key?.effort || "medium";

/** `quiet` is for the dial: it already shows the level, so only a failure is worth a toast. */
export async function setEffort(level, { quiet = false } = {}) {
  try {
    await api("/api/effort", { method: "PUT", body: { level } });
    await refreshKey();
    if (!quiet) toast(`${t("effort")}: ${t(`effort ${level}`)} — ${t("effort from next message")}`);
  } catch (error) {
    toast(error.localized ?? String(error), "error");
  }
}

/** One of Kvist's commands, run in the task's conversation; its dialogs come as the task's questions. */
async function runKvistCommand(agent, command) {
  if (!agent.session && !agent.cloud) {
    toast(t("command needs task"), "error");
    return false;
  }
  try {
    await bridge.sendToAgent(agent.id, { type: "command", name: command.name, args: command.args });
  } catch (error) {
    toast(error.localized ?? String(error), "error");
    return false;
  }
  if (!agent.turn.startedAt) beginTurn(agent);
  announce({ kind: "agent", id: agent.id });
  return true;
}

/** `/effort` alone says where it stands; `/effort high` sets it. */
async function effortCommand(level) {
  if (!level) {
    toast(`${t("effort")}: ${effortNow()} · ${EFFORT_LEVELS.join(" / ")}`);
    return;
  }
  if (!EFFORT_LEVELS.includes(level)) {
    toast(`${t("effort")}: ${EFFORT_LEVELS.join(" / ")}`, "error");
    return;
  }
  await setEffort(level);
}

/** Gives the task a name of the person's own; an empty name goes back to the first line of the goal. */
export async function renameTask(agent, title) {
  const task = account.tasks.find((item) => String(item.id) === agent.session);
  if (!task) {
    toast(t("rename needs task"), "error");
    return;
  }
  try {
    const renamed = await api(`/api/tasks/${task.id}`, { method: "PATCH", body: { title } });
    Object.assign(task, renamed);
    dispatch(agent.id, { type: "session_title", session: agent.session, title: titleOf(renamed) });
    await refreshTasks();
  } catch (error) {
    toast(error.localized ?? String(error), "error");
  }
}

/** The only commands the cloud has: another task, and continuing a paused one. */
export async function runCommand(agent, name) {
  if (name === "new") {
    if (agent.workspace) setPref("project", agent.workspace);
    return goHome();
  }
  if (name === "resume" && agent.cloud?.status === "paused") {
    return bridge.sendToAgent(agent.id, { type: "command", name: "resume" }).catch((error) => toast(error.localized ?? String(error), "error"));
  }
  return undefined;
}

export function pause(agent) {
  bridge.sendToAgent(agent.id, { type: "pause" }).catch((error) => toast(error.localized ?? String(error), "error"));
}

export function stopAll(agent) {
  bridge.sendToAgent(agent.id, { type: "cancel" }).catch((error) => toast(error.localized ?? String(error), "error"));
}

export function answer(agent, request, reply) {
  agent.dialogs = agent.dialogs.filter((d) => d.request_id !== request.request_id);
  announce({ kind: "agent", id: agent.id });
  bridge.sendToAgent(agent.id, { request_id: request.request_id, ...reply }).catch((error) => toast(error.localized ?? String(error), "error"));
}

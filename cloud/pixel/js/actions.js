// What the person can make an agent (a cloud task) do. Views call these; they talk to the cloud through the
// bridge and feed what comes back into the store.

import * as bridge from "./bridge.js";
import { account, cannotRun } from "./cloud.js";
import { t } from "./i18n.js";
import { addNote, clearFeed, createAgent, replayHistory, addUser, beginTurn } from "./reducer.js";
import { addAgent, announce, dispatch, removeAgent, setActive, setPref, store } from "./store.js";
import { toast } from "./views/toast.js";

const ready = new Map();
let openKeyDialog = () => {};

/** The settings dialogs live in views; this is how an action asks for one without importing the view. */
export function onNeedKey(open) {
  openKeyDialog = open;
}

function newId() {
  return `a${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
}

export function startListening() {
  bridge.onCore(({ agent: id, event }) => {
    dispatch(id, event);
    if (event.type === "hello") ready.get(id)?.();
    const agent = store.agents.get(id);
    if (agent && ["session", "cloud_task"].includes(event.type) && store.prefs.expanded.includes(agent.workspace)) refreshSessions(agent.workspace);
  });
}

function fail(agent, error) {
  agent.state = "gone";
  addNote(agent, String(error), "error");
  announce({ kind: "agent", id: agent.id });
}

function rememberProject(workspace) {
  if (!store.prefs.projects.includes(workspace)) setPref("projects", [...store.prefs.projects, workspace]);
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

export function toggleProject(workspace) {
  const open = store.prefs.expanded.includes(workspace);
  setPref("expanded", open ? store.prefs.expanded.filter((w) => w !== workspace) : [...store.prefs.expanded, workspace]);
  if (!open) refreshSessions(workspace);
}

/** A repository joins the rail, opens, and gets a draft task to write in. */
export function addProject(workspace) {
  rememberProject(workspace);
  if (!store.prefs.expanded.includes(workspace)) setPref("expanded", [...store.prefs.expanded, workspace]);
  return newAgent(workspace);
}

/** A stored task: the agent that already shows it, or one made for it (its stream opens on first use). */
export async function openSession(workspace, name, title) {
  const open = [...store.agents.values()].find((agent) => agent.workspace === workspace && agent.session === name);
  if (open) return activate(open.id);
  const agent = restoreTab({ workspace, session: name, title });
  return activate(agent.id);
}

/** A draft in `workspace` (a repository link, or "" for an empty folder): nothing exists on the server yet. */
export function newAgent(workspace) {
  rememberProject(workspace);
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

export function restoreTab({ workspace, session, title }) {
  rememberProject(workspace);
  const agent = createAgent(newId(), workspace, session);
  agent.state = "asleep";
  agent.title = title ?? "";
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

export async function activate(id) {
  setActive(id);
  const agent = store.agents.get(id);
  if (agent) history.replaceState(null, "", agent.session ? `#/tasks/${agent.session}` : location.pathname);
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
  if (problem === "no_key") openKeyDialog();
  else location.assign(problem === "no_subscription" ? "/start" : "/dashboard");
}

/** A goal starts the task. The same words go nowhere else: a task has one goal. True when it was sent. */
export async function sendGoal(agent, text) {
  const line = text.trim();
  // One goal per task: once it is sent (or while it is being sent) the words go nowhere.
  if (!line || agent.cloud || agent.session || agent.state === "running" || agent.state === "gone") return false;
  const problem = cannotRun(account);
  if (problem) {
    explain(problem);
    return false;
  }
  addUser(agent, line, []);
  beginTurn(agent);
  announce({ kind: "agent", id: agent.id });
  try {
    await bridge.sendToAgent(agent.id, { type: "goal", text: line });
  } catch (error) {
    undoGoal(agent);
    toast(error.localized ?? String(error), "error");
    if (error.code === "no_key") openKeyDialog();
    else if (error.code === "no_hours") location.assign("/dashboard");
    else if (error.code === "no_subscription") location.assign("/start");
    return false;
  }
  return true;
}

// The server refused the goal: the draft goes back to how it was, with the words still in the box.
function undoGoal(agent) {
  const last = agent.items.at(-1);
  if (last?.kind === "user") agent.items.pop();
  agent.state = "idle";
  agent.turn = { startedAt: 0, waitingSince: 0, outputAtStart: 0 };
  agent.rev++;
  announce({ kind: "agent", id: agent.id });
}

/** The only commands the cloud has: another task, and continuing a paused one. */
export async function runCommand(agent, name) {
  if (name === "new") return newAgent(agent.workspace);
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

// What the person can make an agent (a cloud task) do. Views call these; they talk to the cloud through the
// bridge and feed what comes back into the store.

import * as bridge from "./bridge.js";
import { account, cannotRun, projectUrl, sessionUrl } from "./cloud.js";
import { t } from "./i18n.js";
import { addNote, beginTurn, clearFeed, createAgent, replayHistory } from "./reducer.js";
import { addAgent, announce, dispatch, removeAgent, setActive, setPref, store } from "./store.js";
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

export function startListening() {
  bridge.onCore(({ agent: id, event }) => {
    dispatch(id, event);
    if (event.type === "hello") ready.get(id)?.();
    const agent = store.agents.get(id);
    if (agent && ["session", "cloud_task"].includes(event.type) && store.prefs.expanded.includes(agent.workspace)) refreshSessions(agent.workspace);
    // The first message gives the draft its session id: the address becomes the session's own.
    if (agent && event.type === "cloud_task" && id === store.activeId) syncAddress(agent);
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

export function toggleProject(workspace) {
  const open = store.prefs.expanded.includes(workspace);
  setPref("expanded", open ? store.prefs.expanded.filter((w) => w !== workspace) : [...store.prefs.expanded, workspace]);
  if (!open) refreshSessions(workspace);
}

/** A project (by id) opens on the rail and gets a draft task to write in. */
export function addProject(workspace) {
  if (!store.prefs.expanded.includes(workspace)) setPref("expanded", [...store.prefs.expanded, workspace]);
  return newAgent(workspace);
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
export const addressOf = (agent) => (agent.publicId ? sessionUrl(agent.publicId) : agent.workspace ? projectUrl(agent.workspace) : "/pixel/");

/** The address bar follows what is in front, so a link to it works anywhere; back and forward move between them. */
export function syncAddress(agent, { push = false } = {}) {
  if (!agent) return;
  const address = addressOf(agent);
  if (location.pathname === address && !location.hash) return;
  history[push ? "pushState" : "replaceState"](null, "", address);
}

export async function activate(id) {
  setActive(id);
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
  else if (problem === "no_key") openKeyDialog();
  else location.assign(problem === "no_subscription" ? "/start" : "/dashboard");
}

/**
 * The first message starts the task; every later one goes into the same conversation (the cloud starts the agent
 * again if it had stopped, or queues the message if it is busy). The person's words reach the feed from the
 * task's own history, the way they do after a reload. True when the message was sent.
 */
export async function sendGoal(agent, text) {
  const line = text.trim();
  if (!line || agent.state === "gone") return false;
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
    if (error.code === "no_key") openKeyDialog();
    else if (error.code === "no_hours") location.assign("/dashboard");
    else if (error.code === "no_subscription") location.assign("/start");
    return false;
  }
  if (agent.state !== "running") beginTurn(agent);
  announce({ kind: "agent", id: agent.id });
  return true;
}

/** The only commands the cloud has: another task, and continuing a paused one. */
export async function runCommand(agent, name) {
  if (name === "new") return newAgent(agent.workspace || String(account.projects[0]?.id ?? ""));
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

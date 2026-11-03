// What the person can make an agent do. Views call these; they talk to the
// core through the bridge and feed what comes back into the store.

import * as bridge from "./bridge.js";
import { localized, t } from "./i18n.js";
import { addNote, addUser, beginTurn, clearFeed, createAgent, replayHistory } from "./reducer.js";
import { addAgent, announce, dispatch, removeAgent, saveTabs, setActive, setPref, store } from "./store.js";
import { imageGoal } from "./views/attachments.js";
import { toast } from "./views/toast.js";

const HISTORY_EVENTS = 300;
const ready = new Map();

function newId() {
  return `a${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
}

export function startListening() {
  bridge.onCore(({ agent: id, event }) => {
    dispatch(id, event);
    if (event.type === "hello") ready.get(id)?.();
    if (event.type === "command_result") reportCommand(id, event);
    if (event.type === "session_title" || event.type === "session" || event.type === "hello") saveTabs();
    const agent = store.agents.get(id);
    const listsChanged = ["session_title", "turn_end", "session"].includes(event.type) || (event.type === "command_result" && event.effects?.session);
    if (agent && listsChanged && store.prefs.expanded.includes(agent.workspace)) refreshSessions(agent.workspace);
    if (agent?.needsHistory) {
      agent.needsHistory = false;
      loadHistory(agent);
    }
  });
}

function reportCommand(id, event) {
  if (id !== store.activeId || !event.message) return;
  toast(localized(event.message), event.ok ? "ok" : "error");
}

function fail(agent, error) {
  agent.state = "gone";
  addNote(agent, String(error), "error");
  announce({ kind: "agent", id: agent.id });
}

function rememberProject(workspace) {
  if (!store.prefs.projects.includes(workspace)) setPref("projects", [...store.prefs.projects, workspace]);
}

/** The sessions a folder holds, as the core lists them; the rail shows them under the folder. */
export async function refreshSessions(workspace) {
  try {
    const reply = await bridge.sessionOp(workspace, "list");
    const items = reply.session_items ?? (reply.sessions ?? []).map((name) => ({ name, title: name }));
    store.listings.set(workspace, items);
  } catch {
    store.listings.set(workspace, []);
  }
  announce({ kind: "structure" });
}

export function toggleProject(workspace) {
  const open = store.prefs.expanded.includes(workspace);
  setPref("expanded", open ? store.prefs.expanded.filter((w) => w !== workspace) : [...store.prefs.expanded, workspace]);
  if (!open) refreshSessions(workspace);
}

/** A folder joins the rail, opens, and gets a session to start in. */
export function addProject(workspace) {
  rememberProject(workspace);
  if (!store.prefs.expanded.includes(workspace)) setPref("expanded", [...store.prefs.expanded, workspace]);
  return newAgent(workspace);
}

/** Picking a stored session: the agent that already has it, or one made for it (its core starts on first use). */
export async function openSession(workspace, name, title) {
  const open = [...store.agents.values()].find((agent) => agent.workspace === workspace && agent.session === name);
  if (open) return activate(open.id);
  return activate(restoreTab({ workspace, session: name, title }).id);
}

/** Opens a new agent in `workspace`, in a fresh session of its own. */
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

/** A restored tab has no core until the person uses it. */
export async function wake(agent) {
  if (agent.state !== "asleep") return;
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
    const reply = await bridge.sessionOp(agent.workspace, "history", { sessionName: agent.session, historyLimit: HISTORY_EVENTS });
    clearFeed(agent);
    replayHistory(agent, reply.events ?? []);
    if (reply.closed) agent.closed = reply.closed_notice ?? "";
  } catch (error) {
    addNote(agent, String(error), "error");
  }
  announce({ kind: "agent", id: agent.id });
}

export async function activate(id) {
  const agent = store.agents.get(id);
  // Decided before the switch: showing a sleeping agent wakes it at once, and an awake one is no longer "asleep".
  const needsHistory = agent && agent.items.length === 0 && agent.session && agent.state === "asleep";
  setActive(id);
  if (needsHistory) await loadHistory(agent);
  saveTabs();
}

export async function closeTab(id) {
  const agent = store.agents.get(id);
  if (!agent) return;
  removeAgent(id);
  saveTabs();
  if (agent.state !== "asleep") await bridge.closeAgent(id).catch(() => {});
}

export async function sendGoal(agent, text, images = []) {
  const line = text.trim();
  if ((!line && !images.length) || agent.closed !== null || agent.state === "gone") return;
  if (agent.state === "asleep") await wake(agent);
  const slash = line.match(/^\/([\w-]+)(?:\s+(.*))?$/);
  const known = !images.length && slash && agent.manifest?.slash_commands?.some((c) => c.name === slash[1] || c.aliases?.includes(slash[1]));
  if (known && slash[1] !== "website") {
    runCommand(agent, slash[1], slash[2] ? slash[2].split(/\s+/) : []);
    return;
  }
  addUser(agent, line, images);
  beginTurn(agent);
  announce({ kind: "agent", id: agent.id });
  bridge.sendToAgent(agent.id, { type: "goal", text: imageGoal(line, images) }).catch((error) => fail(agent, error));
  bridge.sessionOp(agent.workspace, "input-history-add", { stdin: line }).catch(() => {});
}

export async function runCommand(agent, name, args = []) {
  if (agent.state === "asleep") await wake(agent);
  if (agent.state === "running") {
    toast(t("available between turns"), "error");
    return;
  }
  bridge.sendToAgent(agent.id, { type: "command", name, args }).catch((error) => fail(agent, error));
}

export function pause(agent) {
  bridge.sendToAgent(agent.id, { type: "pause" }).catch(() => {});
}

/** A core on a server lets one frontend write; this asks to be that one. */
export function takeOver(agent) {
  bridge.sendToAgent(agent.id, { type: "takeover" }).catch(() => {});
}

export function stopAll(agent) {
  bridge.sendToAgent(agent.id, { type: "cancel" }).catch(() => {});
}

export function stopHelper(agent, helperId) {
  bridge.sendToAgent(agent.id, { type: "helper_stop", agent_id: helperId }).catch(() => {});
}

export function answer(agent, request, reply) {
  agent.dialogs = agent.dialogs.filter((d) => d.request_id !== request.request_id);
  announce({ kind: "agent", id: agent.id });
  bridge.sendToAgent(agent.id, { request_id: request.request_id, ...reply }).catch(() => {});
}


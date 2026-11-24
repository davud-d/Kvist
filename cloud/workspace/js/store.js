// What the window knows: its agents, which one is in front, how the window is
// laid out, and the person's interface preferences. Views subscribe and
// redraw what changed; nothing here touches the DOM.

import { loadSeen } from "./attention.js";
import { reduce } from "./reducer.js";

export const DEFAULT_PREFS = {
  theme: "system",
  glass: 0.85,
  speed: 1, // 1 = animations on, 0 = off
  scale: 1,
  project: "", // the project a new task from the home screen goes to (its id)
};

export const store = {
  agents: new Map(),
  listings: new Map(), // workspace -> the sessions the core lists for it
  order: [],
  activeId: null,
  mode: "home", // home (start work, see what changed) | chat (one task in focus)
  sheet: null, // the contextual side sheet: null | {kind, title}
  seen: null, // attention.js: which finished tasks the person has looked at
  prefs: { ...DEFAULT_PREFS },
};

const subscribers = new Set();

/** `fn(change)` where change is `{kind: "agent"|"structure"|"active"|"mode"|"prefs", id?}`. */
export function subscribe(fn) {
  subscribers.add(fn);
  return () => subscribers.delete(fn);
}

export function announce(change) {
  subscribers.forEach((fn) => fn(change));
}

export const active = () => store.agents.get(store.activeId) ?? null;

export function addAgent(agent) {
  store.agents.set(agent.id, agent);
  store.order.push(agent.id);
  announce({ kind: "structure" });
}

export function removeAgent(id) {
  store.agents.delete(id);
  store.order = store.order.filter((other) => other !== id);
  if (store.activeId === id) store.activeId = store.order.at(-1) ?? null;
  announce({ kind: "structure" });
  announce({ kind: "active" });
}

export function setActive(id) {
  if (store.activeId === id || !store.agents.has(id)) return;
  store.activeId = id;
  const agent = store.agents.get(id);
  agent.attention = false;
  announce({ kind: "active" });
}

export function setMode(mode) {
  if (store.mode === mode) return;
  store.mode = mode;
  announce({ kind: "mode" });
}

export function setPref(key, value) {
  store.prefs[key] = value;
  savePrefs();
  announce({ kind: "prefs", key });
}

/** Folds a core event into its agent and tells the views. */
export function dispatch(id, event, options) {
  const agent = store.agents.get(id);
  if (!agent) return;
  reduce(agent, event, options);
  if (id !== store.activeId && ["final", "input_request", "error"].includes(event.type)) agent.attention = true;
  announce({ kind: "agent", id });
}

const PREFS_KEY = "kvist.cloud.prefs";

function read(key) {
  try {
    return JSON.parse(localStorage.getItem(key) ?? "null");
  } catch {
    return null;
  }
}

function write(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Storage can be blocked; the window just forgets its layout then.
  }
}

export function loadPrefs() {
  store.prefs = { ...DEFAULT_PREFS, ...(read(PREFS_KEY) ?? {}) };
  store.seen = loadSeen();
}

/** The side sheet shows one thing at a time; the same kind again closes it. */
export function setSheet(sheet) {
  store.sheet = sheet && store.sheet?.kind === sheet.kind ? null : sheet;
  announce({ kind: "sheet" });
}

export function savePrefs() {
  write(PREFS_KEY, store.prefs);
}


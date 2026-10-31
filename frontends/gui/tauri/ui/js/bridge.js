// The one place that knows whether the page runs inside Tauri. Everything
// else calls these functions. Outside Tauri (a plain browser, the UI tests)
// a scripted stand-in core answers, so the interface can be seen and tried
// without the agent.

import { createMock } from "./mock-core.js";

const tauri = globalThis.__TAURI__;
export const native = Boolean(tauri?.core);

const listeners = new Set();
const mock = native ? null : createMock((agent, event) => listeners.forEach((fn) => fn({ agent, event })));

if (native) {
  tauri.event.listen("core", (message) => listeners.forEach((fn) => fn(message.payload)));
}

/** `fn({agent, event})` for every event of every core. */
export function onCore(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

const call = (name, args) => (native ? tauri.core.invoke(name, args) : mock.invoke(name, args));

export const openAgent = (id, workspace, session = null) => call("agent_open", { id, workspace, session });
export const sendToAgent = (id, message) => call("agent_send", { id, message });
export const closeAgent = (id) => call("agent_close", { id });
export const launchDir = () => call("launch_dir", {});
export const windowMode = () => call("window_mode", {});
export const setTransparent = (value) => call("set_transparent", { value });
export const restartApp = () => call("restart_app", {});

export function sessionOp(workspace, op, { sessionName = null, historyLimit = null, historyBefore = null, stdin = null } = {}) {
  return call("session_op", { workspace, op, sessionName, historyLimit, historyBefore, stdin });
}

export async function pickFolder(defaultPath) {
  if (!native) return mock.pickFolder();
  const chosen = await tauri.dialog.open({ directory: true, multiple: false, defaultPath });
  return typeof chosen === "string" ? chosen : null;
}

export async function openExternal(url) {
  if (native) return tauri.opener.openUrl(url);
  window.open(url, "_blank", "noopener");
  return undefined;
}

const frame = () => (native ? tauri.window.getCurrentWindow() : null);

export const windowControls = {
  minimize: () => frame()?.minimize(),
  toggleMaximize: () => frame()?.toggleMaximize(),
  close: () => (native ? frame().close() : undefined),
  startDragging: () => frame()?.startDragging(),
  startResizing: (direction) => frame()?.startResizeDragging(direction),
};

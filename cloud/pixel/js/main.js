// Kvist Cloud, pixel edition: wires the modules together, loads the account and opens the first task.

import { activate, newAgent, onNeedKey, onNeedProject, openSession, refreshSessions, startListening, syncAddress } from "./actions.js";
import { openExternal } from "./bridge.js";
import { account, keepFresh, loadAccount, parseLocation, resolveSession, workspaceOf } from "./cloud.js";
import { $ } from "./dom.js";
import { detectLanguage, setLanguage, t } from "./i18n.js";
import { setMathRenderer } from "./markdown.js";
import { applyPrefs } from "./theme.js";
import { active, loadPrefs, setMode, setPref, store, subscribe } from "./store.js";
import { mountChat } from "./views/chat.js";
import { mountDock } from "./views/dock.js";
import { mountInspector } from "./views/inspector.js";
import { mountNotices, setNotices } from "./views/notices.js";
import { mountOverview } from "./views/overview.js";
import { openProjectDialog } from "./views/project-dialog.js";
import { openKeyDialog } from "./views/settings-dialogs.js";
import { toast } from "./views/toast.js";
import { mountTitlebar } from "./views/titlebar.js";

function mountMath() {
  const katex = globalThis.katex;
  if (!katex) return;
  setMathRenderer((tex, display) => katex.renderToString(tex, { displayMode: display, throwOnError: false, output: "html", trust: false }));
}

function wireGlobalKeys() {
  document.addEventListener("keydown", (event) => {
    if (event.ctrlKey && event.key === "\\") setMode(store.mode === "overview" ? "chat" : "overview");
    else if (event.ctrlKey && event.key === "Tab") {
      const at = store.order.indexOf(store.activeId);
      const next = store.order[(at + (event.shiftKey ? -1 : 1) + store.order.length) % store.order.length];
      if (next) activate(next);
    } else if (event.altKey && event.key === "n") {
      newAgent(active()?.workspace || String(account.projects[0]?.id ?? ""));
    } else return;
    event.preventDefault();
  });
  document.addEventListener("click", (event) => {
    const link = event.target.closest?.("a[data-external]");
    if (!link) return;
    event.preventDefault();
    openExternal(link.href);
  });
}

// What the address asks for is what opens: /pixel/s/<id> is that session, /pixel/p/<id> a draft in that project, and a
// bare /pixel/ the project used last. A session that is not the person's (or does not exist) is said so, and the page
// falls back to a draft; the cloud answers a stranger exactly as it answers a missing one.
async function openByAddress() {
  const wanted = parseLocation(location.pathname, location.hash);
  setNotices([]);
  if (wanted?.kind === "session" || wanted?.kind === "task") {
    const task = wanted.kind === "session"
      ? account.tasks.find((item) => item.public_id === wanted.id) ?? await resolveSession(wanted.id)
      : account.tasks.find((item) => String(item.id) === wanted.id);
    if (task) return showTask(task);
    setNotices([{ id: "session-missing", level: "error", text: "warn session missing" }]);
  }
  const project = wanted?.kind === "project" && account.projects.some((item) => String(item.id) === wanted.id) ? wanted.id : null;
  if (wanted?.kind === "project" && !project) setNotices([{ id: "project-missing", level: "error", text: "warn project missing" }]);
  const workspace = project ?? lastWorkspace();
  if (!workspace) return syncAddress(null);
  expand(workspace);
  const draft = [...store.agents.values()].find((agent) => agent.workspace === workspace && !agent.session);
  if (draft) return activate(draft.id);
  newAgent(workspace);
  return undefined;
}

const lastWorkspace = () => String(workspaceOf(account.tasks[0] ?? {}) || account.projects[0]?.id || "");

function expand(workspace) {
  if (!store.prefs.expanded.includes(workspace)) setPref("expanded", [...store.prefs.expanded, workspace]);
}

async function showTask(task) {
  const workspace = workspaceOf(task);
  expand(workspace);
  refreshSessions(workspace);
  await openSession(workspace, String(task.id), task.goal.trim().split("\n")[0], task.public_id);
}

async function boot() {
  loadPrefs();
  setLanguage(detectLanguage());
  applyPrefs(store.prefs);
  mountMath();
  const app = $("#app");
  const pane = $("#pane");
  mountTitlebar($("#titlebar"));
  mountNotices($("#notices"));
  mountDock($("#dock"));
  mountChat(pane);
  mountInspector($("#inspector"));
  mountOverview($("#overview"), pane);
  wireGlobalKeys();
  startListening();
  onNeedKey(openKeyDialog);
  onNeedProject(openProjectDialog);
  // The address bar follows what is in front, and back/forward move between sessions.
  let settled = false;
  subscribe((change) => {
    // The first open replaces the address the page was loaded with; after that, each change is a step in history.
    if (change.kind !== "active") return;
    syncAddress(active(), { push: settled });
    // A warning about the address that was asked for is about that moment: moving on to something else ends it.
    if (settled) setNotices([]);
  });
  window.addEventListener("popstate", () => openByAddress().catch((error) => console.error(error)));
  subscribe((change) => {
    if (change.kind !== "prefs") return;
    applyPrefs(store.prefs);
    app.classList.toggle("no-inspector", !store.prefs.inspector);
  });
  app.classList.toggle("no-inspector", !store.prefs.inspector);
  // A signed-out visitor is sent to sign in from inside loadAccount.
  await loadAccount();
  sayHowSignInWent();
  keepFresh();
  await openByAddress();
  settled = true;
}

/** Back from OpenAI the address says how it went (`?chatgpt=ok` or a reason): said once, then removed. */
function sayHowSignInWent() {
  const url = new URL(location.href);
  const result = url.searchParams.get("chatgpt");
  if (!result) return;
  url.searchParams.delete("chatgpt");
  history.replaceState(history.state, "", url);
  toast(t(`chatgpt ${result}`), result === "ok" ? "ok" : "error");
  if (result !== "ok") openKeyDialog();
}

boot().catch((error) => console.error(error));

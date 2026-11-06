// Kvist Cloud, pixel edition: wires the modules together, loads the account and opens the first task.

import { activate, newAgent, onNeedKey, onNeedProject, openSession, refreshSessions, startListening } from "./actions.js";
import { openExternal } from "./bridge.js";
import { account, keepFresh, loadAccount, workspaceOf } from "./cloud.js";
import { $ } from "./dom.js";
import { detectLanguage, setLanguage } from "./i18n.js";
import { setMathRenderer } from "./markdown.js";
import { applyPrefs } from "./theme.js";
import { active, loadPrefs, setMode, setPref, store, subscribe } from "./store.js";
import { mountChat } from "./views/chat.js";
import { mountDock } from "./views/dock.js";
import { mountInspector } from "./views/inspector.js";
import { mountOverview } from "./views/overview.js";
import { openProjectDialog } from "./views/project-dialog.js";
import { openKeyDialog } from "./views/settings-dialogs.js";
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

// The rail lists the person's projects. A link like /pixel/#/tasks/12 opens that task; otherwise the page opens a
// draft in the project used last. With no project at all the first thing to do is add one, so that is asked.
async function openFirst() {
  const wanted = location.hash.match(/^#\/tasks\/(\d+)$/)?.[1];
  const task = account.tasks.find((item) => String(item.id) === wanted);
  const workspace = task ? workspaceOf(task) : String(workspaceOf(account.tasks[0] ?? {}) || account.projects[0]?.id || "");
  if (!workspace && !task) {
    openProjectDialog();
    return;
  }
  if (!store.prefs.expanded.includes(workspace)) setPref("expanded", [...store.prefs.expanded, workspace]);
  if (task) {
    refreshSessions(workspace);
    await openSession(workspace, String(task.id), task.goal.trim().split("\n")[0]);
    return;
  }
  newAgent(workspace);
}

async function boot() {
  loadPrefs();
  setLanguage(detectLanguage());
  applyPrefs(store.prefs);
  mountMath();
  const app = $("#app");
  const pane = $("#pane");
  mountTitlebar($("#titlebar"));
  mountDock($("#dock"));
  mountChat(pane);
  mountInspector($("#inspector"));
  mountOverview($("#overview"), pane);
  wireGlobalKeys();
  startListening();
  onNeedKey(openKeyDialog);
  onNeedProject(openProjectDialog);
  subscribe((change) => {
    if (change.kind !== "prefs") return;
    applyPrefs(store.prefs);
    app.classList.toggle("no-inspector", !store.prefs.inspector);
  });
  app.classList.toggle("no-inspector", !store.prefs.inspector);
  // A signed-out visitor is sent to sign in from inside loadAccount.
  await loadAccount();
  keepFresh();
  await openFirst();
}

boot().catch((error) => console.error(error));

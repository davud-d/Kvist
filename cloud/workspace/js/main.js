// Kvist Cloud's workspace: wires the modules together, loads the account and opens what the address asks for.
// Two screens share the stage: home (start work, see what changed) and one task in focus; the rail is the only
// thing always there, and details open in the side sheet on request.

import { activate, goHome, onNeedKey, onNeedProject, openTask, startListening, syncAddress } from "./actions.js";
import { markSeen } from "./attention.js";
import { openExternal } from "./bridge.js";
import { account, keepFresh, loadAccount, onAccount, parseLocation, resolveSession } from "./cloud.js";
import { $ } from "./dom.js";
import { detectLanguage, setLanguage, t } from "./i18n.js";
import { setMathRenderer } from "./markdown.js";
import { watchTasks } from "./notify.js";
import { applyPrefs } from "./theme.js";
import { active, announce, loadPrefs, setMode, setPref, store, subscribe } from "./store.js";
import { mountChat } from "./views/chat.js";
import { mountHome } from "./views/home.js";
import { setNotices } from "./views/notices.js";
import { openPalette } from "./views/palette.js";
import { openProjectDialog } from "./views/project-dialog.js";
import { mountRail } from "./views/rail.js";
import { openKeyDialog } from "./views/settings-dialogs.js";
import { mountSheet } from "./views/sheet.js";
import { toast } from "./views/toast.js";

function mountMath() {
  const katex = globalThis.katex;
  if (!katex) return;
  setMathRenderer((tex, display) => katex.renderToString(tex, { displayMode: display, throwOnError: false, output: "html", trust: false }));
}

function wireGlobalKeys(home, chat) {
  document.addEventListener("keydown", (event) => {
    const mod = event.ctrlKey || event.metaKey;
    if (mod && event.key.toLowerCase() === "k") openPalette();
    else if (mod && event.key === "\\") (store.mode === "home" && active() ? activate(active().id) : goHome());
    else if (event.ctrlKey && event.key === "Tab") {
      const at = store.order.indexOf(store.activeId);
      const next = store.order[(at + (event.shiftKey ? -1 : 1) + store.order.length) % store.order.length];
      if (next) activate(next);
    } else if (event.altKey && event.key === "n") {
      goHome();
      home.focus();
    } else if (event.shiftKey && event.key === "Escape" && !document.querySelector("#modals > *")) {
      // Back to typing without the mouse, from anywhere on the page: the home field or the task's own.
      if (store.mode === "chat") chat.focusInput();
      else home.focus();
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

// What the address asks for is what opens: /workspace/s/<id> is that session, /workspace/p/<id> the home screen with that
// project chosen for the next task, and a bare /workspace/ the home screen. A session that is not the person's (or does
// not exist) is said so; the cloud answers a stranger exactly as it answers a missing one.
async function openByAddress() {
  const wanted = parseLocation(location.pathname, location.hash);
  setNotices([]);
  if (wanted?.kind === "session" || wanted?.kind === "task") {
    const task = wanted.kind === "session"
      ? account.tasks.find((item) => item.public_id === wanted.id) ?? await resolveSession(wanted.id)
      : account.tasks.find((item) => String(item.id) === wanted.id);
    if (task) return openTask(task);
    setNotices([{ id: "session-missing", level: "error", text: "warn session missing" }]);
  }
  if (wanted?.kind === "project") {
    if (account.projects.some((item) => String(item.id) === wanted.id)) setPref("project", wanted.id);
    else setNotices([{ id: "project-missing", level: "error", text: "warn project missing" }]);
  }
  setMode("home");
  return undefined;
}

// Looking at a task makes what it finished with no longer news (the blue dot goes out).
function noteSeen() {
  const agent = active();
  if (store.mode !== "chat" || !agent?.session) return;
  const task = account.tasks.find((item) => String(item.id) === agent.session);
  if (task && markSeen(store.seen, task)) announce({ kind: "seen" });
}

async function boot() {
  loadPrefs();
  setLanguage(detectLanguage());
  applyPrefs(store.prefs);
  mountMath();
  mountRail($("#rail"));
  const home = mountHome($("#home"));
  const chat = mountChat($("#pane"));
  mountSheet($("#sheet"));
  wireGlobalKeys(home, chat);
  startListening();
  onNeedKey(openKeyDialog);
  onNeedProject(openProjectDialog);
  // The address bar follows the task in focus, and back/forward move between tasks and home.
  let settled = false;
  subscribe((change) => {
    if (change.kind === "prefs") applyPrefs(store.prefs);
    if (change.kind === "active" || change.kind === "mode") {
      if (store.mode === "chat") syncAddress(active(), { push: settled });
      // A warning about the address that was asked for is about that moment: moving on ends it.
      if (settled) setNotices([]);
    }
    if (change.kind !== "seen") noteSeen();
  });
  onAccount(noteSeen);
  window.addEventListener("popstate", () => openByAddress().catch((error) => console.error(error)));
  // A signed-out visitor is sent to sign in from inside loadAccount.
  await loadAccount();
  sayHowSignInWent();
  keepFresh();
  watchTasks(openTask);
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

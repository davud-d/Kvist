// Kvist, pixel edition: wires the modules together and opens the first agent.

import { newAgent, startListening } from "./actions.js";
import { launchDir, openExternal, windowMode } from "./bridge.js";
import { $ } from "./dom.js";
import { detectLanguage, setLanguage } from "./i18n.js";
import { setMathRenderer } from "./markdown.js";
import { applyPrefs, windowState } from "./theme.js";
import { loadPrefs, loadTabs, setMode, setPref, store, subscribe } from "./store.js";
import { mountChat } from "./views/chat.js";
import { mountDock } from "./views/dock.js";
import { mountInspector } from "./views/inspector.js";
import { mountOverview } from "./views/overview.js";
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
    } else if (event.ctrlKey && event.key === "t") {
      launchDir().then(newAgent);
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

// A launch opens a session of its own, in the folder the window was last in; the earlier sessions of
// every project wait in the rail (the core keeps them, so none is lost by closing the window).
async function restoreOrStart() {
  const saved = loadTabs();
  const last = saved.tabs[saved.active] ?? saved.tabs.at(-1);
  const workspace = last?.workspace ?? (await launchDir());
  if (!store.prefs.expanded.includes(workspace)) setPref("expanded", [...store.prefs.expanded, workspace]);
  newAgent(workspace);
}

async function boot() {
  loadPrefs();
  Object.assign(windowState, await windowMode().catch(() => ({})));
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
  subscribe((change) => {
    if (change.kind !== "prefs") return;
    applyPrefs(store.prefs);
    app.classList.toggle("no-inspector", !store.prefs.inspector);
  });
  app.classList.toggle("no-inspector", !store.prefs.inspector);
  restoreOrStart().catch((error) => console.error(error));
}

boot().catch((error) => console.error(error));

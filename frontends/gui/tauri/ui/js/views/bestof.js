// Where the best-of arena, the executor log and the council stand: a layer over the conversation's feed, shown while the
// front agent has one of them. It also puts the way calls travel and the money spent in the conversation's header.
// The decision dialog is not here: it is the agent's open question (decision.js, drawn by dialogs.js).

import { barOf, clearScreens, onScreen } from "../bestof.js";
import { LOG_EVENTS, openExecutorLog } from "../bestof-log.js";
import { w } from "../bestof-words.js";
import { sessionOp } from "../bridge.js";
import { fill, frameThrottle, h } from "../dom.js";
import { active, announce, store, subscribe } from "../store.js";
import { isRussian, mountArena } from "./arena.js";
import { mountCouncil } from "./council.js";
import { mountExecLog } from "./execlog.js";
import { toast } from "./toast.js";

const TOOL_WORDS = { native: "toolsNative", text: "toolsText" };

function changed(agent) {
  announce({ kind: "agent", id: agent.id });
}

function openLog(agent, executor) {
  if (!executor?.session) {
    toast(w("logNoSession"), "error");
    return;
  }
  const fetchHistory = (session) => sessionOp(agent.workspace, "history", { sessionName: session, historyLimit: LOG_EVENTS });
  openExecutorLog(agent, executor, fetchHistory, () => changed(agent));
}

function closeLog(agent) {
  agent.bestof.log = null;
  changed(agent);
}

function dismiss(agent) {
  clearScreens(agent);
  changed(agent);
}

/**
 * `root` is the conversation pane (chat.js); the layer goes over its feed. The way calls travel and the money spent are
 * in the layer's own bar, so an ordinary conversation, header included, shows nothing new.
 */
export function mountBestof(root) {
  const bar = h("div", { class: "bo-bar", hidden: true });
  const arenaEl = h("div", { class: "bo-screen bo-arena" });
  const logEl = h("div", { class: "bo-screen bo-log" });
  const councilEl = h("div", { class: "bo-screen bo-council" });
  const layer = h("div", { class: "bo-layer", hidden: true, tabindex: "-1" }, bar, arenaEl, logEl, councilEl);
  root.querySelector(".feed-wrap").append(layer);

  const paintArena = mountArena(arenaEl, { onOpen: (executor) => openLog(active(), executor), onClose: () => dismiss(active()) });
  const paintLog = mountExecLog(logEl, {
    onBack: () => closeLog(active()),
    onRefresh: () => {
      const agent = active();
      const log = agent.bestof.log;
      openLog(agent, agent.bestof.arena?.executors.find((executor) => executor.id === log.id));
    },
  });
  const paintCouncil = mountCouncil(councilEl, { onClose: () => dismiss(active()) });

  function paintBar(agent) {
    const { mode, money } = barOf(agent, isRussian());
    const words = mode && TOOL_WORDS[mode.mode] ? w(TOOL_WORDS[mode.mode]) : null;
    bar.hidden = !words && !money;
    fill(bar, words ? h("span", { class: "chip bo-mode", title: mode.reason }, words) : null, money ? h("span", { class: "chip bo-money" }, money) : null);
  }

  const redraw = frameThrottle(() => {
    const agent = active();
    if (!agent) return;
    const { arena, council, log } = agent.bestof;
    layer.hidden = !onScreen(agent);
    if (onScreen(agent)) paintBar(agent);
    arenaEl.hidden = !arena || Boolean(log);
    logEl.hidden = !arena || !log;
    councilEl.hidden = !council || Boolean(arena);
    if (arena && !log) paintArena(arena);
    if (arena && log) paintLog(log);
    if (council && !arena) paintCouncil(council);
  });

  // While the layer is up, Esc steps back (log to arena, arena or council away) wherever the focus is, and never reaches
  // the conversation's own Esc, which pauses the turn: a run must not be paused by accident. It is taken in the capture
  // phase, ahead of that handler; a dialog, the settings and the overview keep their own Esc.
  document.addEventListener(
    "keydown",
    (event) => {
      const agent = active();
      if (event.key !== "Escape" || !agent || !onScreen(agent) || store.mode !== "chat" || agent.dialogs.length) return;
      if (event.target.closest?.("#modals, .dialog-layer")) return;
      event.stopPropagation();
      event.preventDefault();
      if (agent.bestof.log) closeLog(agent);
      else dismiss(agent);
    },
    true,
  );

  subscribe((change) => {
    if (change.kind === "agent" && change.id !== store.activeId) return;
    redraw();
  });
  redraw();
}

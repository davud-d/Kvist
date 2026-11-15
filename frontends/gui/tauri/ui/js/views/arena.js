// The best-of arena: one card per executor while a run goes on. It takes the conversation's place; a click on a
// card opens the executor's read-only log (execlog.js).

import { executorSpendLabel, isOut } from "../bestof.js";
import { w } from "../bestof-words.js";
import { fill, h, icon, keyedList } from "../dom.js";
import { lang } from "../i18n.js";

const STATE_WORDS = { waiting: "stateWaiting", running: "stateRunning", finished: "stateFinished", failed: "stateFailed", stopped: "stateStopped", eliminated: "stateEliminated", lost: "stateLost" };
const TEST_WORDS = { unknown: "testsUnknown", running: "testsRunning", passed: "testsPassed", failed: "testsFailed" };
const TEST_MARKS = { unknown: "·", running: "…", passed: "✓", failed: "✕" };
const FINISH_WORDS = { accepted: "finishAccepted", discarded: "finishDiscarded", cancelled: "finishCancelled", failed: "finishFailed", pending: "finishPending" };

export const isRussian = () => lang.current === "ru";

function callLine(last) {
  if (!last) return null;
  const mark = last.state === "run" ? h("span", { class: "spin" }) : h("span", { class: `mark ${last.state}` }, last.state === "ok" ? "✓" : "✕");
  return h("span", { class: `bo-call ${last.state}` }, mark, h("span", { class: "bo-call-label" }, last.label), last.summary ? h("span", { class: "dim" }, last.summary) : null);
}

function paintRow(el, executor) {
  const out = isOut(executor.state);
  el.classList.toggle("out", out);
  const tests = executor.tests;
  const money = executorSpendLabel(executor, isRussian());
  const size = executor.diff.files || executor.diff.added || executor.diff.removed ? `+${executor.diff.added} −${executor.diff.removed} · ${executor.diff.files} ${w("files")}` : "";
  fill(
    el,
    h(
      "div",
      { class: "bo-line" },
      h("span", { class: "bo-style" }, executor.style || executor.id),
      h("span", { class: `chip bo-state ${executor.state}` }, w(STATE_WORDS[executor.state])),
      executor.step ? h("span", { class: "dim" }, `${w("step")} ${executor.step}`) : null,
      h("span", { class: `bo-tests ${tests.state}` }, h("span", { class: "mark" }, TEST_MARKS[tests.state]), h("span", {}, tests.summary || w(TEST_WORDS[tests.state]))),
      size ? h("span", { class: "bo-size" }, size) : null,
      money ? h("span", { class: "bo-money" }, money) : null,
    ),
    h("div", { class: "bo-sub" }, executor.branch ? h("span", { class: "bo-branch" }, executor.branch) : null, callLine(executor.last)),
    out && executor.reason ? h("div", { class: "bo-reason" }, executor.reason) : null,
  );
}

function makeRow(executor, onOpen) {
  const el = h("button", { class: "bo-row px", type: "button" });
  el.addEventListener("click", () => onOpen(el.dataset.key));
  paintRow(el, executor);
  return el;
}

function closingLine(finished) {
  const word = FINISH_WORDS[finished.outcome];
  const head = word ? w(word) : finished.outcome;
  const detail = [finished.id, finished.commit, finished.message].filter(Boolean).join(" · ");
  return h("div", { class: `bo-finish ${finished.outcome}` }, h("b", {}, head), detail ? h("span", {}, detail) : null);
}

/** Mounts the arena into `el`; `paint(arena)` redraws it. */
export function mountArena(el, { onOpen, onClose }) {
  const close = h("button", { class: "btn flat", type: "button", title: w("close"), onclick: onClose }, icon("close", 12));
  const title = h("div", { class: "bo-title" });
  const blurb = h("div", { class: "bo-blurb" });
  const task = h("div", { class: "bo-task" });
  const rows = h("div", { class: "bo-rows" });
  const foot = h("div", { class: "bo-foot" });
  el.append(h("div", { class: "bo-head" }, h("div", { class: "bo-heading" }, title, blurb), close), task, rows, foot);
  let byId = new Map();
  const draw = keyedList(rows, {
    key: (executor) => executor.id,
    make: (executor) => makeRow(executor, (id) => onOpen(byId.get(id))),
    update: paintRow,
    // The row repaints when anything it shows moved, so a quiet run costs nothing.
    rev: (executor) => JSON.stringify(executor),
  });
  rows.addEventListener("keydown", (event) => {
    const step = event.key === "ArrowDown" ? 1 : event.key === "ArrowUp" ? -1 : 0;
    if (!step) return;
    const all = [...rows.children];
    all[all.indexOf(document.activeElement) + step]?.focus();
    event.preventDefault();
  });

  return (arena) => {
    byId = new Map(arena.executors.map((executor) => [executor.id, executor]));
    title.textContent = w("bestofTitle");
    blurb.textContent = w("bestofBlurb");
    close.title = w("close");
    fill(task, h("span", { class: "dim" }, `${w("task")}: `), h("span", {}, arena.task), arena.base ? h("span", { class: "dim" }, ` · ${w("base")} ${arena.base}`) : null);
    draw(arena.executors);
    fill(foot, arena.finished ? closingLine(arena.finished) : null);
  };
}

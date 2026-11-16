// The question a best-of run ends with (`input_request`, `purpose: "bestof_decision"`): the verdict table, the winner and
// why, and the actions the core offered. Closing the dialog answers `null`, which the core takes as "not deciding for now".

import { answer } from "../actions.js";
import { DECISION_CANCEL, decisionReply } from "../bestof.js";
import { w } from "../bestof-words.js";
import { fill, h } from "../dom.js";

const OUTCOME_WORDS = { winner: "outcomeWinner", finalist: "outcomeFinalist", eliminated: "outcomeEliminated", failed: "outcomeFailed" };
const TEST_WORDS = { unknown: "testsUnknown", running: "testsRunning", passed: "testsPassed", failed: "testsFailed" };
const ACTION_WORDS = { accept: "actionAccept", diff: "actionDiff", other: "actionOther", discard: "actionDiscard" };
const ACTION_LOOK = { accept: "primary", discard: "danger" };

/** Accept looks like the main button only while it is what the dialog opens on (a winner whose tests fail is not). */
const actionClass = (action, decision) => `btn ${action === "accept" && decision.first !== "accept" ? "" : (ACTION_LOOK[action] ?? "")}${action === decision.first ? " default" : ""}`;

const testsText = (tests) => tests.summary || w(TEST_WORDS[tests.state]);
const sizeText = (diff) => `+${diff.added} −${diff.removed} · ${diff.files} ${w("files")}`;

function table(arena, decision, repaint) {
  const { verdict } = arena;
  const head = h("tr", {}, ["colStyle", "colResult", "colTests", "colDiff", "colNote"].map((key) => h("th", {}, w(key))));
  const choose = (index) => {
    decision.row = index;
    repaint();
  };
  const rows = verdict.rows.map((row, index) =>
    h(
      "tr",
      {
        class: `vt-row${index === decision.row ? " on" : ""}${row.id === verdict.winner ? " win" : ""}`,
        tabindex: "0",
        onclick: () => choose(index),
        onkeydown: (event) => (event.key === "Enter" || event.key === " ") && (event.preventDefault(), choose(index)),
      },
      h("td", {}, row.style || row.id),
      h("td", {}, row.outcome ? w(OUTCOME_WORDS[row.outcome]) : ""),
      h("td", {}, testsText(row.tests)),
      h("td", {}, sizeText(row.diff)),
      h("td", { class: "vt-note" }, row.note),
    ),
  );
  const winner = verdict.rows.find((row) => verdict.winner && row.id === verdict.winner);
  const failing = winner?.tests.state === "failed";
  return [
    h("table", { class: "verdict-table" }, h("thead", {}, head), h("tbody", {}, rows)),
    h(
      "div",
      { class: "vt-winner" },
      h("b", {}, `${w("winner")}: `),
      winner ? winner.style || winner.id : w("noWinner"),
      failing ? h("span", { class: "dim" }, ` · ${w("winnerFailing")}`) : null,
      verdict.reason ? h("span", { class: "dim" }, ` — ${verdict.reason}`) : null,
    ),
  ];
}

/** A unified diff, file by file, in the look of the approval dialog's diff. */
function patchRows(patch) {
  const kind = (line) => {
    if (/^(diff |index |--- |\+\+\+ )/.test(line)) return "file";
    return line.startsWith("@@") ? "hunk" : line[0] === "+" ? "add" : line[0] === "-" ? "del" : "ctx";
  };
  return h("div", { class: "diff vt-patch" }, patch.split("\n").map((line) => h("div", { class: `diff-line ${kind(line)}` }, line)));
}

function patchPage(arena) {
  const { diff } = arena;
  const owner = arena.verdict.rows.find((row) => row.id === diff.id);
  return [
    h("div", { class: "dialog-path" }, `${w("patchOf")}: ${owner?.style || diff.id}`),
    diff.files.length ? h("div", { class: "dim" }, diff.files.join(", ")) : null,
    patchRows(diff.patch),
  ];
}

/** The dialog spec (see dialogs.js) for a decision request. */
export function decisionDialog(agent, request, decision) {
  const arena = agent.bestof.arena;
  const body = h("div", { class: "vt-body" });
  const toggle = h("button", { class: "btn flat", type: "button" });
  const buttons = h("div", { class: "dialog-buttons" });
  const reply = (action) => answer(agent, request, decisionReply(arena, decision, action));
  const cancel = () => answer(agent, request, DECISION_CANCEL);

  function repaint() {
    const patch = decision.page === "patch" && arena.diff;
    fill(body, ...(patch ? patchPage(arena) : table(arena, decision, repaint)));
    toggle.hidden = !arena.diff;
    toggle.textContent = patch ? w("showTable") : w("showPatch");
    fill(
      buttons,
      toggle,
      decision.actions.map((action) => h("button", { class: actionClass(action, decision), type: "button", onclick: () => reply(action) }, w(ACTION_WORDS[action]))),
    );
  }
  toggle.addEventListener("click", () => {
    decision.page = decision.page === "patch" ? "table" : "patch";
    repaint();
  });
  repaint();

  return {
    focus: ".btn.default",
    title: w("decisionTitle"),
    tone: "bestof",
    content: [body, buttons],
    cancel,
  };
}

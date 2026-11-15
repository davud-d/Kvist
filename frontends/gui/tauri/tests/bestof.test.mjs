// Run: node --test frontends/gui/tauri/tests
// The best-of arena, the decision answers, the council, money and tool mode, and the executor log (docs/UI_API.md §7).
import assert from "node:assert/strict";
import test from "node:test";

import { closeAgent } from "../ui/js/bridge.js";
import { runCommand } from "../ui/js/actions.js";
import {
  DECISION_CANCEL,
  addUsage,
  arenaSpend,
  barOf,
  decisionOf,
  decisionReply,
  isChosen,
  newSpend,
  onScreen,
  spendLabel,
  totalSpend,
  usageOf,
} from "../ui/js/bestof.js";
import { openExecutorLog } from "../ui/js/bestof-log.js";
import { ANGLE_IDS, EN, RU, angleBlurb, angleName } from "../ui/js/bestof-words.js";
import { beginTurn, createAgent, reduce, replayHistory } from "../ui/js/reducer.js";

const feed = (agent, ...events) => events.forEach((ev) => reduce(agent, ev));

const started = (run = "r1") => ({
  type: "bestof_started",
  run_id: run,
  task: "fix the parser",
  count: 3,
  base: "abc123",
  executors: [
    { executor_id: "e1", style: "senior-dev", branch: `kvist/bestof/${run}/e1`, session: "s-e1" },
    { executor_id: "e2", style: "minimalist", branch: `kvist/bestof/${run}/e2`, session: "s-e2" },
    { executor_id: "e3", style: "friendly", branch: `kvist/bestof/${run}/e3`, session: "s-e3" },
  ],
});

const status = (id, over = {}, run = "r1") => ({
  type: "bestof_status",
  run_id: run,
  executor_id: id,
  style: "",
  branch: "",
  state: "running",
  step: 1,
  tests: { state: "unknown", summary: "" },
  diff: { files: 0, added: 0, removed: 0 },
  usage: { input_tokens: 100, output_tokens: 10, cost_approx: false },
  ...over,
});

const verdict = (run = "r1") => ({
  type: "bestof_verdict",
  run_id: run,
  rows: [
    { executor_id: "e1", style: "senior-dev", outcome: "finalist", tests: { state: "passed", summary: "12 passed" }, diff: { files: 2, added: 30, removed: 4 }, note: "bigger" },
    { executor_id: "e2", style: "minimalist", outcome: "winner", tests: { state: "passed", summary: "12 passed" }, diff: { files: 1, added: 8, removed: 2 }, note: "smallest" },
    { executor_id: "e3", style: "friendly", outcome: "eliminated", tests: { state: "failed", summary: "3 failed" }, diff: { files: 1, added: 5, removed: 0 }, note: "tests fail" },
  ],
  winner: "e2",
  reason: "e2 passes everything with the smallest change",
});

const decisionRequest = (id = "q1", run = "r1", values = ["accept", "diff", "other", "discard"]) => ({
  type: "input_request",
  request_id: id,
  kind: "choice",
  title: "Which result do we take?",
  body: "",
  options: values.map((value) => ({ value, label: value })),
  current_value: "",
  help_text: "",
  purpose: "bestof_decision",
  run_id: run,
});

function fullRun() {
  const agent = createAgent("a1", "/w");
  feed(
    agent,
    started(),
    status("e1", { step: 4, tests: { state: "running", summary: "" }, diff: { files: 2, added: 30, removed: 4 } }),
    status("e2", { step: 3 }),
    status("e3", { step: 2 }),
    status("e1", { state: "finished", step: 8, tests: { state: "passed", summary: "12 passed" }, diff: { files: 2, added: 30, removed: 4 } }),
    { type: "executor_activity", run_id: "r1", executor_id: "e2", phase: "step", call_id: "c1", action_type: "run_shell", label: "runs pytest -q", summary: "", output: "" },
    { type: "bestof_eliminated", run_id: "r1", executor_id: "e3", reason: "tests keep failing" },
    verdict(),
    { type: "bestof_finished", run_id: "r1", outcome: "pending", message: "Waiting for your decision" },
    decisionRequest(),
  );
  return agent;
}

test("a best-of run builds the arena row by row and ends in the decision", () => {
  const agent = fullRun();
  const { arena } = agent.bestof;
  assert.deepEqual([arena.runId, arena.task, arena.count, arena.base, arena.executors.length], ["r1", "fix the parser", 3, "abc123", 3]);
  const [e1, e2, e3] = arena.executors;
  assert.deepEqual([e1.state, e1.step, e1.tests.state, e1.tests.summary, e1.diff], ["finished", 8, "passed", "12 passed", { files: 2, added: 30, removed: 4 }]);
  assert.deepEqual([e1.style, e1.branch], ["senior-dev", "kvist/bestof/r1/e1"], "a status with no style keeps the name the row has");
  assert.deepEqual([e2.last.label, e2.last.state], ["runs pytest -q", "run"]);
  assert.deepEqual([e3.state, e3.reason], ["eliminated", "tests keep failing"]);
  assert.equal(arena.verdict.winner, "e2");
  assert.equal(arena.verdict.rows[1].outcome, "winner");
  assert.deepEqual([arena.finished.outcome, arena.finished.id, arena.finished.commit], ["pending", "", ""]);
  assert.equal(agent.dialogs.length, 1);
  assert.ok(decisionOf(agent, agent.dialogs[0]), "the question is the decision screen");
});

test("a status replaces the row's snapshot and an unknown state word keeps the old state", () => {
  const agent = createAgent("a1", "/w");
  feed(agent, started(), status("e1", { state: "running", step: 2 }), status("e1", { state: "levitating", step: 5, tests: "7 passed" }));
  const e1 = agent.bestof.arena.executors[0];
  assert.deepEqual([e1.state, e1.step, e1.tests], ["running", 5, { state: "unknown", summary: "7 passed" }]);
  feed(agent, status("e9", { style: "late" }));
  assert.equal(agent.bestof.arena.executors.length, 4, "a status may name an executor bestof_started did not list");
});

test("a call's result finishes the latest call only", () => {
  const agent = createAgent("a1", "/w");
  const activity = (phase, call, extra = {}) => ({ type: "executor_activity", run_id: "r1", executor_id: "e1", phase, call_id: call, action_type: "read_file", label: `reads ${call}`, ...extra });
  feed(agent, started(), activity("step", "c1"));
  feed(agent, activity("result", "other", { ok: true, summary: "x" }));
  assert.equal(agent.bestof.arena.executors[0].last.state, "run");
  feed(agent, activity("result", "c1", { ok: true, summary: "84 lines" }));
  assert.deepEqual([agent.bestof.arena.executors[0].last.state, agent.bestof.arena.executors[0].last.summary], ["ok", "84 lines"]);
  feed(agent, activity("step", "c2"), activity("result", "c2", { ok: false, summary: "no such file" }));
  assert.equal(agent.bestof.arena.executors[0].last.state, "fail");
});

test("events of another run leave the arena alone", () => {
  const agent = fullRun();
  const before = JSON.stringify(agent.bestof.arena);
  const rev = agent.rev;
  feed(
    agent,
    status("e1", { state: "failed" }, "r2"),
    { type: "executor_activity", run_id: "r2", executor_id: "e1", phase: "step", call_id: "x", label: "x" },
    { type: "bestof_eliminated", run_id: "r2", executor_id: "e1", reason: "x" },
    verdict("r2"),
    { type: "bestof_diff", run_id: "r2", executor_id: "e1", patch: "x", files: [] },
    { type: "bestof_finished", run_id: "r2", outcome: "accepted", message: "" },
  );
  assert.equal(JSON.stringify(agent.bestof.arena), before);
  assert.equal(agent.rev, rev, "events that are no news cost no redraw");
});

test("the next run replaces the arena, and a new turn takes it off the screen", () => {
  const agent = fullRun();
  feed(agent, started("r2"));
  assert.equal(agent.bestof.arena.runId, "r2");
  assert.equal(agent.bestof.arena.verdict, null);
  beginTurn(agent);
  assert.equal(agent.bestof.arena, null);
});

// -- the decision dialog ---------------------------------------------------------------------------------------------

test("the decision screen answers with exactly the value the core takes", () => {
  const agent = fullRun();
  const { arena } = agent.bestof;
  const decision = decisionOf(agent, agent.dialogs[0]);
  assert.equal(decision.row, 1, "the winner's row first");
  assert.deepEqual(decision.actions, ["accept", "diff", "other", "discard"]);
  assert.deepEqual(decisionReply(arena, decision, "accept"), { value: "accept" });
  assert.deepEqual(decisionReply(arena, decision, "diff"), { value: "diff:e2" });
  assert.deepEqual(decisionReply(arena, decision, "other"), { value: "other" }, "on the winner's row it is the core's own question");
  assert.deepEqual(decisionReply(arena, decision, "discard"), { value: "discard" });
  decision.row = 2;
  assert.deepEqual(decisionReply(arena, decision, "diff"), { value: "diff:e3" });
  assert.deepEqual(decisionReply(arena, decision, "other"), { value: "pick:e3" });
  assert.deepEqual(decisionReply(arena, decision, "accept"), { value: "accept" }, "accepting is always about the winner");
  assert.deepEqual(DECISION_CANCEL, { value: null }, "closing the dialog answers null");
});

test("only the buttons the core offered are shown", () => {
  const agent = createAgent("a1", "/w");
  feed(agent, started(), verdict(), decisionRequest("q1", "r1", ["accept", "discard", "elsewhere"]));
  assert.deepEqual(decisionOf(agent, agent.dialogs[0]).actions, ["accept", "discard"]);
});

test("without a verdict, for another run or with another purpose the question stays an ordinary dialog", () => {
  const noVerdict = createAgent("a1", "/w");
  feed(noVerdict, started(), decisionRequest());
  assert.equal(decisionOf(noVerdict, noVerdict.dialogs[0]), null);

  const foreign = createAgent("a2", "/w");
  feed(foreign, started(), verdict(), decisionRequest("q1", "r2"));
  assert.equal(decisionOf(foreign, foreign.dialogs[0]), null);

  const plain = createAgent("a3", "/w");
  const { purpose, ...request } = decisionRequest();
  feed(plain, started(), verdict(), { ...request });
  assert.equal(decisionOf(plain, plain.dialogs[0]), null);

  const noArena = createAgent("a4", "/w");
  feed(noArena, decisionRequest());
  assert.equal(decisionOf(noArena, noArena.dialogs[0]), null);
  assert.ok(purpose);
});

test("after a diff the core asks again and the patch is shown once", () => {
  const agent = fullRun();
  assert.equal(decisionOf(agent, agent.dialogs[0]).page, "table");
  agent.dialogs = [];
  feed(
    agent,
    { type: "bestof_diff", run_id: "r1", executor_id: "e2", patch: "diff --git a/a.py b/a.py\n@@ -1 +1 @@\n-a\n+b", files: ["a.py"] },
    decisionRequest("q2"),
  );
  const again = decisionOf(agent, agent.dialogs[0]);
  assert.equal(again.page, "patch");
  assert.deepEqual([agent.bestof.arena.diff.id, agent.bestof.arena.diff.files], ["e2", ["a.py"]]);
  feed(agent, decisionRequest("q3"));
  assert.equal(decisionOf(agent, agent.dialogs.find((d) => d.request_id === "q3")).page, "table", "a patch already shown does not open the next question");
});

// -- the council ---------------------------------------------------------------------------------------------------

test("a council fills its four cards, shows the risks under them and lights the chosen one", () => {
  const agent = createAgent("a1", "/w");
  feed(agent, {
    type: "council_started",
    council_id: "c1",
    question: "Which queue?",
    members: ANGLE_IDS.map((angle) => ({ executor_id: angle, angle, session: `s-${angle}` })),
  });
  const { council } = agent.bestof;
  assert.deepEqual(council.cards.map((c) => c.angle), ["simplicity", "security", "speed", "maintenance"]);
  assert.ok(council.cards.every((c) => c.decision === null), "no card has arrived");

  feed(
    agent,
    { type: "council_card", council_id: "c1", executor_id: "simplicity", angle: "simplicity", decision: "Use a list", argument: "It is enough" },
    { type: "council_card", council_id: "c1", executor_id: "security", angle: "security", decision: "Bounded queue", argument: "No unbounded growth" },
    { type: "council_objection", council_id: "c1", executor_id: "security", angle: "security", risks: [{ target: "simplicity", risk: "grows without limit" }] },
    { type: "council_card", council_id: "other", executor_id: "speed", angle: "speed", decision: "x", argument: "x" },
  );
  assert.equal(council.cards[0].decision, "Use a list");
  assert.equal(council.cards[2].decision, null, "a card of another council is no news");
  assert.deepEqual(council.cards[1].risks, [{ target: "simplicity", risk: "grows without limit" }]);
  assert.ok(council.cards.every((c) => !isChosen(council, c)));

  feed(agent, {
    type: "council_verdict",
    council_id: "c1",
    chosen: "security",
    decision: "Bounded queue",
    reason: "Safe and small.",
    dissent: [{ executor_id: "speed", angle: "speed", why: "slower to build" }, { executor_id: "x" }],
  });
  assert.deepEqual(council.cards.map((c) => isChosen(council, c)), [false, true, false, false]);
  assert.deepEqual(council.verdict.dissent, [{ who: "speed", why: "slower to build" }, { who: "x", why: "" }]);
  assert.equal(council.verdict.reason, "Safe and small.");
});

test("the angles have names and a line each, in both languages", () => {
  assert.deepEqual(ANGLE_IDS, ["simplicity", "security", "speed", "maintenance"]);
  assert.equal(angleName("simplicity"), "Simplicity");
  assert.ok(angleBlurb("maintenance"));
  assert.equal(angleName("novel"), "novel", "an angle this interface does not know is shown as the core named it");
  assert.deepEqual(["Простота", "Безопасность", "Скорость разработки", "Цена поддержки"], [RU.angleSimplicity, RU.angleSecurity, RU.angleSpeed, RU.angleMaintenance]);
});

test("a best-of run and a council do not stand side by side", () => {
  const agent = fullRun();
  feed(agent, { type: "council_started", council_id: "c1", question: "q", members: [] });
  assert.equal(agent.bestof.arena, null);
  assert.equal(agent.bestof.council.id, "c1");
  feed(agent, started("r3"));
  assert.equal(agent.bestof.council, null);
});

// -- money and tool mode -------------------------------------------------------------------------------------------

const call = (input, output, extra = {}) => ({ type: "usage", input_tokens: input, output_tokens: output, cost_approx: false, ...extra });
const label = (agent, russian = false) => spendLabel(totalSpend(agent), russian);

test("the cost adds up the main stream and the executors' latest snapshots, with ≈ for a shaky price", () => {
  const agent = createAgent("a1", "/w");
  feed(agent, call(2000, 100, { cost_usd: 0.5 }));
  assert.equal(label(agent), "$0.50");
  const usage = (cost, approx = false) => ({ input_tokens: 10, output_tokens: 1, cost_usd: cost, cost_approx: approx });
  feed(agent, started(), status("e1", { usage: usage(0.25) }));
  feed(agent, status("e1", { usage: usage(0.5) }));
  assert.equal(label(agent), "$1.00", "a snapshot replaces the one before");
  feed(agent, status("e2", { usage: usage(0.25, true) }));
  assert.equal(label(agent), "≈$1.25", "one approximate price makes the sum approximate");
  feed(agent, status("e3", { usage: { input_tokens: 7, output_tokens: 1, cost_approx: false } }));
  assert.equal(label(agent), "≈$1.25", "a call with no price among priced ones does too");
  beginTurn(agent);
  assert.equal(agent.bestof.arena, null);
  assert.equal(label(agent), "≈$1.25", "the executors' cost stays in the total when the run leaves the screen");
});

test("a price of a tenth of a cent is not $0.00", () => {
  const spend = newSpend();
  addUsage(spend, usageOf(call(5, 1, { cost_usd: 0.0034 })));
  assert.equal(spendLabel(spend, false), "$0.0034");
});

test("without any price it is tokens, and the uncached part only for calls that said what the cache served", () => {
  const agent = createAgent("a1", "/w");
  feed(agent, call(10000, 500, { cache_read_tokens: 8000 }), call(5000, 500));
  assert.equal(label(agent), "total 16.0k tok · ≈7.0k uncached", "the call that did not say makes the number an upper bound");
  assert.equal(label(agent, true), "всего 16,0 тыс. ток · ≈7,0 тыс. некэш.");
  feed(agent, call(1000, 0, { cache_read_tokens: 1000 }));
  assert.equal(label(agent), "total 17.0k tok · ≈7.0k uncached");

  const known = createAgent("a2", "/w");
  feed(known, call(10000, 2000, { cache_read_tokens: 6000 }), call(3000, 300, { cache_read_tokens: 0 }));
  assert.equal(label(known), "total 15.3k tok · 7.0k uncached", "every call said: no ≈");

  const silent = createAgent("a3", "/w");
  feed(silent, call(10000, 500), call(5000, 500));
  assert.equal(label(silent), "total 16.0k tok", "nobody said: no uncached part at all");
  assert.equal(label(createAgent("a4", "/w")), null, "nothing reported, nothing shown");
});

test("executors without a price count in tokens too, and a missing price is not zero", () => {
  const agent = createAgent("a1", "/w");
  feed(agent, started(), status("e1", { usage: { input_tokens: 1000, output_tokens: 200, cache_read_tokens: 400, cost_approx: false } }));
  assert.equal(label(agent), "total 1.2k tok · 600 uncached");
  assert.equal(arenaSpend(agent.bestof.arena).priced, 0);
});

test("the tool mode is the last one the core named", () => {
  const agent = createAgent("a1", "/w");
  assert.equal(agent.bestof.toolMode, null);
  feed(agent, { type: "tool_mode", mode: "text", reason: "probe_failed" });
  assert.deepEqual(agent.bestof.toolMode, { mode: "text", reason: "probe_failed" });
  feed(agent, { type: "tool_mode", mode: "native", reason: "probe_ok" });
  assert.deepEqual(agent.bestof.toolMode, { mode: "native", reason: "probe_ok" });
});

test("an ordinary conversation puts nothing of this module on the screen, whatever tool_mode and usage say", () => {
  const agent = createAgent("a1", "/w");
  feed(agent, { type: "hello", provider: "x", role: "coder", session: "s1" }, { type: "tool_mode", mode: "native", reason: "probe_ok" }, call(2000, 100, { cost_usd: 0.5 }));
  assert.equal(onScreen(agent), false, "no layer, so no bar: the conversation's header is not touched");
  assert.deepEqual(barOf(agent, false), { mode: { mode: "native", reason: "probe_ok" }, money: "$0.50" }, "the data is kept for the layer");
  feed(agent, started());
  assert.equal(onScreen(agent), true);
  beginTurn(agent);
  assert.equal(onScreen(agent), false);
  feed(agent, { type: "council_started", council_id: "c1", question: "q", members: [] });
  assert.equal(onScreen(agent), true);
});

// -- the commands that are turns ---------------------------------------------------------------------------------------

test("/best-of is a turn that ends and leaves the input free", async () => {
  const agent = createAgent("a1", "/w");
  feed(agent, { type: "hello", provider: "x", role: "coder", session: "s1" });
  await runCommand(agent, "best-of", ["3", "fix", "the", "parser"]);
  assert.equal(agent.state, "running", "Stop works at once");
  assert.equal(agent.items[0].text, "/best-of 3 fix the parser");
  assert.equal(agent.bestof.turnCommand, "best-of");

  feed(agent, started(), { type: "turn_end" });
  assert.equal(agent.state, "idle");
  assert.equal(agent.bestof.turnCommand, "best-of", "the result has not come");
  feed(agent, { type: "command_result", name: "best-of", ok: true, message: "Done / Готово", effects: {} });
  assert.equal(agent.bestof.turnCommand, null);
  assert.equal(agent.state, "idle");
  await closeAgent(agent.id);
});

test("a command that fails before its turn began does not leave the turn running", async () => {
  for (const name of ["best-of", "council"]) {
    const agent = createAgent("a1", "/w");
    feed(agent, { type: "hello", provider: "x", role: "coder", session: "s1" });
    await runCommand(agent, name, ["x"]);
    assert.equal(agent.state, "running");
    feed(agent, { type: "command_result", name, ok: false, message: "Usage / Использование", effects: {} });
    assert.equal(agent.state, "idle", `/${name} sent no turn_end`);
    assert.equal(agent.bestof.turnCommand, null);
    await closeAgent(agent.id);
  }
});

test("a command that is not a turn is left alone", async () => {
  const agent = createAgent("a1", "/w");
  feed(agent, { type: "hello", provider: "x", role: "coder", session: "s1" });
  await runCommand(agent, "role", ["coder"]);
  assert.equal(agent.state, "idle");
  assert.equal(agent.items.length, 0);
  await closeAgent(agent.id);
});

// -- the executor's log ----------------------------------------------------------------------------------------------

test("an executor's journal replays into a state of its own and leaves the conversation alone", async () => {
  const agent = fullRun();
  agent.items.length = 0;
  feed(agent, { type: "user", text: "main request" });
  const mainItems = JSON.stringify(agent.items);
  const mainSpend = JSON.stringify(agent.bestof.spend);
  const asked = [];
  const events = [
    { event: { type: "user", text: "fix it", executor_id: "e2" } },
    { event: { type: "step", step: 1, call_id: "c1", action_type: "run_shell", action_description: "runs the tests", user_message: "", executor_id: "e2" } },
    { event: { type: "action_result", call_id: "c1", ok: true, summary: "exit 0", output: "12 passed", executor_id: "e2" } },
    { event: { type: "step", step: 2, call_id: "c2", action_type: "read_file", action_args: { path: "a.py" }, user_message: "", executor_id: "e2" } },
  ];
  let changes = 0;
  const executor = agent.bestof.arena.executors[1];
  const opening = openExecutorLog(agent, executor, async (session) => (asked.push(session), { ok: true, has_more: true, events }), () => changes++);
  assert.equal(agent.bestof.log.status, "loading");
  await opening;
  const log = agent.bestof.log;
  assert.deepEqual(asked, ["s-e2"]);
  assert.deepEqual([log.status, log.older, log.id, log.style], ["ready", true, "e2", "minimalist"]);
  assert.deepEqual(log.view.items.map((item) => item.kind), ["user", "call", "call"]);
  assert.deepEqual([log.view.items[1].phase, log.view.items[1].label], ["ok", "runs the tests"]);
  assert.equal(log.view.items[2].phase, "fail", "a call the journal never finished is shown stopped");
  assert.equal(JSON.stringify(agent.items), mainItems, "the conversation is untouched");
  assert.equal(JSON.stringify(agent.bestof.spend), mainSpend);
  assert.equal(log.view.dialogs.length, 0);
  assert.equal(changes, 2, "once when it opens, once when it is read");
});

test("a journal that cannot be read says why, and one closed meanwhile is dropped", async () => {
  const agent = fullRun();
  const executor = agent.bestof.arena.executors[0];
  await openExecutorLog(agent, executor, async () => ({ ok: false, error: "unknown session" }), () => {});
  assert.deepEqual([agent.bestof.log.status, agent.bestof.log.error], ["failed", "unknown session"]);
  await openExecutorLog(agent, executor, async () => { throw new Error("no core"); }, () => {});
  assert.equal(agent.bestof.log.error, "no core");

  let release;
  const slow = new Promise((resolve) => { release = resolve; });
  const reading = openExecutorLog(agent, executor, () => slow, () => {});
  const log = agent.bestof.log;
  agent.bestof.log = null;
  release({ ok: true, events: [{ event: { type: "user", text: "late" } }] });
  await reading;
  assert.equal(agent.bestof.log, null);
  assert.equal(log.status, "loading", "the late answer did not touch the closed log");
});

test("a run of the past in a replayed journal leaves nothing on the screen", () => {
  const agent = createAgent("a1", "/w");
  replayHistory(agent, [
    { event: { type: "user", text: "/best-of 3 x" } },
    { event: started() },
    { event: { type: "bestof_eliminated", run_id: "r1", executor_id: "e3", reason: "bad" } },
    { event: verdict() },
    { event: { type: "bestof_finished", run_id: "r1", outcome: "accepted", executor_id: "e2", commit: "abc", message: "done" } },
    { event: { type: "tool_mode", mode: "native", reason: "probe_ok" } },
    { event: { type: "usage", kind: "turn", input_tokens: 900, output_tokens: 100, cost_usd: 0.2, cost_approx: false } },
    { event: { type: "turn_end" } },
  ]);
  assert.equal(agent.bestof.arena, null);
  assert.equal(agent.bestof.toolMode.mode, "native", "the way calls travelled is recorded");
  assert.equal(label(agent), "$0.20");
});

// -- words ------------------------------------------------------------------------------------------------------------

test("both languages have the same words, and nothing says who judges", () => {
  assert.deepEqual(Object.keys(EN).sort(), Object.keys(RU).sort());
  for (const table of [EN, RU]) for (const value of Object.values(table)) assert.doesNotMatch(value, /judge|судь/i);
  assert.equal(EN.bestofTitle, "best-of");
  assert.equal(EN.councilTitle, "council");
});

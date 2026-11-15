// What the core says about a best-of run, a council, the way calls travel and the money spent (docs/UI_API.md §7: the
// `bestof_*`, `executor_activity`, `council_*` and `tool_mode` events), folded into `agent.bestof`. Pure data in, data
// out, like reducer.js, which hands the events over; the views read the state and never change it.
//
// A snapshot (`bestof_status`) replaces the executor's previous one; an event of another run or council than the one on
// screen is not news. Fields the core leaves out stay unknown: a missing price is not zero.

import { EN, RU } from "./bestof-words.js";

export const DECISION_PURPOSE = "bestof_decision";

/** Core commands that are whole turns: events, `turn_end`, then `command_result`. */
const TURN_COMMANDS = new Set(["best-of", "council"]);
export const isTurnCommand = (name) => TURN_COMMANDS.has(name);

/** The buttons of the decision dialog, in the order they stand; the core offers some of them. */
export const DECISION_ACTIONS = ["accept", "diff", "other", "discard"];

const EXECUTOR_STATES = new Set(["waiting", "running", "finished", "failed", "stopped", "eliminated", "lost"]);
const OUT_STATES = new Set(["eliminated", "failed", "stopped", "lost"]);
const TEST_STATES = new Set(["unknown", "running", "passed", "failed"]);
const OUTCOMES = new Set(["winner", "finalist", "eliminated", "failed"]);

/** Out of the race: the row is shown muted. */
export const isOut = (state) => OUT_STATES.has(state);

const text = (value) => (typeof value === "string" ? value : "");
const list = (value) => (Array.isArray(value) ? value : []);
const number = (value) => (typeof value === "number" && Number.isFinite(value) ? value : 0);

export function createBestof() {
  return {
    arena: null,
    council: null,
    // {mode, reason} from the last `tool_mode`
    toolMode: null,
    // What the main stream spent, and the runs that have gone from the screen.
    spend: newSpend(),
    // An executor's journal open over the arena: see bestof-log.js
    log: null,
    // The name of a core command that is a turn, from the moment it is sent until its `command_result`.
    turnCommand: null,
  };
}

// -- money and tokens ---------------------------------------------------------------------------------------------

export const newSpend = () => ({ tokens: 0, uncached: 0, cacheKnown: 0, cacheUnknown: 0, cost: 0, priced: 0, unpriced: 0, approx: false });

/** One call's tokens and price from a `usage` event or a `bestof_status` `usage` object. */
export function usageOf(raw) {
  return {
    input: number(raw.input_tokens),
    output: number(raw.output_tokens),
    cacheRead: typeof raw.cache_read_tokens === "number" ? raw.cache_read_tokens : null,
    cost: typeof raw.cost_usd === "number" ? raw.cost_usd : null,
    approx: raw.cost_approx === true,
  };
}

export function addUsage(spend, usage) {
  const spent = usage.input + usage.output;
  if (spent === 0 && usage.cost === null) return;
  spend.tokens += spent;
  if (usage.cacheRead === null) {
    // A call that did not say what the cache served: all of its input counts as uncached (an upper bound).
    spend.uncached += usage.input;
    spend.cacheUnknown++;
  } else {
    spend.uncached += Math.max(0, usage.input - usage.cacheRead);
    spend.cacheKnown++;
  }
  if (usage.cost === null) {
    spend.unpriced++;
    return;
  }
  spend.cost += usage.cost;
  spend.priced++;
  spend.approx ||= usage.approx;
}

export function mergeSpend(into, other) {
  for (const key of ["tokens", "uncached", "cacheKnown", "cacheUnknown", "cost", "priced", "unpriced"]) into[key] += other[key];
  into.approx ||= other.approx;
}

/** Enough digits for a small amount to show: a price of a tenth of a cent is not "$0.00". */
function money(cost) {
  return cost.toFixed(cost >= 0.1 ? 2 : cost >= 0.01 ? 3 : 4);
}

/** 950, 12.3k, 1.4M (in Russian: 12,3 тыс., 1,4 млн). */
export function countTokens(n, russian) {
  if (n < 1000) return String(n);
  const [value, unit] = n < 1e6 ? [n / 1e3, russian ? " тыс." : "k"] : [n / 1e6, russian ? " млн" : "M"];
  const digits = value.toFixed(1);
  return `${russian ? digits.replace(".", ",") : digits}${unit}`;
}

/**
 * "≈$0.42" when anything was priced ("≈" when a price is approximate or some call has none), else
 * "total 12.3k tok · 4.5k uncached"; null before the first call. The uncached part is an upper bound ("≈") when some call did
 * not say what the cache served, and left out when none did.
 */
export function spendLabel(spend, russian) {
  if (spend.tokens === 0 && spend.priced === 0) return null;
  if (spend.priced > 0) return `${spend.approx || spend.unpriced > 0 ? "≈" : ""}$${money(spend.cost)}`;
  const words = russian ? RU : EN;
  let label = `${words.total} ${countTokens(spend.tokens, russian)} ${words.tokens}`;
  if (spend.cacheKnown > 0) label += ` · ${spend.cacheUnknown > 0 ? "≈" : ""}${countTokens(spend.uncached, russian)} ${words.uncached}`;
  return label;
}

/** What the executors of a run have spent, by their latest snapshots. */
export function arenaSpend(arena) {
  const spend = newSpend();
  for (const executor of arena.executors) if (executor.usage) addUsage(spend, executor.usage);
  return spend;
}

/** Everything spent in the session: the main stream, and the executors of a run still on screen. */
export function totalSpend(agent) {
  const total = newSpend();
  mergeSpend(total, agent.bestof.spend);
  if (agent.bestof.arena) mergeSpend(total, arenaSpend(agent.bestof.arena));
  return total;
}

/** Whether the best-of or council layer is up. Everything this module adds to the screen lives in that layer and nowhere else. */
export const onScreen = (agent) => Boolean(agent.bestof.arena || agent.bestof.council);

/** What the layer's bar says: how calls travel and the money spent; both null until there is something to say. */
export function barOf(agent, russian) {
  const { toolMode } = agent.bestof;
  return { mode: toolMode && toolMode.mode ? toolMode : null, money: spendLabel(totalSpend(agent), russian) };
}

/** An executor's own line: its money, or its tokens. */
export function executorSpendLabel(executor, russian) {
  if (!executor.usage) return null;
  const spend = newSpend();
  addUsage(spend, executor.usage);
  return spendLabel(spend, russian);
}

// -- the best-of arena ----------------------------------------------------------------------------------------------

const newExecutor = (id) => ({
  id,
  style: "",
  branch: "",
  session: "",
  state: "waiting",
  step: 0,
  tests: { state: "unknown", summary: "" },
  diff: { files: 0, added: 0, removed: 0 },
  usage: null,
  // Why it is out, in the core's words.
  reason: "",
  // Its latest call: {call, label, state: run|ok|fail, summary}
  last: null,
});

function testsOf(raw) {
  if (typeof raw === "string") return { state: "unknown", summary: raw };
  if (!raw || typeof raw !== "object") return { state: "unknown", summary: "" };
  return { state: TEST_STATES.has(raw.state) ? raw.state : "unknown", summary: text(raw.summary) };
}

function diffOf(raw) {
  const stat = raw && typeof raw === "object" ? raw : {};
  return { files: number(stat.files), added: number(stat.added), removed: number(stat.removed) };
}

function newArena(ev) {
  return {
    runId: text(ev.run_id),
    task: text(ev.task),
    count: number(ev.count),
    base: text(ev.base),
    executors: list(ev.executors).map((e) => ({ ...newExecutor(text(e.executor_id)), style: text(e.style), branch: text(e.branch), session: text(e.session) })),
    verdict: null,
    // The patch the person asked to see, and whether no decision dialog has shown it yet.
    diff: null,
    diffFresh: false,
    finished: null,
    // The open decision dialog, if this arena has one: see noteRequest
    decision: null,
  };
}

/** The executor with this id; a status may arrive for one `bestof_started` did not list. */
function executorOf(arena, id) {
  let executor = arena.executors.find((e) => e.id === id);
  if (!executor) {
    executor = newExecutor(id);
    arena.executors.push(executor);
  }
  return executor;
}

function applyStatus(arena, ev) {
  const executor = executorOf(arena, text(ev.executor_id));
  // An empty style or branch does not erase the name the row has; a state word this interface does not know leaves the old one.
  if (text(ev.style)) executor.style = ev.style;
  if (text(ev.branch)) executor.branch = ev.branch;
  if (EXECUTOR_STATES.has(ev.state)) executor.state = ev.state;
  executor.step = number(ev.step);
  executor.tests = testsOf(ev.tests);
  executor.diff = diffOf(ev.diff);
  executor.usage = ev.usage && typeof ev.usage === "object" ? usageOf(ev.usage) : null;
}

function applyActivity(arena, ev) {
  const executor = executorOf(arena, text(ev.executor_id));
  if (ev.phase === "step") {
    executor.last = { call: text(ev.call_id), label: text(ev.label) || text(ev.action_type), state: "run", summary: "" };
    return;
  }
  // The result of the latest call only; one of an older call is no news for the row. `ok` is there from the result on.
  if (ev.phase === "result" && executor.last?.call === text(ev.call_id)) {
    executor.last.state = ev.ok === true ? "ok" : "fail";
    executor.last.summary = text(ev.summary);
  }
}

function verdictOf(ev) {
  return {
    winner: text(ev.winner),
    reason: text(ev.reason),
    rows: list(ev.rows).map((row) => ({
      id: text(row.executor_id),
      style: text(row.style),
      outcome: OUTCOMES.has(row.outcome) ? row.outcome : "",
      tests: testsOf(row.tests),
      diff: diffOf(row.diff),
      note: text(row.note),
    })),
  };
}

function foldArena(arena, ev) {
  switch (ev.type) {
    case "bestof_status":
      applyStatus(arena, ev);
      return;
    case "executor_activity":
      applyActivity(arena, ev);
      return;
    case "bestof_eliminated": {
      const executor = executorOf(arena, text(ev.executor_id));
      executor.state = "eliminated";
      executor.reason = text(ev.reason);
      return;
    }
    case "bestof_verdict":
      arena.verdict = verdictOf(ev);
      return;
    case "bestof_diff":
      arena.diff = { id: text(ev.executor_id), patch: text(ev.patch), files: list(ev.files).filter((f) => typeof f === "string") };
      arena.diffFresh = true;
      return;
    case "bestof_finished":
      arena.finished = { outcome: text(ev.outcome), id: text(ev.executor_id), commit: text(ev.commit), message: text(ev.message) };
      return;
    default:
  }
}

// -- the decision dialog ---------------------------------------------------------------------------------------------

/**
 * Called for every `input_request`. When it is the end-of-run question of the arena on screen (a choice with
 * `purpose: "bestof_decision"` and the arena's `run_id`) and there is a verdict to put in front of the person, the arena
 * keeps a `decision` for it; the dialog draws that instead of a plain list. Any other request is an ordinary dialog.
 */
export function noteRequest(agent, ev) {
  const arena = agent.bestof.arena;
  if (!arena?.verdict) return;
  if (ev.purpose !== DECISION_PURPOSE || ev.kind !== "choice" || text(ev.run_id) !== arena.runId) return;
  const offered = list(ev.options).map((option) => option?.value);
  const actions = DECISION_ACTIONS.filter((action) => offered.includes(action));
  if (!actions.length || !text(ev.request_id)) return;
  const fresh = arena.diffFresh;
  arena.diffFresh = false;
  const winner = arena.verdict.rows.findIndex((row) => row.id === arena.verdict.winner);
  arena.decision = {
    requestId: ev.request_id,
    actions,
    // The row the person is on: the winner's to begin with.
    row: Math.max(0, winner),
    // A patch that has just arrived is what the core asked again for; show it once.
    page: fresh && arena.diff ? "patch" : "table",
  };
}

/** The decision for this dialog, or null for any other. */
export function decisionOf(agent, request) {
  const decision = agent.bestof.arena?.decision;
  return decision && decision.requestId === request.request_id ? decision : null;
}

/**
 * The reply for `action`: accepting takes the winner; the diff and a pick are about the row the person is on. Picking the
 * winner is the core's own "other" question, which asks who instead.
 */
export function decisionReply(arena, decision, action) {
  const id = arena.verdict.rows[decision.row]?.id ?? "";
  if (action === "diff" && id) return { value: `diff:${id}` };
  if (action === "other" && id && id !== arena.verdict.winner) return { value: `pick:${id}` };
  return { value: action };
}

/** Closing the dialog is no decision for now: the core takes `null` that way. */
export const DECISION_CANCEL = { value: null };

// -- the council ---------------------------------------------------------------------------------------------------

const newCard = (id, angle) => ({ id, angle, decision: null, argument: "", risks: [] });

function newCouncil(ev) {
  return { id: text(ev.council_id), question: text(ev.question), cards: list(ev.members).map((m) => newCard(text(m.executor_id), text(m.angle))), verdict: null };
}

function cardOf(council, ev) {
  const id = text(ev.executor_id);
  let card = council.cards.find((c) => c.id === id);
  if (!card) {
    card = newCard(id, text(ev.angle));
    council.cards.push(card);
  }
  if (!card.angle) card.angle = text(ev.angle);
  return card;
}

function foldCouncil(council, ev) {
  switch (ev.type) {
    case "council_card": {
      const card = cardOf(council, ev);
      card.decision = text(ev.decision);
      card.argument = text(ev.argument);
      return;
    }
    case "council_objection":
      cardOf(council, ev).risks = list(ev.risks).map((r) => ({ target: text(r.target), risk: text(r.risk) }));
      return;
    case "council_verdict":
      council.verdict = {
        chosen: text(ev.chosen),
        decision: text(ev.decision),
        reason: text(ev.reason),
        // `angle` is the member's angle id; the executor id stands in when the core named none.
        dissent: list(ev.dissent).map((d) => ({ who: text(d.angle) || text(d.executor_id), why: text(d.why) })),
      };
      return;
    default:
  }
}

/** Whether `card` is the one the verdict chose: `chosen` names an executor, or its angle. */
export function isChosen(council, card) {
  const chosen = council.verdict?.chosen;
  return Boolean(chosen) && (chosen === card.id || chosen.toLowerCase() === card.angle.toLowerCase());
}

// -- the events ---------------------------------------------------------------------------------------------------

/** Takes the arena, the council and an open executor log off the screen. What the executors spent stays in the total. */
export function clearScreens(agent) {
  const state = agent.bestof;
  if (state.arena) mergeSpend(state.spend, arenaSpend(state.arena));
  state.arena = null;
  state.council = null;
  state.log = null;
}

export function noteUsage(agent, ev) {
  addUsage(agent.bestof.spend, usageOf(ev));
}

/** Folds one best-of, council or `tool_mode` event into `agent.bestof`; false when it was no news. */
export function foldRun(agent, ev) {
  const state = agent.bestof;
  switch (ev.type) {
    case "tool_mode":
      state.toolMode = { mode: text(ev.mode), reason: text(ev.reason) };
      return true;
    case "bestof_started":
      clearScreens(agent);
      state.arena = newArena(ev);
      return true;
    case "council_started":
      clearScreens(agent);
      state.council = newCouncil(ev);
      return true;
    case "council_card":
    case "council_objection":
    case "council_verdict":
      if (!state.council || state.council.id !== text(ev.council_id)) return false;
      foldCouncil(state.council, ev);
      return true;
    default:
      if (!state.arena || state.arena.runId !== text(ev.run_id)) return false;
      foldArena(state.arena, ev);
      return true;
  }
}

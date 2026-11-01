// What the core says (docs/UI_API.md §7) folded into one agent's state. Pure
// data in, data out: no DOM, so the same function redraws a live turn and a
// journal replayed from `--session-op history`.
//
// The agent is mutated in place. `agent.rev` rises on every change and each
// feed item has its own `rev`, so a view redraws only what moved.

import { parseImageGoal } from "./views/attachments.js";

const MAX_ACTIVITY = 30;

export function createAgent(id, workspace, session = "") {
  return {
    id,
    workspace,
    session,
    title: "",
    // asleep: no core yet (restored tab) · starting · idle · running · paused · gone
    state: "starting",
    taskState: "",
    role: "",
    routedRole: "",
    provider: "",
    models: {},
    effort: "",
    verbose: false,
    manifest: null,
    items: [],
    nextItem: 1,
    streams: new Map(),
    calls: new Map(),
    status: { text: "", step: 0 },
    // The turn's own clock, for the spinner line: when it began, when the model last showed life, output so far.
    turn: { startedAt: 0, waitingSince: 0, outputAtStart: 0 },
    dialogs: [],
    helpers: new Map(),
    jobs: new Map(),
    screen: null,
    context: { tokens: 0, limit: 0 },
    spend: { input: 0, output: 0 },
    changed: new Map(),
    closed: null,
    attention: false,
    needsHistory: false,
    command: null,
    rev: 0,
  };
}

function push(agent, item) {
  item.id = agent.nextItem++;
  item.rev = 1;
  agent.items.push(item);
  agent.rev++;
  return item;
}

function touch(agent, item) {
  item.rev++;
  agent.rev++;
}

// The model's words belong above the card they introduce, which a stream may have opened already.
function placeBefore(agent, item, anchor) {
  agent.items.splice(agent.items.indexOf(item), 1);
  agent.items.splice(agent.items.indexOf(anchor), 0, item);
}

export function addNote(agent, text, tone = "muted") {
  return push(agent, { kind: "note", text, tone });
}

export function addUser(agent, text, images = []) {
  push(agent, { kind: "user", text, images });
}

export function beginTurn(agent) {
  const now = Date.now();
  agent.turn = { startedAt: now, waitingSince: now, outputAtStart: agent.spend.output };
  agent.state = "running";
  agent.status = { text: "", step: 0 };
  agent.rev++;
}

export function clearFeed(agent) {
  agent.items = [];
  agent.streams.clear();
  agent.calls.clear();
  agent.changed.clear();
  agent.rev++;
}

function reply(agent, streamId) {
  const key = `r:${streamId}`;
  let item = agent.streams.get(key);
  if (!item) {
    item = push(agent, { kind: "reply", text: "", live: true });
    agent.streams.set(key, item);
  }
  return item;
}

function thought(agent, streamId) {
  const key = `t:${streamId}`;
  let item = agent.streams.get(key);
  if (!item) {
    item = push(agent, { kind: "thought", text: "", seconds: 0, live: true });
    agent.streams.set(key, item);
  }
  return item;
}

function settleStreams(agent) {
  for (const item of agent.streams.values()) {
    item.live = false;
    touch(agent, item);
  }
  agent.streams.clear();
}

function callFor(agent, ev) {
  if (ev.call_id && agent.calls.has(ev.call_id)) return agent.calls.get(ev.call_id);
  return [...agent.calls.values()].reverse().find((call) => call.step === ev.step && call.phase === "run");
}

function repeatsLastReply(agent, message) {
  const last = agent.items.findLast((item) => item.kind !== "note");
  return last?.kind === "reply" && !last.aside && last.text.trim() === String(message ?? "").trim();
}

// The answer itself is the last action of a response; the core sends no result for it, so it is no card.
const TERMINAL_ACTIONS = new Set(["final_answer", "report_to_user", "refuse"]);

const WRITES = new Set(["write_file", "append_file", "replace_in_file"]);

const lineCount = (text) => (text ? text.split("\n").length - (text.endsWith("\n") ? 1 : 0) : 0);

// call_stream carries whole new lines of a call's text while the model is still writing it; `line` is where they
// begin, so a piece that does not follow on is dropped and line 0 starts the field over (a retried request).
function feedStream(call, ev) {
  const have = ev.line === 0 ? "" : (call.stream[ev.field] ?? "");
  if (ev.line !== have.split("\n").length - 1) return;
  call.stream[ev.field] = have + ev.text;
  const replacing = call.actionType === "replace_in_file";
  call.added = lineCount(call.stream[replacing ? "new_text" : "content"]);
  call.removed = replacing ? lineCount(call.stream.old_text) : 0;
}

function noteWrite(agent, call) {
  if (!call.path) return;
  const was = agent.changed.get(call.path) ?? { added: 0, removed: 0, writes: 0 };
  agent.changed.set(call.path, {
    added: was.added + (call.added ?? 0),
    removed: was.removed + (call.removed ?? 0),
    writes: was.writes + 1,
  });
}

function applyEffects(agent, name, effects = {}) {
  if (effects.role) agent.role = effects.role;
  if (effects.effort !== undefined) agent.effort = effects.effort;
  if (effects.verbose !== undefined) agent.verbose = effects.verbose;
  if (effects.provider) agent.provider = effects.provider;
  if (effects.models) agent.models = effects.models;
  if (typeof effects.session === "string") {
    const switched = effects.changed === true || effects.session !== agent.session;
    agent.session = effects.session;
    if (switched && effects.changed) {
      clearFeed(agent);
      agent.closed = null;
      agent.title = "";
      agent.helpers.clear();
      agent.jobs.clear();
      agent.screen = null;
      agent.needsHistory = true;
    }
  }
  if (effects.title) agent.title = effects.title;
}

/** Folds one core event into the agent. `replay` marks journal events. */
export function reduce(agent, ev, { replay = false } = {}) {
  agent.rev++;
  switch (ev.type) {
    case "hello":
      agent.provider = ev.provider ?? agent.provider;
      agent.models = ev.models ?? agent.models;
      agent.role = ev.role ?? agent.role;
      agent.effort = ev.effort ?? agent.effort;
      agent.session = ev.session ?? agent.session;
      agent.state = "idle";
      return;
    case "manifest":
      agent.manifest = ev;
      return;
    case "session":
      agent.session = ev.session ?? agent.session;
      if (ev.title) agent.title = ev.title;
      agent.closed = ev.closed ? (ev.closed_notice ?? "") : null;
      return;
    case "session_title":
      if (!ev.session || ev.session === agent.session) agent.title = ev.title;
      return;
    case "session_ended":
      agent.closed = ev.closed_notice ?? "";
      return;
    case "route":
      agent.routedRole = ev.role;
      return;
    case "user":
      if (ev.text) {
        const goal = parseImageGoal(ev.text);
        push(agent, { kind: "user", text: goal.text, images: goal.images });
      }
      return;
    case "live_start":
      if (!replay) agent.state = "running";
      return;
    case "wake":
      agent.state = "running";
      return;
    case "status":
      if (!replay && !agent.turn.startedAt) agent.turn = { startedAt: Date.now(), waitingSince: Date.now(), outputAtStart: agent.spend.output };
      agent.status = { text: ev.user_message ?? "", step: ev.step ?? 0 };
      if (!replay && agent.state !== "paused") agent.state = "running";
      return;
    case "task_state":
      agent.taskState = ev.state;
      return;
    case "step": {
      settleStreams(agent);
      const known = agent.calls.get(ev.call_id || `step-${ev.step}`);
      // Only a card that call_begin or call_stream opened and no step has confirmed; an id that comes round again is a new call.
      const early = known && known.step === 0 && known.phase === "run" ? known : undefined;
      if (ev.user_message) {
        const words = push(agent, { kind: "reply", text: ev.user_message, live: false, aside: true });
        if (early) placeBefore(agent, words, early);
      }
      if (early) {
        Object.assign(early, { step: ev.step, actionType: ev.action_type, label: ev.action_description || ev.action_type, args: ev.action_args ?? {} });
        touch(agent, early);
        return;
      }
      if (TERMINAL_ACTIONS.has(ev.action_type)) return;
      const call = push(agent, {
        kind: "call",
        callId: ev.call_id || `step-${ev.step}`,
        step: ev.step,
        actionType: ev.action_type,
        label: ev.action_description || ev.action_type,
        args: ev.action_args ?? {},
        phase: "run",
        summary: "",
        output: "",
      });
      agent.calls.set(call.callId, call);
      return;
    }
    case "action_result": {
      if (!replay) agent.turn.waitingSince = Date.now();
      const call = callFor(agent, ev);
      if (!call) return;
      call.phase = ev.ok ? "ok" : "fail";
      call.summary = ev.summary ?? "";
      call.output = ev.output ?? call.output;
      call.exitCode = ev.exit_code ?? null;
      call.path = ev.path ?? call.args?.path ?? null;
      call.firstLine = ev.first_line ?? null;
      call.added = ev.lines_added ?? null;
      call.removed = ev.lines_removed ?? null;
      call.patch = ev.patch ?? null;
      if (ev.ok && WRITES.has(call.actionType)) noteWrite(agent, call);
      touch(agent, call);
      return;
    }
    case "call_begin": {
      if (replay || agent.calls.has(ev.call_id) || TERMINAL_ACTIONS.has(ev.tool)) return;
      const call = push(agent, { kind: "call", callId: ev.call_id, step: 0, actionType: ev.tool, label: ev.description || ev.path || ev.tool, args: { path: ev.path }, phase: "run", summary: "", output: "" });
      agent.calls.set(ev.call_id, call);
      return;
    }
    case "call_stream": {
      let call = agent.calls.get(ev.call_id);
      if (!call) {
        call = push(agent, { kind: "call", callId: ev.call_id, step: 0, actionType: ev.tool, label: ev.path || ev.tool, args: { path: ev.path }, phase: "run", summary: "", output: "", stream: {} });
        agent.calls.set(ev.call_id, call);
      }
      if (!call.stream || call.phase !== "run") return;
      feedStream(call, ev);
      touch(agent, call);
      return;
    }
    case "action_output": {
      const call = callFor(agent, ev);
      if (!call) return;
      call.output = ev.output;
      touch(agent, call);
      return;
    }
    case "stream_start":
      reply(agent, ev.stream_id);
      return;
    case "stream_chunk": {
      if (!replay) agent.turn.waitingSince = Date.now();
      const item = reply(agent, ev.stream_id);
      item.text = ev.text;
      touch(agent, item);
      return;
    }
    case "stream_commit": {
      const item = reply(agent, ev.stream_id);
      item.text = ev.text;
      item.live = false;
      agent.streams.delete(`r:${ev.stream_id}`);
      if (!item.text) agent.items.splice(agent.items.indexOf(item), 1);
      touch(agent, item);
      return;
    }
    case "reasoning_start":
      thought(agent, ev.stream_id);
      return;
    case "reasoning_chunk": {
      if (!replay) agent.turn.waitingSince = Date.now();
      const item = thought(agent, ev.stream_id);
      item.text = ev.text;
      touch(agent, item);
      return;
    }
    case "reasoning_commit": {
      const item = thought(agent, ev.stream_id);
      item.seconds = ev.duration_seconds ?? 0;
      item.live = false;
      agent.streams.delete(`t:${ev.stream_id}`);
      touch(agent, item);
      return;
    }
    case "message":
      if (ev.source === "notice") addNote(agent, ev.markdown);
      else push(agent, { kind: "reply", text: ev.markdown, live: false });
      return;
    case "plan":
      push(agent, { kind: "plan", text: ev.markdown });
      return;
    case "final":
      settleStreams(agent);
      if (repeatsLastReply(agent, ev.message)) return;
      if (ev.closed) push(agent, { kind: "reply", text: ev.message, live: false });
      else push(agent, { kind: "final", text: ev.message, outcome: ev.outcome ?? "done", paused: ev.paused === true });
      if (ev.paused && !replay) agent.state = "paused";
      return;
    case "error":
      push(agent, { kind: "error", text: ev.message });
      return;
    case "sandbox_notice":
      addNote(agent, ev.note);
      return;
    case "turn_end":
      settleStreams(agent);
      for (const call of agent.calls.values()) {
        if (call.phase !== "run") continue;
        call.phase = "fail";
        call.summary ||= "stopped";
        touch(agent, call);
      }
      agent.calls.clear();
      agent.status = { text: "", step: 0 };
      agent.turn = { startedAt: 0, waitingSince: 0, outputAtStart: 0 };
      if (!replay) agent.state = agent.state === "paused" || agent.taskState === "paused" ? "paused" : "idle";
      return;
    case "usage":
      if (ev.kind !== "turn") {
        agent.spend.input += ev.input_tokens ?? 0;
        agent.spend.output += ev.output_tokens ?? 0;
      }
      if (ev.context_limit) agent.context = { tokens: ev.context_tokens ?? 0, limit: ev.context_limit };
      return;
    case "context":
      agent.context = { tokens: ev.context_tokens ?? 0, limit: ev.context_limit ?? agent.context.limit };
      return;
    case "input_request":
      agent.dialogs.push(ev);
      agent.attention = true;
      return;
    case "input_closed":
      agent.dialogs = agent.dialogs.filter((d) => d.request_id !== ev.request_id);
      return;
    case "helper": {
      const was = agent.helpers.get(ev.agent_id);
      agent.helpers.set(ev.agent_id, { activity: [], ...was, ...ev, id: ev.agent_id, rev: (was?.rev ?? 0) + 1 });
      return;
    }
    case "helper_activity": {
      const helper = agent.helpers.get(ev.agent_id) ?? { id: ev.agent_id, agent_id: ev.agent_id, state: "running", task: "", summary: "", activity: [], rev: 0 };
      const entry = helper.activity.find((a) => a.call_id === ev.call_id);
      if (entry) Object.assign(entry, ev);
      else helper.activity.push({ ...ev });
      if (helper.activity.length > MAX_ACTIVITY) helper.activity.shift();
      helper.rev++;
      agent.helpers.set(ev.agent_id, helper);
      return;
    }
    case "job_started":
      agent.jobs.set(ev.job_id, { id: ev.job_id, command: ev.command ?? [], purpose: ev.purpose ?? "", state: "running", output: "", exitCode: null });
      return;
    case "job_output": {
      const job = agent.jobs.get(ev.job_id);
      if (job) job.output = ev.output;
      return;
    }
    case "job_finished": {
      const job = agent.jobs.get(ev.job_id);
      if (!job) return;
      job.state = ev.status;
      job.exitCode = ev.exit_code ?? null;
      job.output = ev.output ?? job.output;
      return;
    }
    case "screen":
      agent.screen = ev.state === "closed" ? null : ev;
      return;
    case "skill":
      addNote(agent, `skill ${ev.name || ev.skill} ${ev.state}`);
      return;
    case "artifact":
      push(agent, { kind: "artifact", name: ev.name, mime: ev.mime, caption: ev.caption, preview: ev.preview ?? "", path: ev.path });
      return;
    case "image_job":
      if (ev.state === "failed") addNote(agent, `${ev.name || "picture"}: ${ev.error}`, "error");
      return;
    case "command_result":
      agent.command = { name: ev.name, ok: ev.ok, message: ev.message, effects: ev.effects ?? {} };
      applyEffects(agent, ev.name, ev.effects);
      return;
    case "gone":
      agent.state = "gone";
      agent.dialogs = [];
      addNote(agent, `The core stopped: ${ev.message}`, "error");
      return;
    default:
      // Unknown event types are ignored on purpose (docs/UI_API.md §4).
      agent.rev--;
  }
}

/** Replays a journal page (oldest first) into an empty feed. */
export function replayHistory(agent, events) {
  for (const entry of events) reduce(agent, entry.event ?? entry, { replay: true });
  agent.streams.clear();
  agent.calls.clear();
}

/** Short words for the dock: what this agent is doing right now. */
export function summary(agent) {
  if (agent.dialogs.length) return "asks";
  if (agent.closed !== null) return "closed";
  return agent.state;
}

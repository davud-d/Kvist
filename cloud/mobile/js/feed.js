// What the agent says (the core's events, relayed unchanged by the cloud) folded into a conversation to show.
// Pure data in, data out: no DOM, so the same function draws a live turn and a history loaded from the server.
//
// The items are the web workspace's (cloud/workspace/js/reducer.js), one per thing said, thought or done: user, reply,
// final, thought, call, plan, error, note. The view groups them the way the web does (js/web/feed.js feedBlocks):
// a thought stands on its own, a run of calls is one block that folds. Each item has an id that never changes and a
// rev that rises whenever it changes, so the view redraws only what moved.

import { parseImageGoal } from "./envelope.js";

// The answer itself is the last action of a response; the core sends no result for it, so it is no card.
const TERMINAL_ACTIONS = new Set(["final_answer", "report_to_user", "refuse"]);
// What shows the model is alive: the wait that warms the status line's colour starts over.
const LIFE = new Set(["stream_chunk", "reasoning_chunk", "step", "action_result", "status"]);
// Commands the cloud's runner sends to keep a task in its conversation (runner/kvist_runner/task.py).
const RUNNER_COMMANDS = new Set(["new", "session", "resume"]);

export function createFeed() {
  return {
    items: [],
    nextId: 1,
    streams: new Map(),
    calls: new Map(),
    dialogs: [],
    // The agent's own line while a turn runs: {text, step}.
    status: { text: "", step: 0 },
    // The task behind the conversation as the server last said it ({status, reason, used_seconds, ...}); the
    // server's word, not the core's events, says whether the agent is working.
    task: null,
    rev: 0,
  };
}

function push(feed, item) {
  item.id = feed.nextId++;
  item.rev = 1;
  feed.items.push(item);
  feed.rev++;
  return item;
}

function touch(feed, item) {
  item.rev++;
  feed.rev++;
}

// The model's words belong above the card they introduce, which a stream may have opened already.
function placeBefore(feed, item, anchor) {
  feed.items.splice(feed.items.indexOf(item), 1);
  feed.items.splice(feed.items.indexOf(anchor), 0, item);
}

function reply(feed, streamId) {
  const key = `r:${streamId}`;
  let item = feed.streams.get(key);
  if (!item) {
    item = push(feed, { kind: "reply", text: "", live: true });
    feed.streams.set(key, item);
  }
  return item;
}

function thought(feed, streamId) {
  const key = `t:${streamId}`;
  let item = feed.streams.get(key);
  if (!item) {
    item = push(feed, { kind: "thought", text: "", seconds: 0, live: true });
    feed.streams.set(key, item);
  }
  return item;
}

function settleStreams(feed) {
  for (const item of feed.streams.values()) {
    item.live = false;
    touch(feed, item);
  }
  feed.streams.clear();
}

function callFor(feed, ev) {
  if (ev.call_id && feed.calls.has(ev.call_id)) return feed.calls.get(ev.call_id);
  return [...feed.calls.values()].reverse().find((call) => call.step === ev.step && call.phase === "run");
}

// The core appends a line ("Kvist closed the task's processes: ...") to an answer that was already streamed
// as a reply, so the final text is that reply plus a tail. Returns what of the final text is not on screen
// yet: "" when it is all there, the tail when only a tail is new, null when the answer was never shown.
// One italic line: the closing words the core appends. A longer tail is a different answer, not a footer.
const CLOSING_LINE = /^_[^\n]+_$/;

function unshownTail(feed, message) {
  const last = feed.items.findLast((item) => item.kind !== "note");
  if (!last || (last.kind !== "reply" && last.kind !== "final") || last.aside) return null;
  const shown = String(last.text).trim();
  const full = String(message ?? "").trim();
  if (!shown || !full) return null;
  if (shown.endsWith(full)) return "";
  if (!full.startsWith(shown)) return null;
  const tail = full.slice(shown.length).trim();
  return CLOSING_LINE.test(tail) ? tail : null;
}

// A tail is a status line, not a second answer: it is written as a note, without its markdown emphasis.
const asNote = (tail) => tail.replace(/^_+|_+$/g, "");

const lineCount = (text) => (text ? text.split("\n").length - (text.endsWith("\n") ? 1 : 0) : 0);

// call_stream carries the new words of a call's text while the model is still writing it; `line` is the line
// they begin on, so a piece that does not follow on is dropped.
function feedStream(call, ev) {
  const current = call.stream[ev.field] ?? "";
  const have = ev.line === 0 && current.includes("\n") ? "" : current;
  if (ev.line !== have.split("\n").length - 1) return;
  call.stream[ev.field] = have + ev.text;
  const replacing = call.actionType === "replace_in_file";
  call.added = lineCount(call.stream[replacing ? "new_text" : "content"]);
  call.removed = replacing ? lineCount(call.stream.old_text) : 0;
}

const newCall = (fields) => ({ kind: "call", step: 0, phase: "run", summary: "", output: "", ...fields });

/** Folds one core event into the feed. `replay` marks events loaded from history rather than live ones. */
export function reduce(feed, ev, { replay = false } = {}) {
  if (!replay && LIFE.has(ev.type)) feed.lastLife = Date.now();
  switch (ev.type) {
    case "user":
      if (!ev.text) return;
      if (!replay) feed.turnStartedAt = feed.lastLife = Date.now();
      push(feed, { kind: "user", ...parseImageGoal(ev.text) });
      return;
    case "status":
      feed.status = { text: ev.user_message ?? "", step: ev.step ?? 0 };
      feed.rev++;
      return;
    case "task_state":
      // Only "awaiting_input" leaves a question open; anything else means a question still showing is a leftover.
      if (ev.state !== "awaiting_input") feed.dialogs = [];
      feed.rev++;
      return;
    case "step": {
      settleStreams(feed);
      const known = feed.calls.get(ev.call_id || `step-${ev.step}`);
      // Only a card that call_begin or call_stream opened and no step has confirmed; an id that comes round again is a new call.
      const early = known && known.step === 0 && known.phase === "run" ? known : undefined;
      if (ev.user_message) {
        const words = push(feed, { kind: "reply", text: ev.user_message, live: false, aside: true });
        if (early) placeBefore(feed, words, early);
      }
      if (early) {
        Object.assign(early, { step: ev.step, actionType: ev.action_type, label: ev.action_description || ev.action_type, args: ev.action_args ?? {} });
        touch(feed, early);
        return;
      }
      if (TERMINAL_ACTIONS.has(ev.action_type)) return;
      const call = push(feed, newCall({
        callId: ev.call_id || `step-${ev.step}`, step: ev.step, actionType: ev.action_type,
        label: ev.action_description || ev.action_type, args: ev.action_args ?? {},
      }));
      feed.calls.set(call.callId, call);
      return;
    }
    case "call_begin": {
      if (replay || feed.calls.has(ev.call_id) || TERMINAL_ACTIONS.has(ev.tool)) return;
      const call = push(feed, newCall({ callId: ev.call_id, actionType: ev.tool, label: ev.description || ev.path || ev.tool, args: { path: ev.path } }));
      feed.calls.set(ev.call_id, call);
      return;
    }
    case "call_stream": {
      let call = feed.calls.get(ev.call_id);
      if (!call) {
        call = push(feed, newCall({ callId: ev.call_id, actionType: ev.tool, label: ev.path || ev.tool, args: { path: ev.path }, stream: {} }));
        feed.calls.set(ev.call_id, call);
      }
      if (!call.stream || call.phase !== "run") return;
      feedStream(call, ev);
      touch(feed, call);
      return;
    }
    case "action_result": {
      const call = callFor(feed, ev);
      if (!call) return;
      Object.assign(call, {
        phase: ev.ok ? "ok" : "fail", summary: ev.summary ?? "", output: ev.output ?? call.output,
        exitCode: ev.exit_code ?? null, path: ev.path ?? call.args?.path ?? null,
        added: ev.lines_added ?? null, removed: ev.lines_removed ?? null, patch: ev.patch ?? null,
      });
      touch(feed, call);
      return;
    }
    case "action_output": {
      const call = callFor(feed, ev);
      if (!call) return;
      call.output = ev.output;
      touch(feed, call);
      return;
    }
    case "stream_start":
      reply(feed, ev.stream_id);
      return;
    case "stream_chunk": {
      const item = reply(feed, ev.stream_id);
      // `append`: the next piece of the answer; without it (older cores) the whole answer.
      item.text = ev.append === true ? item.text + ev.text : ev.text;
      touch(feed, item);
      return;
    }
    case "stream_commit": {
      const item = reply(feed, ev.stream_id);
      item.text = ev.text;
      item.live = false;
      feed.streams.delete(`r:${ev.stream_id}`);
      // An empty commit is the end-of-session tag that is never shown.
      if (!item.text) feed.items.splice(feed.items.indexOf(item), 1);
      touch(feed, item);
      return;
    }
    case "reasoning_start":
      thought(feed, ev.stream_id);
      return;
    case "reasoning_chunk": {
      const item = thought(feed, ev.stream_id);
      item.text = ev.append === true ? item.text + ev.text : ev.text;
      touch(feed, item);
      return;
    }
    case "reasoning_commit": {
      const item = thought(feed, ev.stream_id);
      // The history has no pieces: there the end of a thought carries its words.
      if (typeof ev.text === "string" && ev.text) item.text = ev.text;
      item.seconds = ev.duration_seconds ?? 0;
      item.live = false;
      feed.streams.delete(`t:${ev.stream_id}`);
      touch(feed, item);
      return;
    }
    case "message":
      push(feed, ev.source === "notice" ? { kind: "note", text: ev.markdown, tone: "muted" } : { kind: "reply", text: ev.markdown, live: false });
      return;
    case "plan":
      push(feed, { kind: "plan", text: ev.markdown });
      return;
    case "final":
      settleStreams(feed);
      feed.dialogs = [];
      const tail = unshownTail(feed, ev.message);
      if (tail !== null) {
        if (tail) push(feed, { kind: "note", text: asNote(tail), tone: "muted" });
        return;
      }
      push(feed, { kind: "final", text: ev.message, outcome: ev.outcome ?? "done", paused: ev.paused === true });
      return;
    case "error":
      push(feed, { kind: "error", text: ev.message });
      return;
    case "turn_end":
      settleStreams(feed);
      feed.dialogs = [];
      for (const call of feed.calls.values()) {
        if (call.phase !== "run") continue;
        call.phase = "fail";
        call.summary ||= "stopped";
        touch(feed, call);
      }
      feed.calls.clear();
      feed.status = { text: "", step: 0 };
      feed.rev++;
      return;
    case "input_request":
      // The core sends a dialog again to a frontend that takes over; it is the same dialog.
      feed.dialogs = feed.dialogs.filter((d) => d.request_id !== ev.request_id);
      feed.dialogs.push(ev);
      feed.rev++;
      return;
    case "input_closed":
      feed.dialogs = feed.dialogs.filter((d) => d.request_id !== ev.request_id);
      feed.rev++;
      return;
    case "command_result":
      // What the runner says about switching sessions is plumbing, not news.
      if (RUNNER_COMMANDS.has(ev.name) || !(ev.message || ev.text)) return;
      if (ev.ok === false) push(feed, { kind: "note", text: String(ev.message ?? ev.text), tone: "error" });
      else push(feed, { kind: "reply", text: String(ev.message ?? ev.text), live: false, aside: true });
      return;
    default:
  }
}

/** What the turn is doing now, for the line above the composer: "waiting" (for the person) | "working" | "". */
export function working(feed) {
  const status = feed.task?.status;
  if (status === "waiting_for_user" || feed.dialogs.length) return "waiting";
  return ["queued", "starting", "running"].includes(status) ? "working" : "";
}

/**
 * What the agent is at, for the orb and the status line: null when it is not working. `running` is the call in progress,
 * `speaking` that a reply is being written, `silentSince` since when the model has said nothing, `startedAt` the turn's
 * start. The server's task status says whether it works; the events say what at.
 */
export function feedActivity(feed) {
  if (!["running", "starting", "queued"].includes(feed.task?.status)) return null;
  const turn = feed.items.findLastIndex((item) => item.kind === "user");
  const mine = feed.items.slice(turn + 1);
  const last = feed.items.at(-1);
  const startedAt = feed.turnStartedAt ?? (feed.turnStartedAt = Date.now());
  return {
    running: mine.findLast((item) => item.kind === "call" && item.phase === "run") ?? null,
    speaking: last?.kind === "reply" && last.live === true,
    silentSince: feed.lastLife ?? startedAt,
    startedAt,
    request: feed.items.findLast((item) => item.kind === "user")?.text ?? "",
  };
}

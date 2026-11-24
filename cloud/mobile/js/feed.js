// What the agent says (the core's events, relayed unchanged by the cloud) folded into a conversation to show.
// Pure data in, data out: no DOM, so the same function draws a live turn and a history loaded from the server.
//
// The feed is a list of items; each has an id that never changes and a rev that rises whenever it changes, so the
// view redraws only what moved. Everything the agent does between two things the person reads (a reply, their own
// message) is gathered into one "work" item, a collapsed ledger of steps, instead of a long run of cards.

const TERMINAL = new Set(["final_answer", "report_to_user", "refuse"]);
const WRITES = new Set(["write_file", "append_file", "replace_in_file"]);
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

/** The work item the next step belongs to: the last item when it is one, else a new one. */
function work(feed) {
  const last = feed.items.at(-1);
  if (last?.kind === "work") return last;
  return push(feed, { kind: "work", calls: [], thoughts: [], live: true, startedAt: Date.now() });
}

function closeWork(feed) {
  for (const item of feed.items) {
    if (item.kind === "work" && item.live) {
      item.live = false;
      item.endedAt ??= Date.now();
      touch(feed, item);
    }
  }
}

function reply(feed, streamId) {
  const key = `r:${streamId}`;
  let item = feed.streams.get(key);
  if (!item) {
    closeWork(feed);
    item = push(feed, { kind: "reply", text: "", live: true });
    feed.streams.set(key, item);
  }
  return item;
}

function thought(feed, streamId) {
  const key = `t:${streamId}`;
  let item = feed.streams.get(key);
  if (!item) {
    item = { text: "", seconds: 0, live: true };
    const group = work(feed);
    group.thoughts.push(item);
    feed.streams.set(key, item);
    touch(feed, group);
  }
  return item;
}

function settle(feed) {
  for (const item of feed.streams.values()) item.live = false;
  feed.streams.clear();
  feed.rev++;
  for (const item of feed.items) if (item.kind === "reply" && item.live) touch(feed, item), (item.live = false);
}

const callFor = (feed, ev) =>
  (ev.call_id && feed.calls.get(ev.call_id)) || [...feed.calls.values()].reverse().find((c) => c.step === ev.step && c.phase === "run");

function openCall(feed, fields) {
  const call = { phase: "run", summary: "", output: "", step: 0, ...fields };
  work(feed).calls.push(call);
  feed.calls.set(call.callId, call);
  touch(feed, feed.items.at(-1));
  return call;
}

const sameAsLast = (feed, text) => {
  const last = [...feed.items].reverse().find((item) => item.kind === "reply" || item.kind === "final");
  return last && String(last.text).trim() === String(text).trim();
};

/** Folds one core event into the feed. `replay` marks events loaded from history rather than live ones. */
export function reduce(feed, ev, { replay = false } = {}) {
  switch (ev.type) {
    case "user":
      if (!ev.text) return;
      closeWork(feed);
      push(feed, { kind: "user", text: ev.text });
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
      settle(feed);
      if (ev.user_message) {
        closeWork(feed);
        push(feed, { kind: "reply", text: ev.user_message, live: false });
      }
      const known = feed.calls.get(ev.call_id || `step-${ev.step}`);
      const early = known && known.step === 0 && known.phase === "run" ? known : undefined;
      if (early) {
        Object.assign(early, { step: ev.step, actionType: ev.action_type, label: ev.action_description || ev.action_type });
        feed.rev++;
        return;
      }
      if (TERMINAL.has(ev.action_type)) return;
      openCall(feed, {
        callId: ev.call_id || `step-${ev.step}`, step: ev.step, actionType: ev.action_type,
        label: ev.action_description || ev.action_type, args: ev.action_args ?? {},
      });
      return;
    }
    case "call_begin":
      if (replay || feed.calls.has(ev.call_id) || TERMINAL.has(ev.tool)) return;
      openCall(feed, { callId: ev.call_id, actionType: ev.tool, label: ev.description || ev.path || ev.tool, args: { path: ev.path } });
      return;
    case "action_result": {
      const call = callFor(feed, ev);
      if (!call) return;
      Object.assign(call, {
        phase: ev.ok ? "ok" : "fail", summary: ev.summary ?? "", output: ev.output ?? call.output,
        exitCode: ev.exit_code ?? null, path: ev.path ?? call.args?.path ?? null,
        added: ev.lines_added ?? null, removed: ev.lines_removed ?? null,
      });
      call.wrote = Boolean(ev.ok && WRITES.has(call.actionType));
      feed.rev++;
      const group = feed.items.findLast((item) => item.kind === "work");
      if (group) touch(feed, group);
      return;
    }
    case "action_output": {
      const call = callFor(feed, ev);
      if (call) {
        call.output = ev.output;
        feed.rev++;
      }
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
      feed.rev++;
      return;
    }
    case "reasoning_commit": {
      const item = thought(feed, ev.stream_id);
      // The history has no pieces: there the end of a thought carries its words.
      if (typeof ev.text === "string" && ev.text) item.text = ev.text;
      item.seconds = ev.duration_seconds ?? 0;
      item.live = false;
      feed.streams.delete(`t:${ev.stream_id}`);
      feed.rev++;
      return;
    }
    case "message":
      closeWork(feed);
      push(feed, ev.source === "notice" ? { kind: "note", text: ev.markdown } : { kind: "reply", text: ev.markdown, live: false });
      return;
    case "plan":
      closeWork(feed);
      push(feed, { kind: "plan", text: ev.markdown });
      return;
    case "final":
      settle(feed);
      closeWork(feed);
      feed.dialogs = [];
      if (sameAsLast(feed, ev.message)) return;
      push(feed, { kind: "reply", text: ev.message, live: false, outcome: ev.outcome ?? "done" });
      return;
    case "error":
      closeWork(feed);
      push(feed, { kind: "error", text: ev.message });
      return;
    case "turn_end":
      settle(feed);
      closeWork(feed);
      feed.dialogs = [];
      for (const call of feed.calls.values()) {
        if (call.phase !== "run") continue;
        call.phase = "fail";
        call.summary ||= "stopped";
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
      if (RUNNER_COMMANDS.has(ev.name)) return;
      if (ev.message || ev.text) {
        closeWork(feed);
        push(feed, { kind: "note", text: String(ev.message ?? ev.text) });
      }
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

/** The steps of a work item as the ledger counts them. */
export function tally(group) {
  const done = group.calls.filter((c) => c.phase === "ok").length;
  const failed = group.calls.filter((c) => c.phase === "fail").length;
  const running = group.calls.find((c) => c.phase === "run") ?? null;
  const files = new Set(group.calls.filter((c) => c.wrote && c.path).map((c) => c.path));
  return { total: group.calls.length, done, failed, running, files: files.size };
}

/** The glyph family for an action: what kind of thing it touched. */
export function actionKind(type) {
  if (/^(run_|shell|command|exec)/.test(type ?? "") || type === "run_command") return "terminal";
  if (/(write|append|replace|edit|patch)/.test(type ?? "")) return "edit";
  if (/(read|list|glob|open_file|view)/.test(type ?? "")) return "file";
  if (/(search|grep|find)/.test(type ?? "")) return "search";
  if (/(web|fetch|http|browser|url)/.test(type ?? "")) return "globe";
  return "step";
}

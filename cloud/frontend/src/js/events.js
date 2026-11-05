// Kvist's Mode B events folded into what the task screen shows: one group per step of the agent
// (one model response: what it thought, what it said, the calls it made and their results).
// Pure: no DOM, so node --test covers it.

const TERMINAL_ACTIONS = new Set(["final_answer", "report_to_user", "refuse"]);

/** A group with nothing to show (a response that only answered) is not drawn. */
export function isEmpty(group) {
  return !group.reasoning && !group.answer && !group.say && !group.plan && !group.actions.length && !group.notes.length;
}

export function initialFeed() {
  return { groups: [], open: null, input: null, final: null, live: "", seen: new Set() };
}

function newGroup(feed) {
  const group = { n: feed.groups.length + 1, batch: "", reasoning: null, answer: null, say: "", plan: "",
    actions: [], notes: [], version: 0 };
  feed.groups.push(group);
  feed.open = group;
  return group;
}

/** The group a new piece of a response belongs to: a response that already made calls is over. */
function current(feed) {
  const group = feed.open;
  if (!group || group.actions.length) return newGroup(feed);
  return group;
}

function action(group, callId) {
  let found = group.actions.find((item) => item.callId === callId);
  if (!found) {
    found = { callId, type: "", description: "", args: {}, running: true, live: "", result: null };
    group.actions.push(found);
  }
  return found;
}

function touch(group) {
  if (group) group.version += 1;
  return group;
}

/** Apply one event; returns the group that changed (or null when only the feed's top changed). */
export function apply(feed, event) {
  const type = event && event.type;
  switch (type) {
    case "reasoning_start": {
      const group = current(feed);
      group.reasoning = { text: "", seconds: null, live: true };
      return touch(group);
    }
    case "reasoning_chunk": {
      const group = feed.open || current(feed);
      group.reasoning = { ...(group.reasoning || { seconds: null }), text: event.text || "", live: true };
      return touch(group);
    }
    case "reasoning_commit": {
      const group = feed.open;
      if (!group || !group.reasoning) return null;
      group.reasoning = { ...group.reasoning, live: false, seconds: event.duration_seconds ?? null };
      return touch(group);
    }
    case "stream_start": {
      const group = current(feed);
      group.answer = { text: "", live: true };
      return touch(group);
    }
    case "stream_chunk":
    case "stream_commit": {
      const group = feed.open || current(feed);
      group.answer = { text: event.text || "", live: type === "stream_chunk" };
      return touch(group);
    }
    case "plan": {
      const group = current(feed);
      group.plan = event.markdown || "";
      return touch(group);
    }
    case "call_begin": {
      let group = feed.open;
      if (!group || (group.batch && !group.actions.some((item) => item.running))) group = current(feed);
      const item = action(group, event.call_id);
      item.type = event.tool || item.type;
      item.description = event.description || item.description;
      return touch(group);
    }
    case "step": {
      // The answer itself is not an action (no action_result ever follows it); it shows as the result.
      if (TERMINAL_ACTIONS.has(event.action_type)) return null;
      let group = feed.open;
      const batch = event.batch_id || "";
      const known = group && group.actions.some((item) => item.callId === event.call_id);
      if (!group || (!known && group.batch && batch && group.batch !== batch)) group = newGroup(feed);
      if (!group.batch) group.batch = batch;
      if (event.user_message) group.say = event.user_message;
      const item = action(group, event.call_id || `step-${event.step}`);
      item.type = event.action_type || item.type;
      item.description = event.action_description || item.description || item.type;
      item.args = event.action_args || {};
      if (event.reasoning_seconds && !group.reasoning) {
        group.reasoning = { text: "", seconds: event.reasoning_seconds, live: false };
      }
      return touch(group);
    }
    case "action_output": {
      const group = feed.groups.find((g) => g.actions.some((item) => item.callId === event.call_id));
      if (!group) return null;
      action(group, event.call_id).live = event.output || "";
      return touch(group);
    }
    case "action_result": {
      const callId = event.call_id || `step-${event.step}`;
      const group = feed.groups.find((g) => g.actions.some((item) => item.callId === callId)) || current(feed);
      const item = action(group, callId);
      item.type = item.type || event.action_type;
      item.running = false;
      item.result = event;
      return touch(group);
    }
    case "message":
    case "error":
    case "job_started":
    case "job_finished":
    case "job_notice": {
      const group = feed.open || newGroup(feed);
      group.notes.push(event);
      return touch(group);
    }
    case "input_request":
      feed.input = event;
      return null;
    case "input_closed":
      if (feed.input && feed.input.request_id === event.request_id) feed.input = null;
      return null;
    case "status":
      feed.live = event.user_message || "";
      return null;
    case "final":
      feed.final = event;
      feed.input = null;
      feed.live = "";
      return null;
    case "turn_end":
      for (const group of feed.groups) for (const item of group.actions) item.running = false;
      feed.open = null;
      feed.input = null;
      feed.live = "";
      return null;
    default:
      return null;
  }
}

/** Minimal Markdown → blocks: fenced code and paragraphs; inline `code` is split out by the caller. */
export function blocks(markdown) {
  const out = [];
  const lines = String(markdown || "").split("\n");
  let paragraph = [];
  let code = null;
  const flush = () => {
    if (paragraph.length) out.push({ kind: "p", text: paragraph.join("\n") });
    paragraph = [];
  };
  for (const line of lines) {
    if (line.startsWith("```")) {
      if (code) {
        out.push({ kind: "code", text: code.join("\n") });
        code = null;
      } else {
        flush();
        code = [];
      }
      continue;
    }
    if (code) code.push(line);
    else if (!line.trim()) flush();
    else paragraph.push(line);
  }
  if (code) out.push({ kind: "code", text: code.join("\n") });
  flush();
  return out;
}

/** "a `b` c" → [["text","a "],["code","b"],["text"," c"]] */
export function inline(text) {
  return String(text).split(/(`[^`]+`)/).filter(Boolean)
    .map((part) => (part.startsWith("`") && part.endsWith("`") ? ["code", part.slice(1, -1)] : ["text", part]));
}

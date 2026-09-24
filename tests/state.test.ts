import assert from "node:assert/strict"
import { test } from "node:test"

import type { CoreEvent, JournalEvent } from "../src/protocol.js"
import { actionTarget, answerFromEnd, exploreSummary, initialState, reduce, type State } from "../src/state.js"

// Stands in for the translation half of the core's bilingual messages, written
// with escapes so no Russian text lives in this file.
const TR = "\u043f\u0435\u0440\u0435\u0432\u043e\u0434"

function feed(...events: CoreEvent[]): State {
  return events.reduce((state, event) => reduce(state, { type: "event", event, now: 1000 }), initialState())
}

const hello: CoreEvent = { type: "hello", protocol_version: 1, provider: "p", models: { fast: "m" }, role: "coder", session: "s" }

test("hello fills the header info and prints the banner once", () => {
  const state = feed(hello, hello)
  assert.deepEqual(state.info, { provider: "p", model: "m", role: "coder", session: "s" })
  assert.equal(state.items.filter((item) => item.kind === "banner").length, 1)
})

test("a streamed reply is committed once and the live block is cleared", () => {
  const state = feed(
    { type: "stream_start", stream_id: "a" },
    { type: "stream_chunk", stream_id: "a", text: "Hel" },
    { type: "stream_chunk", stream_id: "a", text: "Hello" },
  )
  assert.equal(state.live?.answer, "Hello")
  const done = reduce(state, { type: "event", event: { type: "stream_commit", stream_id: "a", text: "Hello" } })
  assert.equal(done.live, null)
  assert.deepEqual(done.items.map((i) => i.kind), ["answer"])
})

test("final is not shown twice when the same text was just streamed", () => {
  const state = feed(
    { type: "stream_commit", stream_id: "a", text: "same" },
    { type: "final", message: "same ", user_messages: [] },
  )
  assert.equal(state.items.length, 1)
})

test("reasoning: live thinking, then a committed thought when it took time or said something", () => {
  const thinking = feed(
    { type: "reasoning_start", stream_id: "a" },
    { type: "reasoning_chunk", stream_id: "a", text: "hmm" },
  )
  assert.equal(thinking.live?.thinking, true)
  assert.equal(thinking.live?.reasoning, "hmm")
  const quick = reduce(thinking, { type: "event", event: { type: "reasoning_commit", stream_id: "a", duration_seconds: 0.2 } })
  assert.equal(quick.items.length, 1)
  const silent = feed(
    { type: "reasoning_start", stream_id: "a" },
    { type: "reasoning_commit", stream_id: "a", duration_seconds: 0.2 },
  )
  assert.equal(silent.items.length, 0)
  const slow = reduce(thinking, { type: "event", event: { type: "reasoning_commit", stream_id: "a", duration_seconds: 12 } })
  assert.deepEqual(slow.items.map((i) => i.kind), ["thought"])
  assert.equal(slow.items[0]?.kind === "thought" && slow.items[0].text, "hmm")
  assert.equal(slow.live?.thinking, false)
})

test("a step right after a streamed reasoning_commit does not repeat it as a second thought", () => {
  const withStream = feed(
    { type: "reasoning_start", stream_id: "a" },
    { type: "reasoning_chunk", stream_id: "a", text: "checking the cache logic" },
    { type: "reasoning_commit", stream_id: "a", duration_seconds: 3 },
    stepEvent("write_file", { path: "a.py" }, { thought: "Cache logic needs a guard", user_message: "" }),
  )
  assert.deepEqual(withStream.items.map((i) => i.kind), ["thought"])
})

test("a step with its own thought but no preceding reasoning stream still shows it", () => {
  const state = feed(stepEvent("write_file", { path: "a.py" }, { thought: "Straightforward edit", user_message: "" }))
  assert.deepEqual(state.items.map((i) => i.kind), ["thought"])
})

test("a step's reasoning_seconds alone (no streamed chunks) still shows a thought", () => {
  const state = feed(stepEvent("write_file", { path: "a.py" }, { thought: "", user_message: "", reasoning_seconds: 4 }))
  assert.deepEqual(state.items.map((i) => i.kind), ["thought"])
})

test("a later, unrelated step is unaffected by an earlier reasoning_commit once another event lands between", () => {
  const state = feed(
    { type: "reasoning_start", stream_id: "a" },
    { type: "reasoning_commit", stream_id: "a", duration_seconds: 3 },
    { type: "plan", goal: "g", complexity: "simple", plan: [], success_criteria: [], risks: [] },
    stepEvent("write_file", { path: "a.py" }, { thought: "Independent step", user_message: "" }),
  )
  assert.deepEqual(state.items.map((i) => i.kind), ["thought", "plan", "thought"])
})

test("a step's user_message is shown as a say line before its action, but not for explore-kind steps", () => {
  const write = feed(stepEvent("write_file", { path: "a.py" }, { user_message: "Writing the fix" }))
  assert.deepEqual(write.items.map((i) => i.kind), ["say"])
  assert.equal(write.items[0]?.kind === "say" && write.items[0].text, "Writing the fix")

  const read = feed(stepEvent("read_file", { path: "a.py" }, { user_message: "Reading the target file" }))
  assert.deepEqual(read.items.map((i) => i.kind), [])
})

test("usage sums spend but replaces context size, and keeps it on a zero", () => {
  const usage = (input: number, context: number, limit: number): CoreEvent => ({
    type: "usage", input_tokens: input, output_tokens: 1, context_tokens: context, context_limit: limit, model: "m", kind: "action", step: 1,
  })
  const state = feed(usage(100, 500, 9000), usage(100, 800, 9000), usage(50, 0, 0))
  assert.equal(state.usage.input, 250)
  assert.equal(state.usage.output, 3)
  assert.equal(state.usage.context, 800)
  assert.equal(state.usage.limit, 9000)
})

test("a turn runs from submit until turn_end, even without a final", () => {
  let state = reduce(initialState(), { type: "submit", text: "hi", now: 5 })
  assert.equal(state.running, true)
  state = reduce(state, { type: "event", event: { type: "stream_commit", stream_id: "a", text: "hey" } })
  assert.equal(state.running, true)
  state = reduce(state, { type: "event", event: { type: "turn_end" } })
  assert.equal(state.running, false)
  assert.equal(state.startedAt, null)
})

test("input_request is pending until answered; turn_end drops a stale one", () => {
  const request: CoreEvent = { type: "input_request", request_id: "r", kind: "confirm", title: "t", body: "b", options: null, current_value: "", help_text: "" }
  let state = feed(request)
  assert.equal(state.pending?.request_id, "r")
  assert.equal(reduce(state, { type: "answered" }).pending, null)
  state = reduce(state, { type: "event", event: { type: "turn_end" } })
  assert.equal(state.pending, null)
})

test("unknown event types are ignored", () => {
  const state = feed({ type: "quantum_flux" } as unknown as CoreEvent)
  assert.deepEqual(state.items, [])
})

test("an unexpected core exit is reported; an expected one is silent", () => {
  const crashed = reduce(initialState(), { type: "core_exit", expected: false, code: 3 })
  assert.match(JSON.stringify(crashed.items), /exit code 3/)
  assert.equal(reduce(initialState(), { type: "core_exit", expected: true, code: 0 }).items.length, 0)
})

test("actionTarget picks the argument that names the subject", () => {
  assert.equal(actionTarget({ path: "a.py" }), "a.py")
  assert.equal(actionTarget({ command: ["git", "status"] }), "git status")
  assert.equal(actionTarget({ paths: ["a", "b"] }), "a, b")
  assert.equal(actionTarget({}), "")
})

test("clear empties the transcript and bumps the epoch so Static remounts", () => {
  const state = reduce(feed(hello), { type: "clear" })
  assert.equal(state.items.length, 0)
  assert.equal(state.epoch, 1)
})

function stepEvent(action_type: string, action_args: Record<string, unknown>, extra: Partial<Extract<CoreEvent, { type: "step" }>> = {}): CoreEvent {
  return { type: "step", step: 1, plan_step: "inspect", thought: "", user_message: "", action_type, action_args, reasoning_seconds: 0, ...extra }
}

test("read-only steps grow one live group instead of printing a line each", () => {
  const state = feed(
    stepEvent("read_file", { path: "a" }, { thought: "routine note" }),
    stepEvent("search_code", { query: "x" }),
    stepEvent("read_multiple_files", { paths: ["b", "c"] }),
    stepEvent("list_tree", { dir: "." }),
    stepEvent("git_status", {}),
  )
  assert.deepEqual(state.items, [])
  assert.deepEqual(state.exploring, { reads: 3, searches: 1, lists: 1, git: 1 })
})

test("any other item commits the group as a single explored item, in order", () => {
  const state = feed(
    stepEvent("read_file", { path: "a" }),
    stepEvent("find_files", { pattern: "*.py" }),
    stepEvent("write_file", { path: "a" }, { plan_step: "edit" }),
    stepEvent("read_file", { path: "a" }),
    { type: "turn_end" },
  )
  assert.deepEqual(state.items.map((i) => i.kind), ["explored", "step", "explored"])
  assert.equal(state.exploring, null)
})

test("real thinking time breaks a group; a routine note on a read does not", () => {
  const state = feed(
    stepEvent("read_file", { path: "a" }),
    stepEvent("read_file", { path: "b" }, { thought: "now I see it", reasoning_seconds: 1.5 }),
    { type: "turn_end" },
  )
  assert.deepEqual(state.items.map((i) => i.kind), ["explored", "thought", "explored"])
})

test("a non-read step shows its thought before itself, even with no thinking time", () => {
  const state = feed(stepEvent("run_shell", { command: ["ls"] }, { thought: "list it", reasoning_seconds: 0 }), { type: "turn_end" })
  assert.deepEqual(state.items.map((i) => i.kind), ["thought", "step"])
  const bare = feed(stepEvent("run_shell", { command: ["ls"] }), { type: "turn_end" })
  assert.deepEqual(bare.items.map((i) => i.kind), ["step"])
})

test("a user submit, a core crash and a clear all settle or drop the group", () => {
  const grown = feed(stepEvent("read_file", { path: "a" }))
  assert.deepEqual(reduce(grown, { type: "submit", text: "next" }).items.map((i) => i.kind), ["explored", "user"])
  assert.deepEqual(reduce(grown, { type: "core_exit", expected: true, code: 0 }).items.map((i) => i.kind), ["explored"])
  const cleared = reduce(grown, { type: "clear" })
  assert.equal(cleared.exploring, null)
  assert.deepEqual(cleared.items, [])
})

test("exploreSummary counts and pluralises in a fixed order", () => {
  assert.equal(exploreSummary({ reads: 1, searches: 2, lists: 0, git: 0 }), "1 read, 2 searches")
  assert.equal(exploreSummary({ reads: 0, searches: 0, lists: 1, git: 2 }), "1 listing, 2 git checks")
})

test("route sets the turn's role; a new submit or a new core clears it", () => {
  let state = feed(hello, { type: "route", role: "reader", source: "router" })
  assert.equal(state.info.routedRole, "reader")
  assert.equal(state.info.role, "coder")
  state = reduce(state, { type: "submit", text: "again" })
  assert.equal(state.info.routedRole, undefined)
  state = feed(hello, { type: "route", role: "reader" }, hello)
  assert.equal(state.info.routedRole, undefined)
})

function result(step: number, action_type: string, fields: Partial<Extract<CoreEvent, { type: "action_result" }>> = {}): CoreEvent {
  return { type: "action_result", step, action_type, ok: true, summary: "", output: "", exit_code: null, path: null, first_line: null, ...fields }
}

test("a running action waits in the live area and commits once, with its result", () => {
  const started = feed(stepEvent("run_shell", { command: ["npm", "test"] }, { step: 3, plan_step: "verify" }))
  assert.deepEqual(started.items, [])
  assert.equal(started.inflight?.action, "run_shell")
  const done = reduce(started, { type: "event", event: result(3, "run_shell", { summary: "Command exited with code 0", output: "ok", exit_code: 0 }) })
  assert.equal(done.inflight, null)
  assert.deepEqual(done.items.map((i) => i.kind), ["tool"])
  const card = done.items[0]
  assert.equal(card?.kind === "tool" && card.result.output, "ok")
  assert.deepEqual(card?.kind === "tool" && card.args.command, ["npm", "test"])
})

test("a step that never gets a result is committed as a plain step line", () => {
  for (const later of [stepEvent("write_file", { path: "b" }, { step: 2 }), { type: "turn_end" } as CoreEvent, { type: "final", message: "x", user_messages: [] } as CoreEvent]) {
    const state = feed(stepEvent("write_file", { path: "a" }), later, { type: "turn_end" })
    assert.equal(state.items[0]?.kind, "step")
    assert.equal(state.inflight, null)
  }
})

test("a result for an unknown step is ignored", () => {
  const state = feed(stepEvent("write_file", { path: "a" }, { step: 1 }), result(9, "write_file"))
  assert.deepEqual(state.items, [])
  assert.equal(state.inflight?.step, 1)
})

test("a card keeps its place after the read group that came before it", () => {
  const state = feed(
    stepEvent("read_file", { path: "a" }, { step: 1 }),
    result(1, "read_file"),
    stepEvent("replace_in_file", { path: "a", old_text: "x", new_text: "y" }, { step: 2 }),
    result(2, "replace_in_file", { path: "a", first_line: 4 }),
  )
  assert.deepEqual(state.items.map((i) => i.kind), ["explored", "tool"])
})

test("a failed read ends its group, is not counted in it, and gets a red line", () => {
  const state = feed(
    stepEvent("read_file", { path: "a" }, { step: 1 }),
    result(1, "read_file"),
    stepEvent("read_file", { path: "gone.py" }, { step: 2 }),
    result(2, "read_file", { ok: false, summary: "Failed: file not found", output: "file not found" }),
    stepEvent("read_file", { path: "b" }, { step: 3 }),
    { type: "turn_end" },
  )
  assert.deepEqual(state.items.map((i) => i.kind), ["explored", "note", "explored"])
  const [first, note, last] = state.items
  assert.equal(first?.kind === "explored" && first.counts.reads, 1)
  assert.match(note?.kind === "note" ? note.text : "", /gone\.py: Failed: file not found/)
  assert.equal(last?.kind === "explored" && last.counts.reads, 1)
})

test("a failed read with nothing before it leaves no empty group", () => {
  const state = feed(
    stepEvent("read_file", { path: "gone" }, { step: 1 }),
    result(1, "read_file", { ok: false, summary: "Failed: nope" }),
  )
  assert.equal(state.exploring, null)
  assert.deepEqual(state.items.map((i) => i.kind), ["note"])
})

test("git_diff is a card, not part of the explored group", () => {
  const state = feed(stepEvent("git_diff", {}), result(1, "git_diff", { output: "+x" }))
  assert.deepEqual(state.items.map((i) => i.kind), ["tool"])
  assert.equal(state.exploring, null)
})

test("clear drops a pending action", () => {
  const state = reduce(feed(stepEvent("run_shell", { command: "ls" })), { type: "clear" })
  assert.equal(state.inflight, null)
})

const cmdResult = (fields: Partial<Extract<CoreEvent, { type: "command_result" }>>): CoreEvent => ({
  type: "command_result",
  name: "role",
  ok: true,
  message: "",
  effects: {},
  ...fields,
})

const notes = (state: State) => state.items.filter((item) => item.kind === "note").map((item) => (item.kind === "note" ? `${item.tone}:${item.text}` : ""))

test("a command locks the UI until its command_result, and turn_end is not needed", () => {
  const started = reduce(feed(hello), { type: "command_start", name: "models" })
  assert.equal(started.command, "models")
  assert.equal(started.running, false)
  const asking = reduce(started, {
    type: "event",
    event: { type: "input_request", request_id: "c1", kind: "choice", title: "t", body: "", options: [], current_value: "", help_text: "" },
  })
  assert.equal(asking.pending?.request_id, "c1")
  const done = reduce(asking, { type: "event", event: cmdResult({ name: "models", message: `Models updated. / ${TR}`, effects: { models: { fast: "m2" } } }) })
  assert.equal(done.command, null)
  assert.equal(done.pending, null)
  assert.equal(done.info.model, "m2")
})

test("role and effort effects update the header line; a routed role is forgotten", () => {
  const routed = feed(hello, { type: "route", role: "reader" })
  assert.equal(routed.info.routedRole, "reader")
  const role = reduce(routed, { type: "event", event: cmdResult({ message: `Role set to: coder / ${TR}`, effects: { role: "coder" } }) })
  assert.equal(role.info.role, "coder")
  assert.equal(role.info.routedRole, undefined)
  const effort = reduce(role, { type: "event", event: cmdResult({ name: "effort", message: `Reasoning effort set to: high / ${TR}`, effects: { effort: "high", advisory: "Maps to the high tier." } }) })
  assert.equal(effort.effort, "high")
  assert.deepEqual(notes(effort), ["info:Role set to: coder", "info:Reasoning effort set to: high", "info:Maps to the high tier."])
})

test("the message follows the request language", () => {
  const state = { ...feed(hello), lang: "ru" as const }
  const next = reduce(state, { type: "event", event: cmdResult({ message: `Role set to: coder / ${TR}` }) })
  assert.deepEqual(notes(next), ["info:Role set to: coder"])
})

test("a cancelled dialog says nothing and changes nothing", () => {
  const state = reduce(feed(hello), { type: "command_start", name: "role" })
  const next = reduce(state, { type: "event", event: cmdResult({ message: `Cancelled / ${TR}`, effects: { cancelled: true } }) })
  assert.equal(next.command, null)
  assert.equal(next.items.length, state.items.length)
  assert.equal(next.info.role, state.info.role)
})

test("a failed command is a red note with the core's message", () => {
  const next = reduce(feed(hello), { type: "event", event: cmdResult({ ok: false, message: `Unknown command: x / ${TR}` }) })
  assert.deepEqual(notes(next), ["error:Unknown command: x"])
  assert.deepEqual(notes(reduce(feed(hello), { type: "event", event: cmdResult({ ok: false, message: "" }) })), ["error:/role failed"])
})

test("models and session effects reach the header; warnings become notes", () => {
  const next = feed(
    hello,
    cmdResult({ name: "models", message: `Models updated. / ${TR}`, effects: { provider: "q", models: { fast: "f", smart: "s", planner: "p" }, warnings: [`Careful / ${TR}`] } }),
    cmdResult({ name: "session", message: `Switched to session b. / ${TR}`, effects: { session: "b", changed: true } }),
  )
  assert.equal(next.info.provider, "q")
  assert.equal(next.info.model, "f")
  assert.equal(next.info.session, "b")
  assert.deepEqual(notes(next), ["info:Models updated.", "warn:Careful", "info:Switched to session b."])
})

test("a /changes report is shown as it is and cut when long", () => {
  const report = Array.from({ length: 60 }, (_, i) => `M file${i}.py / x`).join("\n")
  const next = feed(hello, cmdResult({ name: "changes", message: report }))
  const note = next.items.find((item) => item.kind === "note")
  assert.ok(note?.kind === "note")
  assert.equal(note.text.split("\n").length, 41)
  assert.match(note.text, /M file0\.py \/ x/)
  assert.match(note.text, /… 20 more lines$/)
})

test("a core exit during a command releases the lock", () => {
  const state = reduce(feed(hello), { type: "command_start", name: "models" })
  assert.equal(reduce(state, { type: "core_exit", expected: false, code: 1 }).command, null)
})

test("notes that arrive while an action runs do not turn its step into a bare line", () => {
  const state = feed(
    stepEvent("write_file", { path: "a.py", content: "x\n" }, { plan_step: "edit" }),
    { type: "debug", message: "stderr chatter from the core" },
    { type: "sandbox_notice", note: "sandbox notice" },
    { type: "error", message: "a retry warning" },
    result(1, "write_file", { summary: "Wrote 1 line", path: "a.py", first_line: 1 }),
  )
  assert.equal(state.inflight, null)
  assert.equal(state.items.filter((i) => i.kind === "tool").length, 1)
  assert.equal(state.items.filter((i) => i.kind === "step").length, 0)
})

const journal = (...events: JournalEvent[]): State => reduce(feed(hello), { type: "restore", events, now: 1000 })

test("restoring a journal redraws the turns in order and leaves the input free", () => {
  const state = journal(
    { type: "user", text: "fix the test" },
    { type: "route", role: "coder", source: "llm" },
    { type: "step", step: 1, plan_step: "edit", thought: "", user_message: "", action_type: "write_file", action_args: { path: "a.py", content: "x = 1\n" }, reasoning_seconds: 0 },
    { type: "action_result", step: 1, action_type: "write_file", ok: true, summary: "Wrote 1 line", output: "", exit_code: null, path: "a.py", first_line: 1 },
    { type: "final", message: "Done.", user_messages: [] },
    { type: "usage", input_tokens: 5, output_tokens: 5, context_tokens: 5, context_limit: 10, model: "m", kind: "turn", step: 0 },
    { type: "turn_end" },
    { type: "user", text: "and now?" },
    { type: "final", message: "Nothing more.", user_messages: [] },
    { type: "turn_end" },
  )
  assert.deepEqual(state.items.map((i) => i.kind), ["user", "tool", "answer", "user", "answer"])
  assert.equal(state.running, false)
  assert.equal(state.live, null)
  assert.equal(state.info.routedRole, undefined)
  // The counters belong to the conversation running now, not to the one redrawn.
  assert.deepEqual(state.usage, { input: 0, output: 0, context: 0, limit: 0 })
  // Everything drawn is history, and only history.
  assert.equal(state.settledThrough, state.nextId - 1)
})

test("restoring replaces what was on screen, and later live items are not history", () => {
  const before = feed(hello, { type: "message", markdown: "old screen" })
  const restored = reduce(before, { type: "restore", events: [{ type: "user", text: "asked" }, { type: "turn_end" }], now: 1000 })
  assert.deepEqual(restored.items.map((i) => i.kind), ["user"])
  const later = reduce(restored, { type: "event", event: { type: "message", markdown: "fresh" }, now: 1000 })
  const fresh = later.items.at(-1)
  assert.ok(fresh && fresh.id > later.settledThrough)
})

test("a journal without its closing turn_end still leaves nothing running", () => {
  const state = journal(
    { type: "user", text: "long job" },
    { type: "step", step: 1, plan_step: "verify", thought: "", user_message: "", action_type: "run_shell", action_args: { command: ["make"] }, reasoning_seconds: 0 },
  )
  assert.equal(state.running, false)
  assert.equal(state.inflight, null)
  assert.deepEqual(state.items.map((i) => i.kind), ["user", "step"])
})

test("a resumed turn has no request line, and a journal notice says what is missing", () => {
  const state = journal(
    { type: "journal_notice", omitted_turns: 3 },
    { type: "user", text: "", resumed: true },
    { type: "final", message: "Continued.", user_messages: [] },
    { type: "turn_end" },
  )
  assert.deepEqual(state.items.map((i) => i.kind), ["note", "answer"])
  assert.match(state.items[0]?.kind === "note" ? state.items[0].text : "", /3 older turns/)
})

test("thoughts come back with their text so the feed can show them", () => {
  const state = journal(
    { type: "user", text: "why" },
    { type: "reasoning_start", stream_id: "r" },
    { type: "reasoning_chunk", stream_id: "r", text: "because of the cache" },
    { type: "reasoning_commit", stream_id: "r", duration_seconds: 2.5 },
    { type: "turn_end" },
  )
  const thought = state.items.find((i) => i.kind === "thought")
  assert.ok(thought && thought.kind === "thought" && thought.text === "because of the cache" && thought.seconds === 2.5)
})

test("resume runs as a turn: the input box is freed by turn_end, not command_result", () => {
  const started = reduce(feed(hello), { type: "resume_start", now: 5 })
  assert.equal(started.running, true)
  assert.equal(started.command, null)

  const ended = reduce(started, { type: "event", event: { type: "turn_end" } })
  assert.equal(ended.running, false)
  assert.equal(ended.command, null)
})

test("turn_end also clears a pending command, so a core that answers one as a turn cannot wedge the input", () => {
  const busy = reduce(feed(hello), { type: "command_start", name: "resume" })
  assert.equal(busy.command, "resume")
  assert.equal(reduce(busy, { type: "event", event: { type: "turn_end" } }).command, null)
})

test("answerFromEnd counts back from the most recent answer, 1-indexed", () => {
  const state = feed(
    { type: "final", message: "first ", user_messages: [] },
    { type: "final", message: "second ", user_messages: [] },
    { type: "final", message: "third ", user_messages: [] },
  )
  assert.equal(answerFromEnd(state.items, 1), "third")
  assert.equal(answerFromEnd(state.items, 2), "second")
  assert.equal(answerFromEnd(state.items, 3), "first")
  assert.equal(answerFromEnd(state.items, 4), null)
})

test("answerFromEnd skips non-answer items (steps, notes) in between", () => {
  const state = feed(
    { type: "final", message: "earlier answer ", user_messages: [] },
    { type: "debug", message: "noise" },
  )
  assert.equal(answerFromEnd(state.items, 1), "earlier answer")
})

test("answerFromEnd returns null when nothing was ever answered", () => {
  assert.equal(answerFromEnd(feed(hello).items, 1), null)
})

import assert from "node:assert/strict"
import { test } from "node:test"

import type { CoreEvent, JournalEvent } from "../protocol.js"
import { actionTarget, answerFromEnd, exploreSummary, initialState, reduce, type State } from "../state.js"

// Stands in for the translation half of the core's bilingual messages, written
// with escapes so no Russian text lives in this file.
const TR = "\u043f\u0435\u0440\u0435\u0432\u043e\u0434"

function feed(...events: CoreEvent[]): State {
  return events.reduce((state, event) => reduce(state, { type: "event", event, now: 1000 }), initialState())
}

const hello: CoreEvent = { type: "hello", protocol_version: 1, provider: "p", models: { fast: "m" }, role: "coder", session: "s" }

test("hello fills the header info and prints the banner once", () => {
  const state = feed(hello, hello)
  assert.deepEqual(state.info, { provider: "p", model: "m", role: "coder", session: "s", kvistVersion: undefined })
  assert.equal(state.items.filter((item) => item.kind === "banner").length, 1)
})

test("hello announces the reasoning effort, and a hello without one keeps the known level", () => {
  assert.equal(feed({ ...hello, effort: "high" }).effort, "high")
  assert.equal(reduce(reduce(initialState("low"), { type: "event", event: hello }), { type: "event", event: hello }).effort, "low")
})

test("hello carries the running kvist's version into state.info", () => {
  const state = feed({ ...hello, kvist_version: "0.1.0b1" })
  assert.equal(state.info.kvistVersion, "0.1.0b1")
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
    { type: "plan", markdown: "**Goal**\n- one change" },
    stepEvent("write_file", { path: "a.py" }, { thought: "Independent step", user_message: "" }),
  )
  assert.deepEqual(state.items.map((i) => i.kind), ["thought", "plan", "thought"])
})

test("stream_chunk with append=true appends deltas, preventing quadratic memory growth", () => {
  const state = feed(
    { type: "stream_start", stream_id: "s" },
    { type: "stream_chunk", stream_id: "s", text: "Hello", append: true },
    { type: "stream_chunk", stream_id: "s", text: " ", append: true },
    { type: "stream_chunk", stream_id: "s", text: "world", append: true },
  )
  assert.equal(state.live?.answer, "Hello world")
  const done = reduce(state, { type: "event", event: { type: "stream_commit", stream_id: "s", text: "Hello world" } })
  assert.equal(done.items[0]?.kind === "answer" && done.items[0].text, "Hello world")
})

test("stream_chunk with append=false replaces the answer (backward compatibility)", () => {
  const state = feed(
    { type: "stream_start", stream_id: "s" },
    { type: "stream_chunk", stream_id: "s", text: "Hel", append: false },
    { type: "stream_chunk", stream_id: "s", text: "Hello", append: false },
  )
  assert.equal(state.live?.answer, "Hello")
})

test("reasoning_chunk with append=true appends deltas", () => {
  const state = feed(
    { type: "reasoning_start", stream_id: "r" },
    { type: "reasoning_chunk", stream_id: "r", text: "Let me", append: true },
    { type: "reasoning_chunk", stream_id: "r", text: " think", append: true },
    { type: "reasoning_chunk", stream_id: "r", text: " about it", append: true },
  )
  assert.equal(state.live?.reasoning, "Let me think about it")
})

test("reasoning_chunk with append=false replaces the reasoning (backward compatibility)", () => {
  const state = feed(
    { type: "reasoning_start", stream_id: "r" },
    { type: "reasoning_chunk", stream_id: "r", text: "partial", append: false },
    { type: "reasoning_chunk", stream_id: "r", text: "complete thought", append: false },
  )
  assert.equal(state.live?.reasoning, "complete thought")
})

test("long streaming with deltas uses linear memory: 10,000 chunks build one final string", () => {
  let state = initialState()
  state = reduce(state, { type: "event", event: { type: "stream_start", stream_id: "long" } })
  for (let i = 0; i < 10_000; i++) {
    state = reduce(state, { type: "event", event: { type: "stream_chunk", stream_id: "long", text: "x", append: true } })
  }
  assert.equal(state.live?.answer.length, 10_000)
  assert.equal(state.live?.answer, "x".repeat(10_000))
})

test("a response's words are shown once as a say line before its calls, reads included", () => {
  const write = feed(stepEvent("write_file", { path: "a.py" }, { user_message: "Writing the fix" }))
  assert.deepEqual(write.items.map((i) => i.kind), ["say"])
  assert.equal(write.items[0]?.kind === "say" && write.items[0].text, "Writing the fix")

  const read = feed(stepEvent("read_file", { path: "a.py" }, { user_message: "Reading the target file" }))
  assert.deepEqual(read.items.map((i) => i.kind), ["say"])
  assert.deepEqual(read.exploring, { reads: 1, searches: 0, lists: 0, git: 0 })
})

test("usage sums spend and takes the core's context size as given", () => {
  const usage = (input: number, context: number, limit: number): CoreEvent => ({
    type: "usage", input_tokens: input, output_tokens: 1, context_tokens: context, context_limit: limit, model: "m", kind: "action", step: 1,
  })
  const state = feed(usage(100, 500, 9000), usage(100, 800, 9000), usage(50, 800, 9000))
  assert.equal(state.usage.input, 250)
  assert.equal(state.usage.output, 3)
  assert.equal(state.usage.context, 800)
  assert.equal(state.usage.limit, 9000)
})

test("a live context event moves the context without touching the spend", () => {
  const state = feed(
    { type: "usage", input_tokens: 10, output_tokens: 2, context_tokens: 100, context_limit: 9000, model: "m", kind: "action", step: 1 },
    { type: "context", context_tokens: 450, context_limit: 9000, prompt: 100, generated: 300, pending: 50 },
  )
  assert.equal(state.usage.context, 450)
  assert.equal(state.usage.limit, 9000)
  assert.equal(state.usage.input, 10)
})

test("/new empties the feed under a fresh welcome block and restarts the context", () => {
  let state = feed(hello, { type: "usage", input_tokens: 1, output_tokens: 1, context_tokens: 500, context_limit: 1000, model: "m", kind: "action", step: 1 })
  state = reduce(state, { type: "submit", text: "old task", now: 1 })
  state = reduce(state, { type: "event", event: { type: "turn_end" } })
  const after = reduce(state, { type: "event", event: { type: "command_result", name: "new", ok: true, message: "Session chat-2 created.", effects: { session: "chat-2", changed: true } } })
  assert.deepEqual(after.items.map((i) => i.kind), ["banner", "note"])
  assert.equal(after.info.session, "chat-2")
  assert.equal(after.usage.context, 0)
  assert.equal(after.epoch, state.epoch + 1)
})

test("a command that changed no session leaves the feed alone", () => {
  const state = reduce(feed(hello), { type: "submit", text: "task", now: 1 })
  const after = reduce(state, { type: "event", event: { type: "command_result", name: "session", ok: true, message: "Active session: s", effects: { session: "s", changed: false } } })
  assert.deepEqual(after.items.map((i) => i.kind), ["banner", "user", "note"])
})

test("a restored session feed can start with the welcome block", () => {
  const restored = reduce(feed(hello), { type: "restore", events: [{ type: "user", text: "asked" }, { type: "turn_end" }], now: 1, banner: true })
  assert.deepEqual(restored.items.map((i) => i.kind), ["banner", "user"])
})

test("a live turn ends with a closing line that knows its length, role and language", () => {
  let state = reduce(feed(hello), { type: "submit", text: "привет", now: 10_000 })
  state = reduce(state, { type: "event", event: { type: "route", role: "chat" }, now: 10_500 })
  state = reduce(state, { type: "event", event: { type: "final", message: "Здравствуйте", user_messages: [], outcome: "done" }, now: 12_000 })
  state = reduce(state, { type: "event", event: { type: "turn_end" }, now: 14_500 })
  const done = state.items.at(-1)
  assert.ok(done?.kind === "done")
  assert.deepEqual({ seconds: done.seconds, at: done.at, role: done.role, russian: done.russian, seed: done.seed, interrupted: done.interrupted }, { seconds: 4.5, at: 14_500, role: "chat", russian: true, seed: 10_000, interrupted: false })
})

test("a turn the person stopped says so, a command or a replayed journal gets no closing line", () => {
  let state = reduce(feed(hello), { type: "submit", text: "fix", now: 0 })
  state = reduce(state, { type: "interrupt" })
  state = reduce(state, { type: "event", event: { type: "turn_end" }, now: 2000 })
  const done = state.items.at(-1)
  assert.ok(done?.kind === "done" && done.interrupted)
  assert.equal(state.interrupted, false)
  const command = reduce(feed(hello), { type: "event", event: { type: "turn_end" }, now: 2000 })
  assert.ok(!command.items.some((item) => item.kind === "done"))
  const replayed = reduce(feed(hello), { type: "restore", events: [{ type: "user", text: "asked" }, { type: "turn_end" }], now: 1000 })
  assert.ok(!replayed.items.some((item) => item.kind === "done"))
})

test("a turn counts its own output tokens and remembers the thought that just ended", () => {
  let state = feed(hello, { type: "usage", input_tokens: 1, output_tokens: 500, context_tokens: 10, context_limit: 100, model: "m", kind: "action", step: 1 })
  state = reduce(state, { type: "submit", text: "go", now: 1000 })
  assert.equal(state.turnStartOutput, 500)
  state = reduce(state, { type: "event", event: { type: "usage", input_tokens: 1, output_tokens: 300, context_tokens: 10, context_limit: 100, model: "m", kind: "action", step: 1 }, now: 2000 })
  assert.equal(state.usage.output - state.turnStartOutput, 300)
  state = reduce(state, { type: "event", event: { type: "reasoning_commit", stream_id: "r", duration_seconds: 2 }, now: 3000 })
  assert.deepEqual(state.thought, { seconds: 2, at: 3000 })
  state = reduce(state, { type: "event", event: { type: "turn_end" }, now: 4000 })
  assert.equal(state.thought, null)
})

test("echo shows a typed command like a request without starting a run", () => {
  const state = reduce(initialState(), { type: "echo", text: "/help" })
  assert.deepEqual(state.items.map((i) => (i.kind === "user" ? i.text : i.kind)), ["/help"])
  assert.equal(state.running, false)
})

test("a wake event starts a turn nobody typed, and its turn_end closes it", () => {
  let state = reduce(feed(hello), { type: "event", event: { type: "wake" }, now: 2000 })
  assert.equal(state.running, true)
  assert.equal(state.startedAt, 2000)
  assert.equal(state.items.some((item) => item.kind === "user"), false)
  state = reduce(state, { type: "event", event: { type: "turn_end" }, now: 5000 })
  assert.equal(state.running, false)
  assert.equal(state.items.at(-1)?.kind, "done")
})

test("a wake turn the core runs before a just-sent request does not end that request's turn", () => {
  let state = reduce(feed(hello), { type: "submit", text: "next question", now: 1000 })
  state = reduce(state, { type: "event", event: { type: "wake" }, now: 1001 })
  state = reduce(state, { type: "event", event: { type: "turn_end" }, now: 3000 })
  assert.equal(state.running, true)
  assert.equal(state.turnsAhead, 0)
  state = reduce(state, { type: "event", event: { type: "turn_end" }, now: 6000 })
  assert.equal(state.running, false)
  assert.equal(state.items.filter((item) => item.kind === "done").length, 1)
})

test("a restored woken turn shows no request of the person", () => {
  const restored = reduce(feed(hello), { type: "restore", events: [{ type: "user", text: "", woken: true }, { type: "final", message: "the build passed", user_messages: [] }, { type: "turn_end" }], now: 1 })
  assert.equal(restored.items.some((item) => item.kind === "user"), false)
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
  assert.equal(started.inflight[0]?.action, "run_shell")
  const done = reduce(started, { type: "event", event: result(3, "run_shell", { summary: "Command exited with code 0", output: "ok", exit_code: 0 }) })
  assert.deepEqual(done.inflight, [])
  assert.deepEqual(done.items.map((i) => i.kind), ["tool"])
  const card = done.items[0]
  assert.equal(card?.kind === "tool" && card.result.output, "ok")
  assert.deepEqual(card?.kind === "tool" && card.args.command, ["npm", "test"])
})

test("a step that never gets a result is committed as a plain step line", () => {
  for (const later of [stepEvent("write_file", { path: "b" }, { step: 2 }), { type: "turn_end" } as CoreEvent, { type: "final", message: "x", user_messages: [] } as CoreEvent]) {
    const state = feed(stepEvent("write_file", { path: "a" }), later, { type: "turn_end" })
    assert.equal(state.items[0]?.kind, "step")
    assert.deepEqual(state.inflight, [])
  }
})

test("a result for an unknown step is ignored", () => {
  const state = feed(stepEvent("write_file", { path: "a" }, { step: 1 }), result(9, "write_file"))
  assert.deepEqual(state.items, [])
  assert.equal(state.inflight[0]?.step, 1)
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
  assert.deepEqual(state.inflight, [])
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
    cmdResult({ name: "models", message: `Models updated. / ${TR}`, effects: { provider: "q", models: { fast: "f", smart: "s" }, warnings: [`Careful / ${TR}`] } }),
    cmdResult({ name: "session", message: `Switched to session b. / ${TR}`, effects: { session: "b", changed: true } }),
  )
  assert.equal(next.info.provider, "q")
  assert.equal(next.info.model, "f")
  assert.equal(next.info.session, "b")
  // Switching sessions empties the feed, so the notes of the models command went with it.
  assert.deepEqual(notes(next), ["info:Switched to session b."])
  assert.deepEqual(notes(feed(hello, cmdResult({ name: "models", message: "Models updated.", effects: { warnings: ["Careful"] } }))), ["info:Models updated.", "warn:Careful"])
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
  assert.deepEqual(state.inflight, [])
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
  assert.deepEqual(state.inflight, [])
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


// ── v2 batches: several calls of one response in flight at once ──────────

function call(step: number, action_type: string, action_args: Record<string, unknown>, callId: string, batch = "b1"): CoreEvent {
  return stepEvent(action_type, action_args, { step, call_id: callId, batch_id: batch })
}

test("parallel calls of one response wait together and commit in their results' order", () => {
  const state = feed(
    call(1, "web_search", { query: "a" }, "c1"),
    call(2, "fetch_url", { url: "https://x" }, "c2"),
    result(1, "web_search", { call_id: "c1", summary: "3 results" }),
    result(2, "fetch_url", { call_id: "c2", summary: "Fetched" }),
  )
  assert.deepEqual(state.inflight, [])
  assert.deepEqual(state.items.map((i) => i.kind === "tool" && [i.step, i.batchIndex]), [[1, 0], [2, 1]])
})

test("a second call of the same response does not settle the first", () => {
  const state = feed(call(1, "web_search", { query: "a" }, "c1"), call(2, "fetch_url", { url: "https://x" }, "c2"))
  assert.deepEqual(state.items, [])
  assert.deepEqual(state.inflight.map((waiting) => waiting.callId), ["c1", "c2"])
})

test("a new response settles calls of the last one that never got a result", () => {
  const state = feed(call(1, "write_file", { path: "a" }, "c1", "b1"), call(2, "write_file", { path: "b" }, "c2", "b2"))
  assert.deepEqual(state.items.map((i) => i.kind), ["step"])
  assert.deepEqual(state.inflight.map((waiting) => waiting.callId), ["c2"])
})

test("results are matched by call id, not by step", () => {
  const state = feed(call(1, "fetch_url", { url: "https://x" }, "c1"), result(9, "fetch_url", { call_id: "c1" }))
  assert.deepEqual(state.items.map((i) => i.kind), ["tool"])
})

test("parallel reads each find their own failure", () => {
  const state = feed(
    stepEvent("read_file", { path: "a" }, { step: 1, call_id: "c1", batch_id: "b1", user_message: "Reading both" }),
    stepEvent("read_file", { path: "b" }, { step: 2, call_id: "c2", batch_id: "b1" }),
    result(1, "read_file", { call_id: "c1", ok: false, summary: "Failed: missing" }),
    result(2, "read_file", { call_id: "c2" }),
    { type: "turn_end" },
  )
  assert.deepEqual(state.items.map((i) => i.kind), ["say", "explored", "note"])
  const note = state.items.find((i) => i.kind === "note")
  assert.equal(note?.kind === "note" && note.text, "read_file  a: Failed: missing")
})

test("thinking time shows once per response, not on every call of it", () => {
  const state = feed(
    stepEvent("run_shell", { command: ["ls"] }, { step: 1, call_id: "c1", batch_id: "b1", reasoning_seconds: 5 }),
    result(1, "run_shell", { call_id: "c1" }),
    stepEvent("run_shell", { command: ["pwd"] }, { step: 2, call_id: "c2", batch_id: "b1", reasoning_seconds: 5 }),
    result(2, "run_shell", { call_id: "c2" }),
  )
  assert.deepEqual(state.items.map((i) => i.kind), ["thought", "tool", "tool"])
})

test("a plan event becomes a card that keeps the Markdown and the language of the request", () => {
  const plan = (text: string) => {
    const asked = reduce(initialState(), { type: "submit", text })
    return reduce(asked, { type: "event", event: { type: "plan", markdown: "**Цель**" } }).items.at(-1)
  }
  assert.deepEqual({ ...plan("rename the module"), id: 0 }, { id: 0, kind: "plan", markdown: "**Цель**", russian: false })
  assert.equal(plan("переименуй модуль")?.kind === "plan" && (plan("переименуй модуль") as { russian: boolean }).russian, true)
})

test("a request shown as a paste label takes its language from the pasted text", () => {
  const asked = reduce(initialState(), { type: "submit", text: "[Pasted text #1 +33 lines]", request: "переименуй модуль" })
  const plan = reduce(asked, { type: "event", event: { type: "plan", markdown: "x" } }).items.at(-1)
  assert.equal(plan?.kind === "plan" && plan.russian, true)
})

const NOTICE = "conversation over · Enter — new session"
const warning = "those last words weren't about the code."
const closing = "the conversation is over."
const ended: CoreEvent = { type: "session_ended", session_id: "s", warned_msg_id: 1, closed_msg_id: 3, reason: "r", closed_notice: NOTICE }

test("session_ended puts the core's notice in place of the input", () => {
  assert.equal(feed(hello).closed, null)
  assert.equal(feed(hello, ended).closed, NOTICE)
})

test("switching into a closed session shows the notice, and switching away brings the input back", () => {
  const closed = feed(hello, { type: "session", session: "old", has_checkpoint: false, closed: true, closed_notice: NOTICE })
  assert.equal(closed.closed, NOTICE)
  assert.equal(closed.info.session, "old")
  const back = reduce(closed, { type: "event", event: { type: "session", session: "fresh", has_checkpoint: false } })
  assert.equal(back.closed, null)
  assert.equal(back.info.session, "fresh")
})

test("the notice survives the switch's command_result and the redrawn journal", () => {
  const switched = feed(
    hello,
    { type: "session", session: "old", has_checkpoint: false, closed: true, closed_notice: NOTICE },
    { type: "command_result", name: "session", ok: true, message: "Switched", effects: { session: "old", changed: true } },
  )
  const redrawn = reduce(switched, { type: "restore", events: [{ type: "user", text: "idiot" }, ended, { type: "final", message: closing, user_messages: [], outcome: "incomplete", closed: true }], banner: true })
  assert.equal(switched.closed, NOTICE)
  assert.equal(redrawn.closed, NOTICE)
})

test("the warning and the closing words are ordinary Kvist messages, with no status", () => {
  const state = feed(
    hello,
    { type: "message", markdown: warning },
    ended,
    { type: "final", message: closing, user_messages: [], outcome: "incomplete", closed: true },
    { type: "message", markdown: "plain reply" },
  )
  const answers = state.items.filter((item) => item.kind === "answer")
  assert.deepEqual(answers.map((item) => item.kind === "answer" && item.text), [warning, closing, "plain reply"])
  assert.equal(state.items.filter((item) => item.kind === "note").length, 0)
  const shapes = answers.map(({ id: _id, ...rest }) => Object.keys(rest).sort().join(","))
  assert.equal(new Set(shapes).size, 1)
})

const feedEvents = (state: State, ...events: CoreEvent[]): State =>
  events.reduce((current, event) => reduce(current, { type: "event", event, now: 1000 }), state)

test("the task state follows the core: pause requested is not paused yet", () => {
  let state = feedEvents(initialState(), { type: "task_state", state: "pause_requested" })
  assert.equal(state.taskState, "pause_requested")
  state = feedEvents(state, { type: "task_state", state: "paused", reason: "user_paused" })
  assert.equal(state.taskState, "paused")
})

test("the agent screen is shown while open and forgotten when closed", () => {
  const open: CoreEvent = { type: "screen", screen_id: "browser", skill: "browser", title: "Browser", state: "running", url: "http://127.0.0.1:4000/?t=x", stream_url: "s", frame_url: "f" }
  let state = feedEvents(initialState(), open)
  assert.deepEqual(state.screen, { title: "Browser", state: "running", url: "http://127.0.0.1:4000/?t=x", stream: "s" })
  state = feedEvents(state, { ...open, state: "closed", url: "", stream_url: "", frame_url: "" })
  assert.equal(state.screen, null)
})

test("a paused turn keeps its screen and running jobs; a finished one clears the screen", () => {
  const open: CoreEvent = { type: "screen", screen_id: "browser", skill: "browser", title: "Browser", state: "running", url: "u", stream_url: "s", frame_url: "f" }
  const paused = feedEvents(initialState(), open, { type: "task_state", state: "paused" }, { type: "turn_end" })
  assert.notEqual(paused.screen, null)
  const finished = feedEvents(initialState(), open, { type: "task_state", state: "completed" }, { type: "turn_end" })
  assert.equal(finished.screen, null)
})

test("skills, helpers and artifacts become short notes; update_applied stays silent", () => {
  const state = feedEvents(initialState(),
    { type: "skill", skill: "browser", version: "0.1.0", name: "Browser", state: "attached" },
    { type: "helper", agent_id: "h1", event: "started", state: "running", session: "helper-x", task: "read the logs", summary: "" },
    { type: "helper", agent_id: "h1", event: "update_applied", state: "running", session: "helper-x", task: "", summary: "" },
    { type: "helper", agent_id: "h1", event: "stopped", state: "stopped", session: "helper-x", task: "", summary: "partial", stopped_by: "person" },
    { type: "artifact", name: "shot.png", mime: "image/png", path: "/w/.kvist/artifacts/r/shot.png", caption: "Screenshot", skill: "browser" },
  )
  const notes = state.items.filter((item) => item.kind === "note").map((item) => (item as { text: string }).text)
  assert.deepEqual(notes, [
    'Skill "Browser" 0.1.0 attached',
    "Helper h1 started: read the logs",
    "Helper h1 stopped by you: partial",
    "Screenshot: /w/.kvist/artifacts/r/shot.png",
  ])
})

test("a session_title for the session on screen becomes its window title; another session's does not", () => {
  const on = feed(hello, { type: "session", session: "s1", has_checkpoint: false })
  const titled = reduce(on, { type: "event", event: { type: "session_title", session: "s1", title: "Fix the parser" } })
  assert.equal(titled.info.title, "Fix the parser")
  const other = reduce(titled, { type: "event", event: { type: "session_title", session: "s2", title: "Other" } })
  assert.equal(other.info.title, "Fix the parser")
  const switched = reduce(other, { type: "event", event: { type: "session", session: "s2", has_checkpoint: false } })
  assert.equal(switched.info.title, undefined)
})

test("a reply is shown whole once anything follows it, so a fast model is not trailed by the typewriter", () => {
  const running = feed(hello, { type: "live_start" } as CoreEvent, { type: "message", markdown: "running the tests now" })
  const answer = (state: State) => state.items.find((item) => item.kind === "answer")
  assert.equal(answer(running)?.kind === "answer" && answer(running)?.fresh, true)
  const called = reduce(running, { type: "event", event: stepEvent("run_shell", { command: "pytest" }), now: 1000 })
  const moved = reduce(called, { type: "event", event: result(1, "run_shell", { exit_code: 0 }), now: 1000 })
  const first = answer(moved)
  assert.ok(first?.kind === "answer" && !first.fresh)
})

// A fake provider at tens of thousands of tokens per second: every round of words, call and result
// reaches the reducer in the same millisecond, so only the redraw can trail the work.
function fastProviderRounds(rounds: number): CoreEvent[] {
  const events: CoreEvent[] = [hello, { type: "live_start" } as CoreEvent]
  for (let round = 0; round < rounds; round++) {
    events.push(stepEvent("run_shell", { command: "pytest" }, { step: round + 1, user_message: `${"word ".repeat(2500)} END${round}` }))
    events.push(result(round + 1, "run_shell", { exit_code: 0 }))
  }
  return events
}

test("at fifty thousand tokens a second only the last message of the feed is still being revealed", () => {
  const state = feed(...fastProviderRounds(40))
  const says = state.items.filter((item) => item.kind === "say")
  assert.equal(says.length, 40)
  assert.deepEqual(says.slice(0, -1).filter((item) => item.kind === "say" && item.fresh), [])
})

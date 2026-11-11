"use strict"

const assert = require("node:assert/strict")
const test = require("node:test")

require("../renderer/js/util.js")
const { Thread } = require("../renderer/js/thread.js")

function kinds(thread) {
  return thread.items.map((item) => item.kind)
}

function ev(type, fields = {}) {
  return { type, ...fields }
}

test("a streamed answer grows in place and an empty commit draws nothing", () => {
  const thread = new Thread()
  thread.addUser("hi")
  thread.apply(ev("stream_start", { stream_id: "s" }))
  assert.deepEqual(kinds(thread), ["user"])
  thread.apply(ev("stream_chunk", { stream_id: "s", text: "Hel" }))
  thread.apply(ev("stream_chunk", { stream_id: "s", text: "Hello" }))
  assert.equal(thread.items[1].text, "Hello")
  assert.equal(thread.items[1].streaming, true)
  thread.apply(ev("stream_commit", { stream_id: "s", text: "Hello!" }))
  assert.equal(thread.items[1].streaming, false)
  assert.equal(thread.items[1].text, "Hello!")

  // end_session's hidden tag: the stream ends with an empty commit
  thread.apply(ev("stream_commit", { stream_id: "tag", text: "" }))
  assert.equal(thread.items.length, 2)
})

test("thinking comes before the answer it precedes", () => {
  const thread = new Thread()
  thread.addUser("q")
  thread.apply(ev("stream_start", { stream_id: "s" }))
  thread.apply(ev("reasoning_start", { stream_id: "s" }))
  thread.apply(ev("reasoning_chunk", { stream_id: "s", text: "hmm" }))
  thread.apply(ev("reasoning_commit", { stream_id: "s", duration_seconds: 4 }))
  thread.apply(ev("stream_chunk", { stream_id: "s", text: "A" }))
  assert.deepEqual(kinds(thread), ["user", "summary", "thinking", "assistant"])
  const thinking = thread.items.find((item) => item.kind === "thinking")
  assert.equal(thinking.done, true)
  assert.equal(thinking.seconds, 4)
})

test("a final that only extends the streamed answer is the same message", () => {
  const thread = new Thread()
  thread.addUser("go")
  thread.apply(ev("stream_commit", { stream_id: "s", text: "Done." }))
  thread.apply(ev("final", { message: "Done.\n\n_Kvist closed the task's processes: browser._", outcome: "done" }))
  const answers = thread.items.filter((item) => item.kind === "assistant")
  assert.equal(answers.length, 1)
  assert.match(answers[0].text, /closed the task's processes/)
})

test("a final with a different text is shown as its own message", () => {
  const thread = new Thread()
  thread.addUser("go")
  thread.apply(ev("stream_commit", { stream_id: "s", text: "First" }))
  thread.apply(ev("final", { message: "Something else", outcome: "done" }))
  assert.equal(thread.items.filter((item) => item.kind === "assistant").length, 2)
})

test("an incomplete or paused final adds a status, a closed one does not", () => {
  const incomplete = new Thread()
  incomplete.addUser("x")
  incomplete.apply(ev("final", { message: "gave up", outcome: "incomplete" }))
  assert.ok(incomplete.items.some((item) => item.kind === "status" && item.tone === "warn"))

  const paused = new Thread()
  paused.addUser("x")
  paused.apply(ev("final", { message: "", outcome: "incomplete", paused: true }))
  assert.ok(paused.items.some((item) => item.kind === "status" && item.tone === "muted"))

  const closed = new Thread()
  closed.addUser("x")
  closed.apply(ev("final", { message: "bye", outcome: "incomplete", closed: true }))
  assert.equal(closed.items.some((item) => item.kind === "status"), false)
  assert.ok(closed.closedNotice)
})

test("a call goes from call_begin to step to result, streaming its text", () => {
  const thread = new Thread()
  thread.addUser("write")
  thread.apply(ev("call_begin", { call_id: "c1", tool: "write_file", path: "a.py", description: "writes a.py" }))
  thread.apply(ev("call_stream", { call_id: "c1", tool: "write_file", path: "a.py", field: "content", line: 0, text: "print(" }))
  thread.apply(ev("call_stream", { call_id: "c1", field: "content", line: 0, text: "1)\n" }))
  const call = thread.items.find((item) => item.kind === "work").calls[0]
  assert.equal(call.state, "writing")
  assert.equal(call.writing.content, "print(1)\n")

  thread.apply(ev("step", { call_id: "c1", step: 1, action_type: "write_file", action_args: { path: "a.py" }, batch_id: "b" }))
  assert.equal(thread.items.filter((item) => item.kind === "work").length, 1, "the step reuses the call_begin card")
  assert.equal(call.state, "running")

  thread.apply(ev("action_result", { call_id: "c1", step: 1, ok: true, summary: "Wrote 1 line", path: "a.py", lines_added: 1, lines_removed: 0, patch: "--- /dev/null\n+++ b/a.py\n@@ -0,0 +1 @@\n+print(1)\n" }))
  assert.equal(call.state, "ok")
  assert.deepEqual(thread.changes().map((entry) => [entry.path, entry.added, entry.removed]), [["a.py", 1, 0]])
})

test("a restart of a streamed field (line 0 after later lines) starts the text over", () => {
  const thread = new Thread()
  thread.addUser("w")
  thread.apply(ev("call_begin", { call_id: "c", tool: "write_file", path: "a" }))
  thread.apply(ev("call_stream", { call_id: "c", field: "content", line: 0, text: "one\ntwo" }))
  thread.apply(ev("call_stream", { call_id: "c", field: "content", line: 0, text: "retry" }))
  assert.equal(thread.items.find((item) => item.kind === "work").calls[0].writing.content, "retry")
})

test("a step carries the model's words before its call", () => {
  const thread = new Thread()
  thread.addUser("do")
  thread.apply(ev("step", { call_id: "c", step: 1, action_type: "read_file", action_args: { path: "x" }, user_message: "Let me look.", batch_id: "b" }))
  assert.deepEqual(kinds(thread), ["user", "summary", "assistant", "work"])
})

test("calls that speak (final_answer) draw no card", () => {
  const thread = new Thread()
  thread.addUser("do")
  thread.apply(ev("step", { call_id: "c", step: 1, action_type: "final_answer", action_args: {}, user_message: "All done." }))
  assert.deepEqual(kinds(thread), ["user", "assistant"])
})

test("results are matched by step when no call_id came", () => {
  const thread = new Thread()
  thread.addUser("run")
  thread.apply(ev("step", { step: 3, action_type: "run_shell", action_args: { command: ["ls"] } }))
  thread.apply(ev("action_output", { step: 3, output: "a\nb" }))
  assert.equal(thread.shellCalls()[0].live, "a\nb")
  thread.apply(ev("action_result", { step: 3, ok: true, summary: "exit 0", output: "a\nb\n", exit_code: 0 }))
  const call = thread.shellCalls()[0]
  assert.equal(call.state, "ok")
  assert.equal(call.live, "")
  assert.equal(call.exitCode, 0)
})

test("a turn's work gets a header; ending the turn stops running calls and folds the work", () => {
  const thread = new Thread()
  thread.addUser("go", { ts: 100 })
  thread.apply(ev("step", { call_id: "c", step: 1, action_type: "run_shell", action_args: { command: ["x"] }, user_message: "On it." }), 101)
  thread.apply(ev("stream_commit", { stream_id: "s", text: "Finished." }))
  const header = thread.items.find((item) => item.kind === "summary")
  assert.equal(header.live, true)
  assert.equal(thread.isFolded(thread.items[2]), false, "nothing folds while the turn runs")

  thread.apply(ev("final", { message: "Finished.", outcome: "done" }), 175)
  assert.equal(thread.shellCalls()[0].state, "stopped")
  assert.deepEqual(kinds(thread), ["user", "summary", "assistant", "work", "assistant"])
  assert.equal(header.live, false)
  assert.equal(header.seconds, 75)

  const [, , interim, work, last] = thread.items
  assert.equal(thread.isFolded(interim), true, "the words before the work fold with it")
  assert.equal(thread.isFolded(work), true)
  assert.equal(thread.isFolded(last), false, "the last answer stays")
  assert.equal(thread.isFolded(header), false)

  thread.toggleFold(header.turn)
  assert.equal(thread.isFolded(work), false, "the header opens the work again")
})

test("a chat answer with no work has no header", () => {
  const thread = new Thread()
  thread.addUser("hi")
  thread.apply(ev("stream_commit", { stream_id: "s", text: "Hello." }))
  thread.apply(ev("final", { message: "Hello.", outcome: "done" }), 5)
  assert.deepEqual(kinds(thread), ["user", "assistant"])
})

test("a journal replays to the same thread as the live events", () => {
  const events = [
    ev("user", { text: "make a" }),
    ev("route", { role: "coder" }),
    ev("step", { call_id: "c", step: 1, action_type: "write_file", action_args: { path: "a" }, batch_id: "b" }),
    ev("action_result", { call_id: "c", step: 1, ok: true, summary: "Wrote", path: "a", lines_added: 1, lines_removed: 0, patch: "--- /dev/null\n+++ b/a\n@@ -0,0 +1 @@\n+x\n" }),
    ev("stream_commit", { stream_id: "s", text: "ok" }),
    ev("final", { message: "ok", outcome: "done" }),
    ev("turn_end"),
  ]
  const live = new Thread()
  for (const event of events) live.apply(event, 10)
  const replayed = new Thread()
  replayed.applyAll(events.map((event, index) => ({ seq: index, ts: 10, event })))
  assert.deepEqual(kinds(replayed), kinds(live))
  assert.equal(replayed.role, "coder")
  assert.equal(replayed.items.at(-1).text, "ok")
})

test("applyAll is silent: nothing is emitted while replaying", () => {
  const thread = new Thread()
  let calls = 0
  thread.onChange = () => { calls += 1 }
  thread.applyAll([{ ts: 1, event: ev("user", { text: "hi" }) }, { ts: 2, event: ev("final", { message: "yo", outcome: "done" }) }])
  assert.equal(calls, 0)
  assert.equal(thread.items.length, 2)
})

test("background jobs and helpers update their cards in place", () => {
  const thread = new Thread()
  thread.addUser("serve")
  thread.apply(ev("job_started", { job_id: "j1", command: ["npm", "start"], purpose: "dev server" }))
  thread.apply(ev("job_output", { job_id: "j1", output: "ready" }))
  const job = thread.items.find((item) => item.kind === "job")
  assert.equal(job.output, "ready")
  assert.equal(thread.activeJobs().length, 1)
  thread.apply(ev("job_finished", { job_id: "j1", status: "exited", exit_code: 0, output: "bye" }))
  assert.equal(job.status, "exited")
  assert.equal(thread.activeJobs().length, 0)

  thread.apply(ev("helper", { agent_id: "h1", state: "running", task: "check" }))
  thread.apply(ev("helper", { agent_id: "h1", state: "done", summary: "fine" }))
  assert.equal(thread.items.filter((item) => item.kind === "helper").length, 1)
  assert.equal(thread.items.find((item) => item.kind === "helper").state, "done")
})

test("usage sums spend but replaces the context size", () => {
  const thread = new Thread()
  thread.apply(ev("usage", { input_tokens: 100, output_tokens: 10, context_tokens: 500, context_limit: 8000, cost_usd: 0.01 }))
  thread.apply(ev("usage", { input_tokens: 120, output_tokens: 20, context_tokens: 650, context_limit: 8000 }))
  assert.equal(thread.stats.tokensIn, 220)
  assert.equal(thread.stats.contextTokens, 650)
  assert.equal(thread.stats.costKnown, false)
})

test("an error ends the turn and shows the message", () => {
  const thread = new Thread()
  thread.addUser("x")
  thread.apply(ev("error", { message: "boom" }))
  assert.deepEqual(kinds(thread), ["user", "error"])
  assert.equal(thread.turn.ended, true)
})

test("unknown events are ignored", () => {
  const thread = new Thread()
  thread.apply(ev("something_new", { a: 1 }))
  assert.equal(thread.items.length, 0)
})

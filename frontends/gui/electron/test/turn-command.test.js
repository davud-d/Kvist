"use strict"

const assert = require("node:assert/strict")
const { EventEmitter, once } = require("node:events")
const path = require("node:path")
const { PassThrough } = require("node:stream")
const test = require("node:test")

const { KvistProcess } = require("../src/kvist-process")
const { ProtocolState } = require("../src/protocol-state")

// `/council` and `/best-of` (UI_API.md §8c) are turns that a command starts. The app runs the single-turn core for
// goals, so such a command gets a core of its own that serves it and ends with the command's `command_result`.

const fakeCore = path.join(__dirname, "..", "..", "..", "tui", "ink", "tests", "fixtures", "fake-core.mjs")

function fakeChild() {
  const child = new EventEmitter()
  child.stdout = new PassThrough()
  child.stderr = new PassThrough()
  child.stdin = new PassThrough()
  child.kill = () => true
  child.stdin.on("finish", () => queueMicrotask(() => child.emit("exit", 0, null)))
  return child
}

function started(options = {}) {
  const child = fakeChild()
  let invocation
  const runner = new KvistProcess({
    workspace: "/tmp/project",
    command: "kvist",
    spawnImpl: (command, args) => {
      invocation = { command, args }
      return child
    },
  })
  let stdin = ""
  child.stdin.on("data", (chunk) => { stdin += chunk.toString("utf8") })
  runner.runTurnCommand("council", ["is", "it", "simple"], options)
  return { child, runner, invocation, stdin: () => stdin }
}

test("a turn command starts the long-lived core and sends the command, not a goal", () => {
  const { invocation, stdin } = started({ role: "reader" })
  assert.deepEqual(invocation.args.slice(0, 4), ["--workspace", "/tmp/project", "--frontend", "stdio-multi"])
  assert.deepEqual(invocation.args.slice(-4), ["--role", "reader", "--approval-mode", "ask"])
  assert.deepEqual(JSON.parse(stdin()), { type: "command", name: "council", args: ["is", "it", "simple"] })
})

test("the process ends with the command_result of its command, after the turn's own events", async () => {
  const { child, runner } = started()
  const events = []
  runner.on("event", (event) => events.push(event.type))
  child.stdout.write('{"type":"hello","protocol_version":1}\n{"type":"final","message":"x"}\n{"type":"turn_end"}\n')
  await new Promise((resolve) => setImmediate(resolve))
  assert.equal(child.stdin.writableEnded, false, "still serving until the command's result")
  const exited = once(runner, "exit")
  child.stdout.write('{"type":"command_result","name":"council","ok":true,"message":"done","effects":{}}\n')
  const [exit] = await exited
  assert.equal(exit.code, 0)
  assert.deepEqual(events, ["hello", "final", "turn_end", "command_result"])
})

test("a refusal, a lone command_result, ends the process too; another command's result does not", async () => {
  const { child, runner } = started()
  child.stdout.write('{"type":"hello","protocol_version":1}\n{"type":"command_result","name":"role","ok":true,"message":"","effects":{}}\n')
  await new Promise((resolve) => setImmediate(resolve))
  assert.equal(child.stdin.writableEnded, false)
  const exited = once(runner, "exit")
  child.stdout.write('{"type":"command_result","name":"council","ok":false,"message":"Usage","effects":{}}\n')
  await exited
})

test("only the turn commands start this way", () => {
  const runner = new KvistProcess({ workspace: "/tmp/project", command: "kvist", spawnImpl: () => fakeChild() })
  assert.throws(() => runner.runTurnCommand("full-reset", []), /Unsupported command/)
  assert.throws(() => runner.runTurnCommand("provider", []), /Unsupported command/)
})

test("the events of best-of and council are ignored by the protocol state; a command_result is passed on", () => {
  const protocol = new ProtocolState()
  protocol.consume({ type: "hello", protocol_version: 1 })
  for (const type of ["bestof_started", "bestof_status", "executor_activity", "bestof_eliminated", "bestof_verdict", "bestof_diff", "bestof_finished", "council_started", "council_card", "council_objection", "council_verdict"]) {
    assert.deepEqual(protocol.consume({ type, run_id: "r1" }), { kind: "ignore" }, type)
  }
  assert.equal(protocol.consume({ type: "command_result", name: "council", ok: false, message: "m" }).kind, "command_result")
})

test("a thread draws the request and the final text of a council, whatever else the journal holds", () => {
  require("../renderer/js/util.js")
  const { Thread } = require("../renderer/js/thread.js")
  const thread = new Thread()
  const journal = [
    { type: "user", text: "/council is it simple" },
    { type: "council_started", council_id: "c1", question: "q", members: [] },
    { type: "council_card", council_id: "c1", executor_id: "security", angle: "security", decision: "d", argument: "a" },
    { type: "bestof_from_the_future", run_id: "r1" },
    { type: "council_verdict", council_id: "c1", chosen: "security", decision: "d", reason: "r", dissent: [] },
    { type: "final", message: "## council\n\nuse security", outcome: "done" },
    { type: "turn_end" },
  ]
  thread.applyAll(journal.map((event, index) => ({ seq: index, ts: 10, event })))
  assert.deepEqual(thread.items.map((item) => item.kind), ["user", "assistant"])
  assert.match(thread.items[1].text, /use security/)
})

test("a council against a real child process: the answer, then exit 0", async () => {
  const runner = new KvistProcess({ workspace: __dirname, command: process.execPath, prefixArgs: [fakeCore] })
  const events = []
  runner.on("event", (event) => events.push(event))
  runner.runTurnCommand("council", ["is", "it", "simple"])
  const [exit] = await once(runner, "exit")
  assert.equal(exit.code, 0)
  const types = events.map((event) => event.type)
  assert.ok(types.indexOf("turn_end") < types.indexOf("command_result"))
  assert.match(events.find((event) => event.type === "final").message, /The chosen decision: use security/)
})

test("a best-of decision against a real child process is an ordinary choice that carries the verdict in its body", async () => {
  const runner = new KvistProcess({ workspace: __dirname, command: process.execPath, prefixArgs: [fakeCore] })
  const events = []
  runner.on("event", (event) => {
    events.push(event)
    if (event.type === "input_request") runner.respond({ request_id: event.request_id, value: "accept" })
  })
  runner.runTurnCommand("best-of", ["2", "fix", "it"])
  const [exit] = await once(runner, "exit")
  assert.equal(exit.code, 0)
  const request = events.find((event) => event.type === "input_request")
  assert.equal(request.kind, "choice")
  assert.equal(request.purpose, "bestof_decision")
  assert.match(request.body, /winner: e1/)
  assert.match(events.find((event) => event.type === "final").message, /accepted/)
})

test("a refused command against a real child process leaves nothing running", async () => {
  const runner = new KvistProcess({ workspace: __dirname, command: process.execPath, prefixArgs: [fakeCore] })
  const events = []
  runner.on("event", (event) => events.push(event))
  runner.runTurnCommand("council", [])
  const [exit] = await once(runner, "exit")
  assert.equal(exit.code, 0)
  assert.deepEqual(events.map((event) => event.type), ["hello", "command_result"])
  assert.equal(events[1].ok, false)
})

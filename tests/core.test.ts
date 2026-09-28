import assert from "node:assert/strict"
import { once } from "node:events"
import path from "node:path"
import { test } from "node:test"
import { fileURLToPath } from "node:url"

import { KvistCore, resolveCoreCommand, splitLines } from "../src/core.js"
import type { CoreEvent } from "../src/protocol.js"
import { sessionOp } from "../src/session.js"

const fake = path.join(path.dirname(fileURLToPath(import.meta.url)), "fixtures", "fake-core.mjs")
const command = { command: process.execPath, prefixArgs: [fake] }
const options = { workspace: process.cwd(), role: "auto", approvalMode: "ask" }

function collect(core: KvistCore) {
  const events: CoreEvent[] = []
  core.on("event", (event: CoreEvent) => events.push(event))
  const until = async (type: string) => {
    while (!events.some((event) => event.type === type)) await once(core, "event")
  }
  return { events, until }
}

test("splitLines keeps a partial trailing line for the next chunk", () => {
  assert.deepEqual(splitLines('{"a":1}\n{"b"'), { lines: ['{"a":1}'], rest: '{"b"' })
  assert.deepEqual(splitLines("x\n"), { lines: ["x"], rest: "" })
})

test("KVIST_CORE_CMD is parsed as an argv array and validated", () => {
  const parsed = resolveCoreCommand({ KVIST_CORE_CMD: JSON.stringify(["/x/python", "/x/main.py"]) })
  assert.deepEqual(parsed, { command: "/x/python", prefixArgs: ["/x/main.py"] })
  assert.throws(() => resolveCoreCommand({ KVIST_CORE_CMD: "[]" }))
  assert.throws(() => resolveCoreCommand({ KVIST_CORE_CMD: '["a", 1]' }))
})

test("one process serves several goals: hello once, turn_end per turn", async () => {
  const core = new KvistCore(command)
  const { events, until } = collect(core)
  core.start(options)
  await until("hello")
  core.send("first")
  await until("turn_end")
  core.send("second\nline")
  while (events.filter((e) => e.type === "turn_end").length < 2) await once(core, "event")
  const finals = events.filter((e) => e.type === "final").map((e) => (e as { message: string }).message)
  assert.deepEqual(finals, ["done: first", "done: second line"])
  assert.equal(events.filter((e) => e.type === "hello").length, 1)
  await core.stop()
})

test("an approval request is answered through respond()", async () => {
  const core = new KvistCore(command)
  const { events, until } = collect(core)
  core.start(options)
  await until("hello")
  core.send("ask")
  await until("input_request")
  core.respond("r1", true)
  await until("turn_end")
  assert.equal((events.find((e) => e.type === "final") as { message: string }).message, "approved")
  await core.stop()
})

test("stopping with a pending approval denies it (fail-closed)", async () => {
  const core = new KvistCore(command)
  const { until } = collect(core)
  core.start(options)
  await until("hello")
  core.send("ask")
  await until("input_request")
  await core.stop()
  assert.equal(core.running, false)
})

test("a foreign protocol version is a fault, not a silent run", async () => {
  const core = new KvistCore(command)
  core.start({ ...options, env: { FAKE_PROTOCOL: "9" } })
  const [error] = (await once(core, "fault")) as [Error]
  assert.match(error.message, /handshake/)
  await core.stop()
})

test("sessionOp parses the one-line JSON reply", async () => {
  const status = await sessionOp(command, process.cwd(), "status")
  assert.equal(status.ok, true)
  assert.equal(status.active_session, "chat-fake")
})

type Result = Extract<CoreEvent, { type: "command_result" }>
type Request = Extract<CoreEvent, { type: "input_request" }>

// Runs one command, answering each dialog with the next entry of `answers`.
// The listener is removed when the command ends: a leftover one would answer
// the next command's dialogs with its own (empty) answers.
async function runCommand(core: KvistCore, name: string, args: string[], answers: (boolean | string | null)[] = []) {
  const events: CoreEvent[] = []
  let listener: (event: CoreEvent) => void = () => {}
  const finished = new Promise<Result>((resolve) => {
    listener = (event: CoreEvent) => {
      events.push(event)
      if (event.type === "input_request") core.respond(event.request_id, answers.shift() ?? null)
      if (event.type === "command_result") resolve(event)
    }
    core.on("event", listener)
  })
  core.sendCommand(name, args)
  try {
    return { result: await finished, requests: events.filter((e): e is Request => e.type === "input_request"), events }
  } finally {
    core.off("event", listener)
  }
}

async function started() {
  const core = new KvistCore(command)
  const { until } = collect(core)
  core.start(options)
  await until("hello")
  return core
}

test("a command with a dialog: input_request, answer, command_result, no turn_end", async () => {
  const core = await started()
  const { result, requests, events } = await runCommand(core, "role", [], ["coder"])
  assert.equal(requests.length, 1)
  assert.equal(requests[0]?.options?.length, 5)
  assert.equal(requests[0]?.current_value, "auto")
  assert.deepEqual([result.ok, result.effects], [true, { role: "coder" }])
  assert.equal(events.some((e) => e.type === "turn_end"), false)
  await core.stop()
})

test("models asks twice, the first list long enough to scroll", async () => {
  const core = await started()
  const { result, requests } = await runCommand(core, "models", [], ["model-40", "beta"])
  assert.deepEqual(requests.map((r) => r.options?.length), [45, 3])
  assert.deepEqual(result.effects?.models, { fast: "model-40", smart: "beta" })
  await core.stop()
})

test("cancelling a dialog (value null) is ok with effects.cancelled", async () => {
  const core = await started()
  const { result } = await runCommand(core, "models", [], [null])
  assert.deepEqual([result.ok, result.effects], [true, { cancelled: true }])
  await core.stop()
})

test("a secret answer travels inbound and is not echoed back", async () => {
  const core = await started()
  const { result, requests, events } = await runCommand(core, "secret", [], ["sk-very-secret"])
  assert.equal(requests[0]?.kind, "secret")
  assert.match(result.message, /14 characters/)
  assert.doesNotMatch(JSON.stringify(events), /sk-very-secret/)
  await core.stop()
})

test("a confirm command answers with approved", async () => {
  const core = await started()
  assert.match((await runCommand(core, "confirm", [], [true])).result.message, /Deleted/)
  assert.match((await runCommand(core, "confirm", [], [false])).result.message, /Kept/)
  await core.stop()
})

test("an unknown command fails at once; so does a command during a turn", async () => {
  const core = await started()
  const unknown = await runCommand(core, "definitely-not-a-command", [])
  assert.equal(unknown.result.ok, false)
  assert.match(unknown.result.message, /Unknown command/)

  const { events, until } = collect(core)
  core.send("slow")
  await until("live_start")
  const busy = await runCommand(core, "role", ["coder"])
  assert.equal(busy.result.ok, false)
  assert.match(busy.result.message, /running/)
  while (!events.some((e) => e.type === "turn_end")) await once(core, "event")
  await core.stop()
})

test("provider, settings, revert and full-reset run in the core", async () => {
  const core = await started()

  const provider = await runCommand(core, "provider", [], ["anthropic", "sk-test-key"])
  assert.deepEqual(provider.requests.map((r) => r.kind), ["choice", "secret"])
  assert.equal(provider.result.effects?.provider, "anthropic")
  // The typed key never comes back out of the core.
  assert.doesNotMatch(JSON.stringify(provider.events), /sk-test-key/)

  const settings = await runCommand(core, "settings-api", [], ["request_timeout", "45"])
  assert.deepEqual(settings.result.effects?.changed_keys, ["request_timeout"])

  const revert = await runCommand(core, "revert", [], [true])
  assert.equal(revert.requests[0]?.kind, "confirm")
  assert.equal((revert.result.effects?.reverted as unknown[])?.length, 2)

  // Two confirmations, and denying either one keeps everything.
  const kept = await runCommand(core, "full-reset", [], [true, false])
  assert.equal(kept.result.effects?.cancelled, true)
  const erased = await runCommand(core, "full-reset", [], [true, true])
  assert.equal(erased.result.effects?.restart_required, true)

  await core.stop()
})

test("resume answers with the turn's events and turn_end, never command_result", async () => {
  const core = await started()
  const events: CoreEvent[] = []
  core.on("event", (event: CoreEvent) => events.push(event))
  core.sendCommand("resume", [])
  while (!events.some((e) => e.type === "turn_end")) await once(core, "event")

  assert.equal(events.some((e) => e.type === "command_result"), false)
  const session = events.find((e) => e.type === "session")
  assert.equal(session && "session" in session ? session.session : "", "chat-interrupted")
  await core.stop()
})

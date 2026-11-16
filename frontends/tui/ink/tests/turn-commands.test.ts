import assert from "node:assert/strict"
import { once } from "node:events"
import path from "node:path"
import { test } from "node:test"
import { fileURLToPath } from "node:url"

import { ALL_COMMANDS, COMMANDS, routeOf, suggest } from "../commands.js"
import { KvistCore } from "../core.js"
import type { CoreEvent } from "../protocol.js"
import { initialState, reduce, type State } from "../state.js"

// The commands that are turns (UI_API.md §8c), driven against the scripted core: what the app sends, the
// events the core answers with, and what the reducer makes of them.

const fake = path.join(path.dirname(fileURLToPath(import.meta.url)), "fixtures", "fake-core.mjs")
const command = { command: process.execPath, prefixArgs: [fake] }
const options = { workspace: process.cwd(), role: "auto", approvalMode: "ask" }

const fold = (state: State, events: CoreEvent[]) => events.reduce((acc, event) => reduce(acc, { type: "event", event, now: 1000 }), state)
const notes = (state: State) => state.items.filter((item) => item.kind === "note")
const answers = (state: State) => state.items.flatMap((item) => (item.kind === "answer" ? [item.text] : []))

async function opened() {
  const core = new KvistCore(command)
  const events: CoreEvent[] = []
  core.on("event", (event: CoreEvent) => events.push(event))
  const until = async (done: (event: CoreEvent) => boolean) => {
    while (!events.some(done)) await once(core, "event")
  }
  core.start(options)
  await until((event) => event.type === "hello")
  return { core, events, until }
}

const isResult = (event: CoreEvent) => event.type === "command_result"

test("/council and /best-of are turns the interface sends as commands", () => {
  assert.equal(routeOf("council"), "turn")
  assert.equal(routeOf("best-of"), "turn")
  for (const name of ["council", "best-of"]) assert.equal(ALL_COMMANDS.find((c) => c.name === name)?.needsArgument, true)
  assert.deepEqual(suggest("/co").map((c) => c.name).filter((name) => name === "council"), ["council"])
  assert.deepEqual(suggest("/best").map((c) => c.name), ["best-of"])
  // The registry the model's reference of Ink's commands is checked against does not list them (turn-commands.ts).
  assert.equal(COMMANDS.some((c) => c.route === "turn"), false)
})

test("a council turn runs to turn_end, shows its final text, and the command_result after it only closes the command", async () => {
  const { core, events, until } = await opened()
  core.sendCommand("council", ["is", "it", "simple"])
  let state = reduce(initialState(), { type: "turn_command_start", name: "council", text: "/council is it simple", now: 1 })
  assert.equal(state.running, true, "the turn is running, so the input stays locked and Ctrl+C can stop it")
  assert.equal(state.command, null)
  await until(isResult)
  const kinds = events.map((event) => event.type)
  assert.ok(kinds.indexOf("turn_end") < kinds.indexOf("command_result"), "turn_end comes before command_result")
  state = fold(state, events.filter((event) => event.type !== "hello"))
  assert.equal(state.running, false)
  assert.equal(state.turnCommand, null)
  assert.equal(state.command, null)
  assert.ok(answers(state).some((text) => text.includes("The chosen decision: use security") && text.includes("| security | use security |")))
  assert.deepEqual(notes(state), [], "no note repeats what the final said")
  assert.equal(state.items.some((item) => item.kind === "done"), true)
  await core.stop()
})

test("the events of a council or a run it has no view for change nothing on screen", async () => {
  const { core, events, until } = await opened()
  core.sendCommand("best-of", ["the", "task"])
  await until((event) => event.type === "input_request")
  const asked = fold(reduce(initialState(), { type: "turn_command_start", name: "best-of", text: "/best-of the task", now: 1 }), events.filter((event) => event.type !== "hello"))
  const unknown = events.filter((event) => /^(bestof|executor|council)_/.test(event.type)).map((event) => event.type)
  assert.ok(unknown.includes("bestof_from_the_future") && unknown.includes("executor_activity") && unknown.includes("bestof_status"))
  const quiet = fold(reduce(initialState(), { type: "turn_command_start", name: "best-of", text: "/best-of the task", now: 1 }), events.filter((event) => /^(bestof|executor|council)_/.test(event.type)))
  assert.deepEqual(quiet.items.map((item) => item.kind), ["user"])
  assert.equal(quiet.running, true)
  assert.ok(asked.pending)
  core.respond(asked.pending!.request_id, "accept")
  await until(isResult)
  await core.stop()
})

test("a best-of decision is a plain choice: its whole verdict is in the feed, the dialog keeps the options", async () => {
  const { core, events, until } = await opened()
  core.sendCommand("best-of", ["2", "fix", "the", "bug"])
  await until((event) => event.type === "input_request")
  let state = reduce(initialState(), { type: "turn_command_start", name: "best-of", text: "/best-of 2 fix the bug", now: 1 })
  state = fold(state, events.filter((event) => event.type !== "hello"))
  const request = state.pending
  assert.ok(request, "the dialog is open")
  assert.equal(request.kind, "choice")
  assert.deepEqual(request.options?.map((option) => option.value), ["accept", "discard"])
  assert.equal(request.body, "", "the dialog does not repeat the verdict a dialog would clip")
  const verdict = answers(state).join("\n")
  for (const line of ["Best-of verdict", "winner: e1 (tests passed)", "| e2 | finalist |"]) assert.ok(verdict.includes(line), line)
  core.respond(request.request_id, "accept")
  state = reduce(state, { type: "answered" })
  const before = events.length
  await until(isResult)
  state = fold(state, events.slice(before).filter((event) => event.type !== "input_request"))
  assert.ok(answers(state).some((text) => text.includes("The result was accepted")))
  assert.equal(state.running, false)
  assert.equal(state.pending, null)
  await core.stop()
})

test("a patch in the body of a decision is shown as a diff block, not as markdown", () => {
  const body = "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new"
  const state = fold(initialState(), [{ type: "input_request", request_id: "q", kind: "choice", title: "Patch", body, options: [{ value: "accept", label: "Accept" }], current_value: "", help_text: "", purpose: "bestof_decision", run_id: "r1" }])
  assert.deepEqual(answers(state), [`\`\`\`diff\n${body}\n\`\`\``])
})

test("a choice with another purpose, or none, keeps its body in the dialog", () => {
  const request = { type: "input_request" as const, request_id: "q", kind: "choice" as const, title: "T", body: "line one\nline two", options: [], current_value: "", help_text: "" }
  const plain = fold(initialState(), [request])
  assert.equal(plain.pending?.body, "line one\nline two")
  assert.deepEqual(plain.items, [])
})

test("a refused turn command leaves no run on screen", async () => {
  const { core, events, until } = await opened()
  core.sendCommand("council", [])
  let state = reduce(initialState(), { type: "turn_command_start", name: "council", text: "/council", now: 1 })
  await until(isResult)
  assert.equal(events.some((event) => event.type === "turn_end"), false, "a refusal has no turn")
  state = fold(state, events.filter((event) => event.type !== "hello"))
  assert.equal(state.running, false)
  assert.equal(state.turnCommand, null)
  assert.equal(state.command, null)
  assert.equal(notes(state).filter((item) => item.tone === "error").length, 1)
  await core.stop()
})

test("a core that does not know the command refuses it the same way", () => {
  const started = reduce(initialState(), { type: "turn_command_start", name: "best-of", text: "/best-of 3 x", now: 1 })
  const state = fold(started, [{ type: "command_result", name: "best-of", ok: false, message: "Unknown command: best-of / Неизвестная команда: best-of", effects: {} }])
  assert.equal(state.running, false)
  assert.equal(state.turnCommand, null)
  assert.match(String(notes(state).at(-1)?.kind === "note" ? (notes(state).at(-1) as { text: string }).text : ""), /Unknown command: best-of/)
})

test("a turn that ends with ok:false after turn_end is reported once, a stop the person asked for is not", async () => {
  const { core, events, until } = await opened()
  core.sendCommand("council", ["refuse"])
  await until(isResult)
  const refusedAfterTurn = fold(reduce(initialState(), { type: "turn_command_start", name: "council", text: "/council refuse", now: 1 }), events.filter((event) => event.type !== "hello"))
  assert.equal(refusedAfterTurn.running, false)
  assert.equal(notes(refusedAfterTurn).filter((item) => item.tone === "error").length, 1)
  await core.stop()

  const slow = await opened()
  slow.core.sendCommand("council", ["slow"])
  await slow.until((event) => event.type === "council_started")
  slow.core.cancel()
  await slow.until(isResult)
  const stopped = fold(reduce(initialState(), { type: "turn_command_start", name: "council", text: "/council slow", now: 1 }), slow.events.filter((event) => event.type !== "hello"))
  assert.equal(stopped.running, false)
  assert.equal(stopped.turnCommand, null)
  assert.deepEqual(notes(stopped), [])
  await slow.core.stop()
})

test("a run in the middle of a council is not ended by a command_result of an older command", () => {
  const state = reduce(initialState(), { type: "turn_command_start", name: "council", text: "/council q", now: 1 })
  const after = fold(state, [{ type: "command_result", name: "role", ok: true, message: "Role set", effects: { role: "coder" } }])
  assert.equal(after.running, true)
  assert.equal(after.turnCommand, "council")
})

import assert from "node:assert/strict"
import { test } from "node:test"

import { ALL_COMMANDS, parseSlash, routeOf, suggest } from "../../../tui/ink/commands"
import type { CoreEvent } from "../../../tui/ink/protocol"
import { initialState, reduce, type State } from "../../../tui/ink/state"

// `/council` and `/best-of` in the panel (UI_API.md §8c): the panel sends a `command` message and dispatches
// `turn_command_start`; everything the core sends goes through the shared reducer.

const hello: CoreEvent = { type: "hello", protocol_version: 1, session: "s" }
const feed = (state: State, ...events: CoreEvent[]) => events.reduce((acc, event) => reduce(acc, { type: "event", event, now: 1000 }), state)
const started = (name: string, text: string) => reduce(feed(initialState(), hello), { type: "turn_command_start", name, text, now: 1 })
const errors = (state: State) => state.items.filter((item) => item.kind === "note" && item.tone === "error")

test("the panel offers both commands and sends them as turns", () => {
  const panelCommands = ALL_COMMANDS.filter((command) => command.name !== "tui" && command.name !== "quit")
  assert.deepEqual(suggest("/co", panelCommands).map((command) => command.name).filter((name) => name === "council"), ["council"])
  assert.deepEqual(suggest("/best", panelCommands).map((command) => command.name), ["best-of"])
  assert.equal(routeOf(parseSlash("/best-of 3 fix the bug")!.name), "turn")
  assert.deepEqual(parseSlash("/best-of 3 fix the bug")!.args, ["3", "fix", "the", "bug"])
})

test("a council turn ends with turn_end, the final text is shown, and the command_result after it changes nothing", () => {
  const council: CoreEvent[] = [
    { type: "council_started", council_id: "c1" } as unknown as CoreEvent,
    { type: "council_card", council_id: "c1" } as unknown as CoreEvent,
    { type: "council_verdict", council_id: "c1" } as unknown as CoreEvent,
    { type: "final", message: "## council\n\nuse security", user_messages: [], outcome: "done" },
    { type: "turn_end" },
  ]
  const running = started("council", "/council which?")
  assert.equal(running.running, true)
  const ended = feed(running, ...council)
  assert.equal(ended.running, false)
  assert.ok(ended.items.some((item) => item.kind === "answer" && item.text.includes("use security")))
  const closed = feed(ended, { type: "command_result", name: "council", ok: true, message: "council: decision chosen / выбрано", effects: { chosen: "security" } })
  assert.deepEqual(closed.items, ended.items)
  assert.equal(closed.running, false)
})

test("a refusal (a lone command_result) does not leave the panel running", () => {
  const refused = feed(started("council", "/council"), { type: "command_result", name: "council", ok: false, message: "Usage: /council <question> / Использование", effects: {} })
  assert.equal(refused.running, false)
  assert.equal(refused.turnCommand, null)
  assert.equal(errors(refused).length, 1)
})

test("a best-of decision opens as an ordinary choice and its verdict stays readable", () => {
  const body = ["Verdict", "winner: e1", "| executor | outcome |", "| e1 | winner |"].join("\n")
  const asked = feed(started("best-of", "/best-of 2 x"), {
    type: "input_request", request_id: "q", kind: "choice", title: "Best-of result", body, current_value: "", help_text: "",
    options: [{ value: "accept", label: "Accept" }, { value: "discard", label: "Discard" }], purpose: "bestof_decision", run_id: "r1",
  })
  assert.equal(asked.pending?.kind, "choice")
  assert.equal(asked.pending?.options?.length, 2)
  assert.ok(asked.items.some((item) => item.kind === "answer" && item.text === body), "the whole verdict is in the transcript")
})

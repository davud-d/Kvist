import assert from "node:assert/strict"
import { test } from "node:test"

import type { CoreEvent } from "../../../tui/ink/protocol"
import { initialState, reduce, type State } from "../../../tui/ink/state"
import { opensNewSession, type KeyPress } from "./closed"

const NOTICE = "conversation over · Enter — new session"
const hello: CoreEvent = { type: "hello", protocol_version: 1, session: "s" }
const ended: CoreEvent = { type: "session_ended", session_id: "s", warned_msg_id: 1, closed_msg_id: 3, reason: "r", closed_notice: NOTICE }
const closedFinal: CoreEvent = { type: "final", message: "the conversation is over.", user_messages: [], outcome: "incomplete", closed: true }

// What the panel's message handler does with core events: each goes through the shared reducer.
function panel(...events: CoreEvent[]): State {
  return events.reduce((state, event) => reduce(state, { type: "event", event, now: 1000 }), initialState())
}

const press = (key: string, extra: Partial<KeyPress> = {}): KeyPress => ({ key, shiftKey: false, ctrlKey: false, altKey: false, metaKey: false, targetTag: "BODY", ...extra })

test("a live session_ended replaces the composer with the core's notice", () => {
  assert.equal(panel(hello).closed, null)
  assert.equal(panel(hello, ended, closedFinal, { type: "turn_end" }).closed, NOTICE)
})

test("opening a closed session from the picker shows the notice; opening another brings the composer back", () => {
  const closed = panel(hello, { type: "session", session: "old", has_checkpoint: false, closed: true, closed_notice: NOTICE })
  assert.equal(closed.closed, NOTICE)
  // The panel clears the feed after a switch and replays the journal through `restore`.
  const redrawn = reduce(reduce(closed, { type: "clear" }), { type: "restore", events: [{ type: "user", text: "idiot" }, ended, closedFinal] })
  assert.equal(redrawn.closed, NOTICE)
  const back = reduce(redrawn, { type: "event", event: { type: "session", session: "fresh", has_checkpoint: false } })
  assert.equal(back.closed, null)
})

test("Enter opens a new session; a control keeps its own Enter", () => {
  assert.equal(opensNewSession(press("Enter")), true)
  assert.equal(opensNewSession(press("Enter", { targetTag: "button" })), false)
  assert.equal(opensNewSession(press("Enter", { targetTag: "SELECT" })), false)
  assert.equal(opensNewSession(press("Enter", { shiftKey: true })), false)
})

test("typed characters, Escape and paste shortcuts do nothing", () => {
  for (const key of ["a", "Escape", " ", "Tab", "v", "Backspace"]) assert.equal(opensNewSession(press(key)), false)
  assert.equal(opensNewSession(press("v", { ctrlKey: true })), false)
})

test("the warning and the closing words are ordinary answers, with no status", () => {
  const state = panel(hello, { type: "message", markdown: "those last words weren't about the code." }, ended, closedFinal)
  const kinds = state.items.filter((item) => item.kind !== "banner").map((item) => item.kind)
  assert.deepEqual(kinds, ["answer", "answer"])
})

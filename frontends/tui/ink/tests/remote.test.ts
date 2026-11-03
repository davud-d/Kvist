import assert from "node:assert/strict"
import { test } from "node:test"

import type { CoreEvent } from "../protocol.js"
import { remoteNotice } from "../remote.js"
import { initialState, reduce, type State } from "../state.js"

function feed(...events: CoreEvent[]): State {
  return events.reduce((state, event) => reduce(state, { type: "event", event, now: 1000 }), initialState())
}

const WATCH = "another frontend is driving this session · Enter — take over"

test("a core on a server that another frontend drives puts its notice in place of the input", () => {
  assert.equal(feed().observer, null)
  assert.equal(feed({ type: "control", role: "observer", notice: WATCH }).observer, WATCH)
})

test("becoming the driver brings the input back", () => {
  const state = feed({ type: "control", role: "observer", notice: WATCH }, { type: "control", role: "driver", notice: "" })
  assert.equal(state.observer, null)
})

test("a dialog that arrived while watching is kept for when this frontend drives", () => {
  const request: CoreEvent = { type: "input_request", request_id: "r1", kind: "confirm", title: "Run it?", body: "", options: null, current_value: "", help_text: "" }
  const state = feed({ type: "control", role: "observer", notice: WATCH }, request, { type: "control", role: "driver", notice: "" })
  assert.equal(state.pending?.request_id, "r1")
})

test("a link that is down shows its reason and the countdown, and coming back clears it", () => {
  const down = feed({ type: "remote_state", state: "reconnecting", host: "me@box", reason: "me@box is not answering", attempt: 2, retry_in: 4 })
  assert.deepEqual(down.remote, { state: "reconnecting", host: "me@box", reason: "me@box is not answering", retryIn: 4 })
  assert.equal(feed({ type: "remote_state", state: "reconnecting", host: "h" }, { type: "remote_state", state: "connected", host: "h" }).remote, null)
})

test("the notice counts down in the person's language, and giving up has no countdown", () => {
  const link = { state: "reconnecting" as const, host: "h", reason: "h is not answering", retryIn: 7.4 }
  assert.equal(remoteNotice(link, false), "h is not answering · retrying in 7 s")
  assert.match(remoteNotice(link, true), /повтор через 7 с$/)
  assert.equal(remoteNotice({ ...link, state: "gave_up", retryIn: null }, false), "h is not answering")
  assert.equal(remoteNotice({ state: "gave_up", host: "box", reason: "", retryIn: null }, false), "box")
})

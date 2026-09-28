import assert from "node:assert/strict"
import { test } from "node:test"

import { farewellText } from "../src/farewell.js"

const plain = (text: string) => text.replace(/\x1b\[[0-9;]*m/g, "")

test("the farewell is the mark, that the agent stopped, and how to come back", () => {
  const out = plain(farewellText("chat-1", 100))
  assert.match(out, /██/)
  assert.match(out, /Kvist agent stopped\./)
  assert.match(out, /To continue, start the agent and resume session chat-1\./)
})

test("a session with nothing to resume gets no way back", () => {
  const out = plain(farewellText(null, 100))
  assert.match(out, /Kvist agent stopped\./)
  assert.doesNotMatch(out, /resume/)
})

test("the mark is six rows in a wide window and three in a narrow one", () => {
  const rows = (columns: number) => plain(farewellText("s", columns)).split("\n").filter((line) => /[█▀▄]/.test(line)).length
  assert.equal(rows(100), 6)
  assert.equal(rows(40), 3)
})

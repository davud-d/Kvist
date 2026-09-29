import assert from "node:assert/strict"
import { test } from "node:test"

import { collapsePaste, emptyPasteStore, expandPastes, findRepeatedLabel, isLargePaste, labelLengthBefore } from "../src/paste.js"

const lines = (n: number) => Array.from({ length: n }, (_, i) => `line ${i}`).join("\n")

test("short pastes stay in the line, long or tall ones collapse", () => {
  assert.equal(isLargePaste("one\ntwo\nthree"), false)
  assert.equal(isLargePaste(lines(4)), true)
  assert.equal(isLargePaste("x".repeat(800)), true)
})

test("a collapsed paste is labelled and expands back to the original text", () => {
  const store = emptyPasteStore()
  const label = collapsePaste(store, lines(34))
  assert.equal(label, "[Pasted text #1 +33 lines]")
  assert.equal(expandPastes(store, `fix this: ${label} please`), `fix this: ${lines(34)} please`)
})

test("a single long line is labelled by its length and ids count up", () => {
  const store = emptyPasteStore()
  collapsePaste(store, lines(5))
  assert.equal(collapsePaste(store, "y".repeat(900)), "[Pasted text #2 900 chars]")
})

test("a label without stored text is left as written", () => {
  assert.equal(expandPastes(emptyPasteStore(), "[Pasted text #7 +3 lines]"), "[Pasted text #7 +3 lines]")
})

test("CRLF pastes count lines once", () => {
  assert.equal(collapsePaste(emptyPasteStore(), "a\r\nb\r\nc\r\nd"), "[Pasted text #1 +3 lines]")
})

test("Backspace removes only a label that ends at the cursor", () => {
  const text = "go [Pasted text #1 +33 lines] now"
  assert.equal(labelLengthBefore(text, 29), "[Pasted text #1 +33 lines]".length)
  assert.equal(labelLengthBefore(text, 30), 0)
})

test("pasting the text a label stands for finds that label", () => {
  const store = emptyPasteStore()
  const label = collapsePaste(store, lines(6))
  const line = `a ${label} b`
  assert.deepEqual(findRepeatedLabel(store, line, lines(6)), { start: 2, end: 2 + label.length })
  assert.equal(findRepeatedLabel(store, line, lines(7)), null)
})

import assert from "node:assert/strict"
import { test } from "node:test"

import { clipLines, clipText, contextLabel, fit, formatThought, formatTokens, lastLines, outputLines, englishOnly } from "../format.js"

test("formatThought keeps a decimal for short spans and rounds long ones", () => {
  assert.equal(formatThought(1.24), "Thought: 1.2s")
  assert.equal(formatThought(12.4), "Thought: 12s")
  assert.equal(formatThought(75), "Thought: 1m 15s")
  assert.equal(formatThought(0), "Thought")
})

test("formatTokens uses K and M with one decimal", () => {
  assert.equal(formatTokens(999), "999")
  assert.equal(formatTokens(24_640), "24.6K")
  assert.equal(formatTokens(1_250_000), "1.3M")
})

test("contextLabel shows a percentage only when the limit is known", () => {
  assert.equal(contextLabel(24_600, 200_000), "24.6K (12%)")
  assert.equal(contextLabel(24_600, 0), "24.6K")
  assert.equal(contextLabel(0, 200_000), "")
})

test("lastLines keeps the tail behind an ellipsis", () => {
  assert.equal(lastLines("a\nb\nc\nd", 3), "…\nc\nd")
  assert.equal(lastLines("a\nb", 3), "a\nb")
})

test("outputLines strips escape codes, folds CRLF and progress rewrites, drops trailing blanks", () => {
  assert.deepEqual(outputLines("\u001b[32mok\u001b[0m\r\n10%\r50%\r100%\n\n\n"), ["ok", "100%"])
  assert.deepEqual(outputLines(""), [])
})

test("clipLines reports how many lines it left out", () => {
  assert.deepEqual(clipLines(["a", "b", "c"], 2), { shown: ["a", "b"], more: 1 })
  assert.deepEqual(clipLines(["a"], 2), { shown: ["a"], more: 0 })
})

test("fit pads short text and cuts long text to exactly the width", () => {
  assert.equal(fit("ab", 5), "ab   ")
  assert.equal(fit("abcdefgh", 5), "abcd…")
  assert.equal(fit("a\tb", 8), "a    b  ")
  assert.equal(fit("x", 0), "")
})

// The translation half is written with escapes so no Russian text lives here.
const TR = "\u043f\u0435\u0440\u0435\u0432\u043e\u0434"

test("englishOnly keeps the English half of a bilingual core message", () => {
  assert.equal(englishOnly(`Role set to: coder / ${TR}: coder`), "Role set to: coder")
})

test("englishOnly is not fooled by slashes inside a half and leaves other lines alone", () => {
  assert.equal(englishOnly(`Use auto / coder / ${TR} auto / coder`), "Use auto / coder")
  assert.equal(englishOnly("src/a.py / b"), "src/a.py / b")
  assert.equal(englishOnly("plain"), "plain")
  assert.equal(englishOnly(`One / ${TR}\nTwo\nThree / ${TR}`), "One\nTwo\nThree")
})

test("clipText cuts a long report and says how much was left out", () => {
  const text = Array.from({ length: 50 }, (_, i) => `l${i}`).join("\n") + "\n\n"
  const clipped = clipText(text, 40).split("\n")
  assert.equal(clipped.length, 41)
  assert.equal(clipped.at(-1), "… 10 more lines")
  assert.equal(clipText("a\nb\n", 40), "a\nb")
})

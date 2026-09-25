import assert from "node:assert/strict"
import { test } from "node:test"

import { MAX_VISIBLE_SUGGESTIONS, parseSlash, resolveEnter, suggest, visibleSuggestionCount, windowStart } from "../src/commands.js"
import { applyKey, emptyEditor, insert } from "../src/editor.js"
import { parseBlocks } from "../src/ui/markdown.js"

test("typing inserts at the cursor and accented letters are fine", () => {
  let ed = insert(emptyEditor, "na\u00efve")
  ed = applyKey(ed, "", { leftArrow: true })!
  ed = applyKey(ed, "!", {})!
  assert.deepEqual(ed, { text: "na\u00efv!e", cursor: 5 })
})

test("backspace and delete both erase to the left (terminals disagree)", () => {
  const ed = { text: "abc", cursor: 3 }
  assert.equal(applyKey(ed, "", { backspace: true })?.text, "ab")
  assert.equal(applyKey(ed, "", { delete: true })?.text, "ab")
  assert.deepEqual(applyKey(emptyEditor, "", { backspace: true }), emptyEditor)
})

test("ctrl shortcuts: a e u k w", () => {
  const ed = { text: "one two", cursor: 7 }
  assert.equal(applyKey(ed, "a", { ctrl: true })?.cursor, 0)
  assert.equal(applyKey(ed, "w", { ctrl: true })?.text, "one ")
  assert.equal(applyKey({ text: "abcd", cursor: 2 }, "u", { ctrl: true })?.text, "cd")
  assert.equal(applyKey({ text: "abcd", cursor: 2 }, "k", { ctrl: true })?.text, "ab")
})

test("unhandled chords are left to the caller", () => {
  assert.equal(applyKey(emptyEditor, "c", { ctrl: true }), null)
  assert.equal(applyKey(emptyEditor, "x", { meta: true }), null)
})

test("a pasted newline becomes a space because a goal is one stdin line", () => {
  assert.equal(insert(emptyEditor, "a\nb").text, "a b")
  assert.equal(insert(emptyEditor, "a\rb\r\nc").text, "a b c")
  assert.equal(insert(emptyEditor, "a\x1bb\x00").text, "ab")
})

test("slash suggestions filter by prefix and stop once an argument starts", () => {
  assert.deepEqual(suggest("/se").map((c) => c.name), ["session", "settings-api"])
  assert.deepEqual(suggest("/ses").map((c) => c.name), ["session"])
  assert.equal(suggest("/role coder").length, 0)
  assert.equal(suggest("hello").length, 0)
  assert.deepEqual(parseSlash("/Bye"), { name: "quit", args: [] })
  assert.deepEqual(parseSlash("/role  coder"), { name: "role", args: ["coder"] })
  assert.equal(parseSlash("hi"), null)
})

test("markdown blocks: fences keep their body verbatim, lists and headings parse", () => {
  const blocks = parseBlocks("# Title\n\n- one\n  - two\n\n```py\nx = 1\n\n# not a heading\n```\n")
  assert.deepEqual(blocks.map((b) => b.kind), ["heading", "blank", "bullet", "bullet", "blank", "code"])
  const code = blocks.at(-1)
  assert.equal(code?.kind === "code" && code.lines.join("|"), "x = 1||# not a heading")
})

test("the suggestion window follows the selection and never leaves the list", () => {
  assert.equal(windowStart(4, 3, 6), 0)
  assert.equal(windowStart(9, 0, 6), 0)
  assert.equal(windowStart(9, 5, 6), 0)
  assert.equal(windowStart(9, 6, 6), 1)
  assert.equal(windowStart(9, 8, 6), 3)
  for (let selected = 0; selected < 9; selected++) {
    const first = windowStart(9, selected, 6)
    assert.ok(selected >= first && selected < first + 6, `row ${selected} must be visible`)
  }
})

test("the suggestion list shrinks on a short terminal so its live frame never reaches the row count", () => {
  // A tall terminal keeps the usual cap.
  assert.equal(visibleSuggestionCount(30), MAX_VISIBLE_SUGGESTIONS)
  // A short terminal (the reported bug: opening the menu on a small window
  // made Ink fall back to a full-screen clear that never recovered) must
  // leave enough margin that the whole live frame — the bordered input box,
  // caption, suggestions, counter and footer — stays under the row count.
  assert.equal(visibleSuggestionCount(10), 2)
  // Never collapses to zero, however small the window.
  assert.equal(visibleSuggestionCount(1), 1)
  assert.equal(visibleSuggestionCount(5), 1)
})

test("Enter runs the highlighted command, or completes one that needs an argument", () => {
  assert.deepEqual(resolveEnter("/", 0), { kind: "submit", text: "/help" })
  assert.deepEqual(resolveEnter("/ro", 0), { kind: "submit", text: "/role" })
  assert.deepEqual(resolveEnter("/eff", 0), { kind: "submit", text: "/effort" })
  // No shipped command needs an argument now, but the completion path stays.
  const needy = [{ name: "ask", route: "core", hint: "", needsArgument: true }] as const
  assert.deepEqual(resolveEnter("/a", 0, needy), { kind: "fill", text: "/ask " })
  // Not a suggestion situation: text goes through untouched.
  assert.deepEqual(resolveEnter("fix the bug", 0), { kind: "submit", text: "fix the bug" })
  assert.deepEqual(resolveEnter("/role coder", 0), { kind: "submit", text: "/role coder" })
  assert.deepEqual(resolveEnter("/nope", 0), { kind: "submit", text: "/nope" })
})

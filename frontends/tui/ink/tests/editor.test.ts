import assert from "node:assert/strict"
import { test } from "node:test"

import { MAX_BOX_ROWS, MAX_VISIBLE_SUGGESTIONS, parseSlash, resolveEnter, suggest, visibleBoxRows, visibleSuggestionCount, windowStart } from "../commands.js"
import { applyKey, cursorRow, emptyEditor, insert, moveRow, wrapRows } from "../editor.js"
import { parseBlocks } from "../ui/markdown.js"

test("typing inserts at the cursor and accented letters are fine", () => {
  let ed = insert(emptyEditor, "na\u00efve")
  ed = applyKey(ed, "", { leftArrow: true })!
  ed = applyKey(ed, "!", {})!
  assert.deepEqual(ed, { text: "na\u00efv!e", cursor: 5 })
})

test("backspace erases to the left and delete to the right", () => {
  const ed = { text: "abc", cursor: 3 }
  assert.equal(applyKey(ed, "", { backspace: true })?.text, "ab")
  assert.deepEqual(applyKey(emptyEditor, "", { backspace: true }), emptyEditor)
  const middle = { text: "abcd", cursor: 1 }
  assert.deepEqual(applyKey(middle, "", { delete: true }), { text: "acd", cursor: 1 })
  assert.deepEqual(applyKey(ed, "", { delete: true }), ed)
})

test("ctrl+delete erases the next word", () => {
  assert.deepEqual(applyKey({ text: "one two three", cursor: 3 }, "", { delete: true, ctrl: true }), { text: "one three", cursor: 3 })
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
  // A tab is counted as zero cells by the layout but printed at a terminal tab
  // stop, so keeping it makes the frame wider than Ink's erase assumes.
  assert.equal(insert(emptyEditor, "a\tb").text, "a b")
  assert.equal(insert(emptyEditor, "a\t\tb").text, "a  b")
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

test("box rows and suggestions share a budget that keeps the frame under the terminal height", () => {
  // The live frame is the input box (its content rows plus two borders), the
  // caption, the suggestions, their counter and the footer.
  const frame = (rows: number) => visibleBoxRows(rows) + visibleSuggestionCount(rows) + 5
  for (let rows = 8; rows <= 60; rows++) {
    assert.ok(frame(rows) <= rows - 1, `rows=${rows}: live frame ${frame(rows)} must stay under the terminal height`)
  }
  assert.equal(visibleBoxRows(60), MAX_BOX_ROWS)
  assert.equal(visibleBoxRows(8), 1)
  // Below the eight rows the minimum frame needs, the floors stop it growing further.
  assert.equal(frame(5), frame(8))
})

test("wrapRows breaks at newlines and where a character no longer fits, counting display cells", () => {
  assert.deepEqual(wrapRows("", 4), [{ start: 0, end: 0 }])
  assert.deepEqual(wrapRows("abcdef", 4), [{ start: 0, end: 4 }, { start: 4, end: 6 }])
  assert.deepEqual(wrapRows("ab\ncd", 4), [{ start: 0, end: 2 }, { start: 3, end: 5 }])
  assert.deepEqual(wrapRows("你好世", 4), [{ start: 0, end: 2 }, { start: 2, end: 3 }])
})

test("cursorRow puts a cursor at a wrap point on the row that starts there", () => {
  const rows = wrapRows("abcdef", 4)
  assert.equal(cursorRow(rows, 3), 0)
  assert.equal(cursorRow(rows, 4), 1)
  assert.equal(cursorRow(rows, 6), 1)
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

test("the goal box keeps pasted line breaks when asked to", () => {
  assert.equal(insert(emptyEditor, "a\r\nb\tc", true).text, "a\nb c")
})

test("Alt+Backspace and Ctrl+Backspace delete the previous word, across lines too", () => {
  const ed = { text: "one two\nthree", cursor: 13 }
  assert.deepEqual(applyKey(ed, "", { backspace: true, meta: true }), { text: "one two\n", cursor: 8 })
  assert.deepEqual(applyKey({ text: "one two", cursor: 7 }, "", { backspace: true, ctrl: true }), { text: "one ", cursor: 4 })
})

test("Home, End and Ctrl+A/E/U/K work on the cursor's line", () => {
  const ed = { text: "ab\ncdef", cursor: 5 }
  assert.equal(applyKey(ed, "", { home: true })!.cursor, 3)
  assert.equal(applyKey(ed, "", { end: true })!.cursor, 7)
  assert.deepEqual(applyKey(ed, "u", { ctrl: true }), { text: "ab\nef", cursor: 3 })
  assert.deepEqual(applyKey(ed, "k", { ctrl: true }), { text: "ab\ncd", cursor: 5 })
})

test("moveRow keeps the column across wrapped rows and stops at the first and last row", () => {
  const ed = { text: "abcdefghij", cursor: 6 }
  assert.equal(moveRow(ed, 4, -1)!.cursor, 2)
  assert.equal(moveRow(ed, 4, 1)!.cursor, 10)
  assert.equal(moveRow({ ...ed, cursor: 2 }, 4, -1), null)
  assert.equal(moveRow({ ...ed, cursor: 9 }, 4, 1), null)
  const lines = { text: "abcd\nx\nefgh", cursor: 3 }
  const down = moveRow(lines, 8, 1)!
  assert.equal(down.cursor, 6)
  assert.equal(moveRow(down, 8, 1)!.cursor, 8)
})

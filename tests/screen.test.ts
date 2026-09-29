import assert from "node:assert/strict"
import { test } from "node:test"

import { cellAt, cellsOf, highlight, highlightLine, lineAt, order, selectedText, wordAt } from "../screen.js"

const RED = "\x1b[31m"
const OFF = "\x1b[39m"

test("cells skip escape sequences and count wide characters as two columns", () => {
  const cells = cellsOf(`${RED}ab${OFF}好c`)
  assert.deepEqual(cells.map((cell) => [cell.text, cell.col, cell.width]), [["a", 0, 1], ["b", 1, 1], ["好", 2, 2], ["c", 4, 1]])
  assert.equal(cellAt(cells, 3)?.text, "好")
  assert.equal(cellAt(cells, 9), undefined)
})

test("a selection across rows copies the covered cells and trims each row's padding", () => {
  const lines = ["hello world   ", `${RED}second${OFF} row   `, "third"]
  const text = selectedText(lines, { start: { row: 0, col: 6 }, end: { row: 2, col: 2 } })
  assert.equal(text, "world\nsecond row\nthi")
})

test("points are ordered whichever way the drag went", () => {
  const a = { row: 3, col: 5 }
  const b = { row: 1, col: 9 }
  assert.deepEqual(order(a, b), { start: b, end: a })
  assert.deepEqual(order(b, a), { start: b, end: a })
})

test("highlighting inverts the covered cells and re-arms after a colour inside the range", () => {
  assert.equal(highlightLine(`x${RED}abc${OFF}y`, [1, 3]), `x${RED}\x1b[7mabc\x1b[27m${OFF}y`)
  const inside = highlightLine(`a${RED}b${OFF}c`, [0, 2])
  assert.equal(inside, `\x1b[7ma${RED}\x1b[7mb${OFF}\x1b[7mc\x1b[27m`)
  assert.equal(cellsOf(inside).map((cell) => cell.text).join(""), "abc")
})

test("only the rows a selection touches change", () => {
  const lines = ["one", "two", "three"]
  const out = highlight(lines, { start: { row: 1, col: 0 }, end: { row: 1, col: 1 } })
  assert.equal(out[0], "one")
  assert.equal(out[2], "three")
  assert.equal(out[1], "\x1b[7mtw\x1b[27mo")
})

test("a double-click takes the word, a path stays whole, and separators are their own run", () => {
  const line = "open src/ui/app.tsx (now)"
  assert.deepEqual(wordAt(line, 7), [5, 18])
  assert.deepEqual(wordAt(line, 1), [0, 3])
  assert.deepEqual(wordAt(line, 4), [4, 4])
  assert.deepEqual(wordAt(line, 20), [19, 20])
  assert.equal(wordAt(line, 99), null)
})

test("a triple-click takes the row up to its last visible character", () => {
  assert.deepEqual(lineAt("  some text   "), [0, 10])
  assert.equal(lineAt("     "), null)
})

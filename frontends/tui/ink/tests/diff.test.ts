import assert from "node:assert/strict"
import { test } from "node:test"

import { diffLines, numberWidth, pairRows, parseUnifiedDiff, toLines } from "../diff.js"

const shape = (rows: ReturnType<typeof diffLines>) => rows.map((r) => `${r.kind === "ctx" ? " " : r.kind === "del" ? "-" : "+"}${r.text}`)

test("toLines: no phantom row for a trailing newline, none at all for empty text, CRLF folded", () => {
  assert.deepEqual(toLines(""), [])
  assert.deepEqual(toLines("a\nb\n"), ["a", "b"])
  assert.deepEqual(toLines("a\r\nb\r\n"), ["a", "b"])
  assert.deepEqual(toLines("a\n\n"), ["a", ""])
})

test("a replaced line keeps its neighbours as context, numbered from first_line", () => {
  const rows = diffLines("a\nb\nc", "a\nB\nc", 10)
  assert.deepEqual(shape(rows), [" a", "-b", "+B", " c"])
  assert.deepEqual(rows.map((r) => r.oldNo), [10, 11, null, 12])
  assert.deepEqual(rows.map((r) => r.newNo), [10, null, 11, 12])
})

test("an insertion shifts only the new side numbers of what follows it", () => {
  const rows = diffLines("a\nc", "a\nb\nc", 5)
  assert.deepEqual(shape(rows), [" a", "+b", " c"])
  assert.deepEqual(rows.map((r) => r.oldNo), [5, null, 6])
  assert.deepEqual(rows.map((r) => r.newNo), [5, 6, 7])
})

test("a deletion", () => {
  assert.deepEqual(shape(diffLines("a\nb\nc", "a\nc")), [" a", "-b", " c"])
})

test("empty old text is all additions, empty new text is all deletions", () => {
  assert.deepEqual(shape(diffLines("", "x\ny")), ["+x", "+y"])
  assert.deepEqual(shape(diffLines("x\ny", "")), ["-x", "-y"])
  assert.deepEqual(diffLines("", ""), [])
})

test("identical text yields no rows", () => {
  assert.deepEqual(diffLines("a\nb", "a\nb"), [])
})

test("CRLF in one side does not turn every line into a change", () => {
  assert.deepEqual(shape(diffLines("a\r\nb\r\nc", "a\nB\nc")), [" a", "-b", "+B", " c"])
})

test("context is limited to three lines each side", () => {
  const body = Array.from({ length: 20 }, (_, i) => `l${i}`)
  const changed = body.slice()
  changed[10] = "X"
  const rows = diffLines(body.join("\n"), changed.join("\n"), 1)
  assert.equal(rows.length, 3 + 2 + 3)
  assert.equal(rows[0]?.oldNo, 8)
  assert.equal(rows.at(-1)?.oldNo, 14)
})

test("an interior common line stays context between two changes", () => {
  assert.deepEqual(shape(diffLines("a\nb\nc\nd\ne", "a\nB\nc\nD\ne")), [" a", "-b", "+B", " c", "-d", "+D", " e"])
})

test("a huge block falls back to remove-all then add-all instead of a quadratic table", () => {
  const a = Array.from({ length: 2000 }, (_, i) => `a${i}`)
  const b = Array.from({ length: 2000 }, (_, i) => `b${i}`)
  const started = Date.now()
  const rows = diffLines(a.join("\n"), b.join("\n"))
  assert.ok(Date.now() - started < 500)
  assert.equal(rows.length, 4000)
  assert.equal(rows[0]?.kind, "del")
  assert.equal(rows[2000]?.kind, "add")
})

test("a long identical prefix is peeled off without the table", () => {
  const body = Array.from({ length: 5000 }, (_, i) => `l${i}`)
  const changed = body.slice()
  changed[4999] = "end"
  assert.deepEqual(shape(diffLines(body.join("\n"), changed.join("\n"))).slice(-2), ["-l4999", "+end"])
})

test("pairRows sets removals opposite the additions that follow", () => {
  const pairs = pairRows(diffLines("a\nb\nc\nd", "a\nB\nC\nX\nd"))
  assert.deepEqual(
    pairs.map((p) => [p.left?.text ?? null, p.right?.text ?? null]),
    [["a", "a"], ["b", "B"], ["c", "C"], [null, "X"], ["d", "d"]],
  )
})

test("pairRows leaves the short side empty for a pure deletion", () => {
  const pairs = pairRows(diffLines("a\nb\nc", "a\nc"))
  assert.deepEqual(pairs.map((p) => [p.left?.text ?? null, p.right?.text ?? null]), [["a", "a"], ["b", null], ["c", "c"]])
})

test("numberWidth covers the largest line number on either side", () => {
  assert.equal(numberWidth(diffLines("a", "b", 98)), 2)
  assert.equal(numberWidth(diffLines("a", "b\nc\nd", 98)), 3)
})

test("parseUnifiedDiff numbers rows from hunk headers and ignores file naming lines", () => {
  const rows = parseUnifiedDiff("--- a/x.py\n+++ b/x.py\n@@ -2,3 +2,3 @@\n import os\n-DEBUG = False\n+DEBUG = True\n PORT = 1\n\\ No newline at end of file")
  assert.deepEqual(
    rows.map((r) => [r.kind, r.text, r.oldNo, r.newNo]),
    [["ctx", "import os", 2, 2], ["del", "DEBUG = False", 3, null], ["add", "DEBUG = True", null, 3], ["ctx", "PORT = 1", 4, 4]],
  )
})

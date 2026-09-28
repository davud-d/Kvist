import assert from "node:assert/strict"
import { test } from "node:test"

import { fitSegments, highlightLines, languageOf } from "../src/highlight.js"

const plain = (row: { text: string }[]) => row.map((segment) => segment.text).join("")

test("the language comes from the file extension, and unknown files have none", () => {
  assert.equal(languageOf("src/ui/app.tsx"), "typescript")
  assert.equal(languageOf("main.PY"), "python")
  assert.equal(languageOf("deploy.sh"), "bash")
  assert.equal(languageOf("Makefile"), null)
  assert.equal(languageOf("notes.xyz"), null)
})

test("code becomes rows of scoped segments, with entities decoded", () => {
  const rows = highlightLines('return a < b ? "x" : 1', "typescript")
  assert.equal(rows.length, 1)
  assert.equal(plain(rows[0]!), 'return a < b ? "x" : 1')
  const scopes = Object.fromEntries(rows[0]!.map((segment) => [segment.text, segment.scope]))
  assert.equal(scopes["return"], "keyword")
  assert.equal(scopes['"x"'], "string")
  assert.equal(scopes["1"], "number")
})

test("a token that spans lines is split at the line breaks", () => {
  const rows = highlightLines("/* one\ntwo */\nx = 1", "javascript")
  assert.deepEqual(rows.map(plain), ["/* one", "two */", "x = 1"])
  assert.equal(rows[0]?.[0]?.scope, "comment")
  assert.equal(rows[1]?.[0]?.scope, "comment")
})

test("without a language the rows are plain, one per line", () => {
  const rows = highlightLines("a\n\nb", null)
  assert.deepEqual(rows.map(plain), ["a", "", "b"])
  assert.ok(rows.every((row) => row.every((segment) => segment.scope === null)))
})

test("fitting pads a short row and cuts a long one to exactly the width", () => {
  const short = fitSegments([{ text: "ab", scope: "keyword" }], 5)
  assert.equal(plain(short), "ab   ")
  const long = fitSegments([{ text: "abcdefghij", scope: null }], 5)
  assert.equal(plain(long), "abcd…")
  assert.equal(plain(fitSegments([{ text: "\tx", scope: null }], 8)), "    x   ")
  assert.deepEqual(fitSegments([], 0), [])
})

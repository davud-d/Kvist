import assert from "node:assert/strict"
import { test } from "node:test"

import { syntaxColor } from "../ui/skins/claude-code/syntax.js"

test("scopes get colours, and plain text keeps the terminal's own", () => {
  assert.equal(syntaxColor(null), undefined)
  assert.equal(syntaxColor("keyword"), syntaxColor("literal"))
  assert.notEqual(syntaxColor("keyword"), syntaxColor("string"))
  assert.notEqual(syntaxColor("number"), syntaxColor("comment"))
  assert.equal(syntaxColor("title.function_"), syntaxColor("params"))
  assert.notEqual(syntaxColor("title.class_"), syntaxColor("title.function_"))
  assert.equal(syntaxColor("variable"), undefined)
})

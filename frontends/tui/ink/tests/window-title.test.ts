import assert from "node:assert/strict"
import { test } from "node:test"

import { formatWindowTitle } from "../window-title.js"

test("an untitled session is just Kvist", () => {
  assert.equal(formatWindowTitle(undefined), "Kvist")
  assert.equal(formatWindowTitle("  "), "Kvist")
})

test("the title follows the product name and loses control characters", () => {
  assert.equal(formatWindowTitle("Fix the parser"), "Kvist | Fix the parser")
  assert.equal(formatWindowTitle("a\u0007b\u001b]0;x"), "Kvist | ab]0;x")
})

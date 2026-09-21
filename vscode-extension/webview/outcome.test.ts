import assert from "node:assert/strict"
import { test } from "node:test"

import { classifyRun, worseOf } from "./outcome"

test("the worse of two tones wins, in either order", () => {
  assert.equal(worseOf("ok", "warn"), "warn")
  assert.equal(worseOf("bad", "warn"), "bad")
  assert.equal(worseOf("warn", "ok"), "warn")
  assert.equal(worseOf("ok", "ok"), "ok")
})

const run = (output: string, exit_code: number | null = 0, ok = exit_code === 0) => classifyRun({ ok, exit_code, output })

test("a clean exit with plain output is green", () => {
  assert.equal(run("..............\n14 passed in 0.52s"), "ok")
  assert.equal(run(""), "ok")
})

test("a non-zero exit is red whatever the output says", () => {
  assert.equal(run("all good", 1), "bad")
  assert.equal(run("", 4), "bad")
})

test("a command that did not finish, or was denied, is red", () => {
  assert.equal(run("", null, false), "bad")
})

test("an error printed by a tool that exits 0 is still red", () => {
  assert.equal(run("build step\nERROR: could not resolve dependency"), "bad")
  assert.equal(run("error: unused import", 0), "bad")
  assert.equal(run("Traceback (most recent call last):\n  File x"), "bad")
  assert.equal(run("FAILED tests/test_a.py::test_b"), "bad")
})

test("a warning on a clean exit is yellow", () => {
  assert.equal(run("warning: unused variable `x`"), "warn")
  assert.equal(run("WARN  peer dependency missing"), "warn")
  assert.equal(run("DeprecationWarning: module is deprecated"), "warn")
})

test("counts of zero are not trouble", () => {
  assert.equal(run("14 passed, 0 warnings"), "ok")
  assert.equal(run("no errors found"), "ok")
  assert.equal(run("errors: 0, warnings: 0"), "ok")
  assert.equal(run("Found 0 errors."), "ok")
})

test("nonzero counts still count", () => {
  assert.equal(run("0 errors, 2 warnings"), "warn")
})

test("prose that mentions the words is not flagged", () => {
  assert.equal(run("see the docs on error handling and how a warning is shown"), "ok")
})

test("an error outranks a warning", () => {
  assert.equal(run("warning: a\nERROR: b"), "bad")
})

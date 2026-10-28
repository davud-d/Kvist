import assert from "node:assert/strict"
import { test } from "node:test"

import { gistOf, jobOutcome, plainStatus } from "./jobText"

test("the gist is the last line that says something, without its rules", () => {
  assert.equal(gistOf("a\n=== 1 failed, 77 passed in 7.28s ===\n\n"), "1 failed, 77 passed in 7.28s")
  assert.equal(gistOf("FAILED x\n!!!!!!!!!!!!!!\n"), "FAILED x")
  assert.equal(gistOf(""), "")
})

test("outcomes are words, not exit codes", () => {
  assert.equal(jobOutcome("exited", 0, false), "Done")
  assert.equal(jobOutcome("exited", 1, false), "Failed")
  assert.equal(jobOutcome("exited", 1, true), "Не удалось")
  assert.equal(jobOutcome("timed_out", null, true), "Время вышло")
  assert.equal(jobOutcome("stopped", null, false), "Stopped")
})

test("job ids never reach the status line", () => {
  assert.equal(plainStatus("смотрит вывод job_1", true), "смотрит вывод фоновой команды")
  assert.equal(plainStatus("reading job_12 output", false), "reading the background command output")
  assert.equal(plainStatus("no ids here", false), "no ids here")
})

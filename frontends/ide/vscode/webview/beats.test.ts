import assert from "node:assert/strict"
import { test } from "node:test"

import { beatFrames, BEATS_TOTAL_MS } from "./beats"

test("the switch moves in three beats of 50, 75 and 30 ms", () => {
  assert.equal(BEATS_TOTAL_MS, 155)
  const frames = beatFrames((progress) => ({ opacity: progress }))
  const at = (ms: number) => ms / BEATS_TOTAL_MS
  assert.deepEqual(frames.map((frame) => frame.offset), [0, at(50), at(125), 1])
})

test("it starts and ends where it is told, the run covers most of the way, the last frame has no easing", () => {
  const frames = beatFrames((progress) => ({ opacity: progress }))
  assert.equal(frames[0].opacity, 0)
  assert.equal(frames.at(-1)?.opacity, 1)
  assert.ok(Number(frames[2].opacity) - Number(frames[1].opacity) > 0.5)
  assert.ok(frames.slice(0, 3).every((frame) => typeof frame.easing === "string"))
  assert.equal(frames.at(-1)?.easing, undefined)
})

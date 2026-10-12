import assert from "node:assert/strict"
import { test } from "node:test"

import { coolDown, shimmerColor } from "../ui/skins/claude-code/shimmer.js"

function rgb(hex: string): number[] {
  return [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16))
}

// Mean colour of a line of characters over one ripple period.
function average(seconds: number): number[] {
  const sum = [0, 0, 0]
  let count = 0
  for (let t = 0; t < 2400; t += 100) {
    for (let index = 0; index < 20; index++) {
      rgb(shimmerColor(seconds, t, index)).forEach((v, i) => (sum[i]! += v))
      count++
    }
  }
  return sum.map((v) => v / count)
}

test("the spinner is blue at the start of a wait", () => {
  const [r, , b] = average(0)
  assert.ok(b! > r!)
})

test("the wait turns orange within seconds, then gold, then red, without jumps", () => {
  const [r1, , b1] = average(6)
  const [, g1] = average(6)
  const [, g2] = average(30)
  const [r3, g3, b3] = average(200)
  assert.ok(r1! > b1!)
  assert.ok(g2! > g1!)
  assert.ok(r3! > b3! && g3! < g2! - 40)
  let previous = average(6)
  for (let seconds = 7; seconds <= 240; seconds++) {
    const now = average(seconds)
    assert.ok(Math.max(...now.map((v, i) => Math.abs(v - previous[i]!))) < 8)
    previous = now
  }
})

test("a long wait still ripples with the blue", () => {
  const blues = Array.from({ length: 24 }, (_, i) => rgb(shimmerColor(200, i * 100, 0))[2]!)
  assert.ok(Math.max(...blues) - Math.min(...blues) > 40)
})

test("the shown wait rises at once and falls back gradually", () => {
  assert.equal(coolDown(5, 40, 0.12), 40)
  const step = coolDown(40, 0, 0.12)
  assert.ok(step < 40 && step > 30)
  let shown = 40
  for (let frame = 0; frame < 50; frame++) shown = coolDown(shown, 0, 0.12)
  assert.ok(shown < 1)
})

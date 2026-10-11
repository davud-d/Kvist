import assert from "node:assert/strict"
import { test } from "node:test"

import { shimmerColor } from "../ui/skins/claude-code/shimmer.js"

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

test("the wait turns orange, then gold, without jumps", () => {
  const [r1, , b1] = average(60)
  const [, g1] = average(45)
  const [, g2] = average(150)
  assert.ok(r1! > b1!)
  assert.ok(g2! > g1!)
  let previous = average(0)
  for (let seconds = 1; seconds <= 200; seconds++) {
    const now = average(seconds)
    assert.ok(Math.max(...now.map((v, i) => Math.abs(v - previous[i]!))) < 8)
    previous = now
  }
})

test("a long wait still ripples with the blue", () => {
  const blues = Array.from({ length: 24 }, (_, i) => rgb(shimmerColor(200, i * 100, 0))[2]!)
  assert.ok(Math.max(...blues) - Math.min(...blues) > 40)
})

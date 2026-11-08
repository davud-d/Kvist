import assert from "node:assert/strict"
import { test } from "node:test"

import { render } from "ink-testing-library"
import { createElement } from "react"

import { MASCOT_OCTANTS as WINTER_OCTANTS, MASCOT_QUADRANTS as WINTER_QUADRANTS } from "../ui/skins/claude-code/mascot-winter-frames.js"
import { MASCOT_PNGS as WINTER_PNGS } from "../ui/skins/claude-code/mascot-winter-images.js"
import { MASCOT_COLUMNS, MASCOT_ROWS, Mascot, nextFrame } from "../ui/skins/claude-code/mascot.js"
import { WINTER_FRAME_MS, isWinter } from "../ui/skins/claude-code/winter.js"

const plain = (text: string | undefined) => (text ?? "").replace(/\x1b\[[0-9;]*m/g, "")
const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

test("it is winter in January and February, in any year, and in no other month", () => {
  assert.equal(isWinter(new Date(2027, 0, 1), {}), true)
  assert.equal(isWinter(new Date(2028, 1, 29, 23, 59), {}), true)
  assert.equal(isWinter(new Date(2026, 11, 31), {}), false)
  assert.equal(isWinter(new Date(2027, 2, 1), {}), false)
  assert.equal(isWinter(new Date(2026, 10, 8), {}), false)
})

test("KVIST_WINTER=1 shows it in any month", () => {
  assert.equal(isWinter(new Date(2026, 6, 1), { KVIST_WINTER: "1" }), true)
  assert.equal(isWinter(new Date(2026, 6, 1), { KVIST_WINTER: "0" }), false)
  assert.equal(isWinter(new Date(2027, 0, 15), { KVIST_WINTER: "0" }), false)
})

test("the winter frames are the animation's nine, the size of the greeting's", () => {
  assert.equal(WINTER_QUADRANTS.length, 9)
  assert.equal(WINTER_OCTANTS.length, 9)
  assert.equal(WINTER_PNGS.length, 9)
  for (const frame of [...WINTER_QUADRANTS, ...WINTER_OCTANTS]) {
    assert.equal(frame.length, MASCOT_ROWS)
    for (const row of frame) assert.equal(row.length, MASCOT_COLUMNS)
  }
})

test("outside winter the greeting ends on its last frame; in winter the nine frames then loop", () => {
  const greeting = 6
  assert.equal(nextFrame(greeting - 1, false), null)
  assert.deepEqual(nextFrame(0, false), { index: 1, delay: 500 })
  assert.deepEqual(nextFrame(greeting - 1, true), { index: greeting, delay: 500 })
  assert.deepEqual(nextFrame(greeting, true), { index: greeting + 1, delay: WINTER_FRAME_MS })
  assert.deepEqual(nextFrame(greeting + 8, true), { index: greeting, delay: WINTER_FRAME_MS })
})

test("in winter the squirrel goes on moving after the greeting, and not otherwise", async () => {
  const before = { winter: process.env.KVIST_WINTER, heist: process.env.KVIST_HEIST }
  try {
    process.env.KVIST_HEIST = "0"
    for (const [winter, moves] of [["1", true], ["0", false]] as const) {
      process.env.KVIST_WINTER = winter
      const app = render(createElement(Mascot))
      await wait(3200)
      const seen = new Set<string>()
      for (let at = 0; at < 6; at++) {
        seen.add(plain(app.lastFrame()))
        await wait(WINTER_FRAME_MS + 20)
      }
      app.unmount()
      assert.equal(seen.size > 1, moves, `KVIST_WINTER=${winter}`)
    }
  } finally {
    for (const [name, value] of [["KVIST_WINTER", before.winter], ["KVIST_HEIST", before.heist]] as const) {
      if (value === undefined) delete process.env[name]
      else process.env[name] = value
    }
  }
})

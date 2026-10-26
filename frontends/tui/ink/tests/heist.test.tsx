import assert from "node:assert/strict"
import { test } from "node:test"

import { Text } from "ink"
import { render } from "ink-testing-library"
import { createElement } from "react"

import { ViewContext } from "../ui/context.js"
import { ClaudeScreen } from "../ui/skins/claude-code/claude.js"
import { HEIST_STARS, heistLine, isHeistDay } from "../ui/skins/claude-code/heist.js"
import { MASCOT_OCTANTS as HEIST_OCTANTS, MASCOT_QUADRANTS as HEIST_QUADRANTS } from "../ui/skins/claude-code/mascot-heist-frames.js"
import { MASCOT_PNGS as HEIST_PNGS } from "../ui/skins/claude-code/mascot-heist-images.js"
import { MASCOT_OCTANTS, MASCOT_QUADRANTS } from "../ui/skins/claude-code/mascot-frames.js"
import { MASCOT_PNGS } from "../ui/skins/claude-code/mascot-images.js"
import { Mascot } from "../ui/skins/claude-code/mascot.js"

const plain = (text: string | undefined) => (text ?? "").replace(/\x1b\[[0-9;]*m/g, "")

test("the heist squirrel is on the 19th of November, in any year, and on no other day", () => {
  assert.equal(isHeistDay(new Date(2026, 10, 19), {}), true)
  assert.equal(isHeistDay(new Date(2027, 10, 19, 23, 59), {}), true)
  assert.equal(isHeistDay(new Date(2026, 10, 18), {}), false)
  assert.equal(isHeistDay(new Date(2026, 10, 20), {}), false)
  assert.equal(isHeistDay(new Date(2026, 9, 19), {}), false)
})

test("KVIST_HEIST=1 shows it on any day", () => {
  assert.equal(isHeistDay(new Date(2026, 0, 1), { KVIST_HEIST: "1" }), true)
  assert.equal(isHeistDay(new Date(2026, 0, 1), { KVIST_HEIST: "0" }), false)
})

test("the heist set has the same frames, in the same size, as the everyday one", () => {
  assert.equal(HEIST_QUADRANTS.length, MASCOT_QUADRANTS.length)
  assert.equal(HEIST_OCTANTS.length, MASCOT_OCTANTS.length)
  assert.equal(HEIST_PNGS.length, MASCOT_PNGS.length)
  for (const [heist, plainSet] of [[HEIST_QUADRANTS, MASCOT_QUADRANTS], [HEIST_OCTANTS, MASCOT_OCTANTS]] as const) {
    for (const [at, frame] of heist.entries()) {
      assert.equal(frame.length, plainSet[at]!.length)
      for (const row of frame) assert.equal(row.length, plainSet[at]![0]!.length)
    }
  }
})

test("the line beside the squirrel follows the language", () => {
  assert.match(heistLine({ LANG: "ru_RU.UTF-8" }), /Ключи не мои/)
  assert.match(heistLine({ LANG: "en_US.UTF-8" }), /Not my keys/)
  assert.equal(HEIST_STARS.length, 5)
})

function welcome(): string {
  const info = { provider: "p", model: "m", role: "coder", session: "s", kvistVersion: "1" }
  const view = { workspace: "/w", info, effort: "high", pointer: null, expanded: false }
  const app = render(createElement(ViewContext.Provider, { value: view }, createElement(ClaudeScreen, { items: [{ id: 1, kind: "banner" }], showDebug: false, followKey: 0, live: null, spinner: null }, createElement(Text, null, "INPUT"))))
  const frame = plain(app.lastFrame())
  app.unmount()
  return frame
}

test("the welcome block carries the stars and the line on the day only", () => {
  const before = { heist: process.env.KVIST_HEIST, lang: process.env.KVIST_LANG }
  try {
    process.env.KVIST_LANG = "en"
    process.env.KVIST_HEIST = "0"
    assert.doesNotMatch(welcome(), /★★★★★/)
    process.env.KVIST_HEIST = "1"
    assert.match(welcome(), /★★★★★ Not my keys/)
  } finally {
    for (const [name, value] of [["KVIST_HEIST", before.heist], ["KVIST_LANG", before.lang]] as const) {
      if (value === undefined) delete process.env[name]
      else process.env[name] = value
    }
  }
})

test("Mascot draws the heist frames on the day and the usual ones otherwise", () => {
  const before = process.env.KVIST_HEIST
  try {
    process.env.KVIST_HEIST = "0"
    const usual = render(createElement(Mascot))
    const usualFrame = plain(usual.lastFrame())
    usual.unmount()
    process.env.KVIST_HEIST = "1"
    const heist = render(createElement(Mascot))
    const heistFrame = plain(heist.lastFrame())
    heist.unmount()
    assert.notEqual(heistFrame, usualFrame)
  } finally {
    if (before === undefined) delete process.env.KVIST_HEIST
    else process.env.KVIST_HEIST = before
  }
})

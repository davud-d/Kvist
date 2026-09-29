import assert from "node:assert/strict"
import { test } from "node:test"

import { Text } from "ink"
import { render } from "ink-testing-library"
import { createElement } from "react"

import { Readable } from "node:stream"
import { Pointer } from "../pointer.js"
import type { Item } from "../state.js"
import { ClaudeScreen } from "../ui/skins/claude-code/claude.js"
import { ViewContext } from "../ui/context.js"

import { MASCOT_OCTANTS, MASCOT_QUADRANTS } from "../ui/skins/claude-code/mascot-frames.js"
const MASCOT_FRAMES = MASCOT_QUADRANTS
import { MASCOT_COLUMNS, MASCOT_FPS, MASCOT_ROWS, Mascot, MascotFrame, mascotGlyphs, runsOf } from "../ui/skins/claude-code/mascot.js"

const plain = (text: string | undefined) => (text ?? "").replace(/\x1b\[[0-9;]*m/g, "")
const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

test("the animation has six frames of the same size, a square picture of cells", () => {
  assert.equal(MASCOT_FRAMES.length, 6)
  assert.equal(MASCOT_COLUMNS, 2 * MASCOT_ROWS)
  assert.ok(MASCOT_ROWS >= 6, "big enough to read")
  for (const frame of MASCOT_FRAMES) {
    assert.equal(frame.length, MASCOT_ROWS)
    for (const row of frame) assert.equal(row.length, MASCOT_COLUMNS)
  }
})

test("no frame paints a background of its own around the squirrel", () => {
  const corners = MASCOT_FRAMES.flatMap((frame) => [frame[0]![0]!, frame[0]!.at(-1)!, frame.at(-1)![0]!, frame.at(-1)!.at(-1)!])
  // Every frame leaves at least one corner to the terminal, and no cell is painted with the dark backdrop.
  assert.ok(corners.some(([, fg, bg]) => fg === null && bg === null))
  const dark = (hex: string | null) => hex !== null && [1, 3, 5].every((at) => Number.parseInt(hex.slice(at, at + 2), 16) < 60)
  for (const frame of MASCOT_FRAMES) for (const row of frame) for (const [, fg, bg] of row) assert.ok(!(dark(bg) && dark(fg)), `${fg} on ${bg}`)
})

test("cells that share both colours are one run, so blank space is a single node", () => {
  const runs = runsOf([[" ", null, null], [" ", null, null], ["▟", "#e08645", null], ["▟", "#e08645", null], ["▚", "#e08645", "#c2541e"]])
  assert.deepEqual(runs, [
    { text: "  ", fg: null, bg: null },
    { text: "▟▟", fg: "#e08645", bg: null },
    { text: "▚", fg: "#e08645", bg: "#c2541e" },
  ])
})

test("it plays once at two frames a second and rests on the last frame", async () => {
  assert.equal(MASCOT_FPS, 2)
  const app = render(createElement(Mascot))
  const first = plain(app.lastFrame())
  const last = render(createElement(MascotFrame, { index: MASCOT_FRAMES.length - 1 }))
  const final = plain(last.lastFrame())
  last.unmount()
  assert.notEqual(first, final)
  await wait(700)
  const second = plain(app.lastFrame())
  assert.notEqual(second, first, "the second frame follows after half a second")
  await wait(2800)
  assert.equal(plain(app.lastFrame()), final, "the last frame is reached after five steps")
  await wait(1200)
  assert.equal(plain(app.lastFrame()), final, "and it stays there")
  app.unmount()
})

function pictureScreen() {
  const written: unknown[] = []
  const output = { rows: 24, columns: 100, isTTY: true, write: (chunk: unknown) => (written.push(chunk), true) }
  const pointer = new Pointer(new Readable({ read() {} }) as unknown as NodeJS.ReadStream, output as unknown as NodeJS.WriteStream, { pictures: "kitty" })
  pointer.stdout.write(Array(24).fill("").join("\n"))
  const info = { provider: "p", model: "m", role: "coder", session: "s", kvistVersion: "1" }
  const view = { workspace: "/w", info, effort: "high", pointer, expanded: false }
  const screen = (items: Item[]) =>
    createElement(ViewContext.Provider, { value: view }, createElement(ClaudeScreen, { items, showDebug: false, followKey: 0, live: null, spinner: null }, createElement(Text, null, "INPUT")))
  return { written, screen }
}

const banner: Item = { id: 1, kind: "banner" }
const notes = (count: number): Item[] => Array.from({ length: count }, (_, i) => ({ id: i + 2, kind: "note", tone: "info", text: `row ${i}` }))
const drawn = (written: unknown[]) => written.map(String).filter((chunk) => chunk.includes("\x1b_Ga=T"))
const removed = (written: unknown[]) => written.map(String).filter((chunk) => chunk.includes("\x1b_Ga=d"))

test("a terminal that draws pictures gets the squirrel over the banner's cells, blank underneath", async () => {
  const { written, screen } = pictureScreen()
  const app = render(screen([banner]))
  await wait(150)
  const placed = drawn(written)
  assert.equal(placed.length, 1)
  assert.match(placed[0]!, new RegExp(`^\\x1b\\[2;1H\\x1b_Ga=T,f=100,i=1,p=1,c=${MASCOT_COLUMNS},r=${MASCOT_ROWS},`))
  assert.doesNotMatch(plain(app.lastFrame()), /[▀▄▌▐▖▗▘▝▚▞▛▜▙▟█]/, "no cell art under the picture")
  app.unmount()
})

test("the animation's frames go out as pictures at two a second and the last one stays", async () => {
  const { written, screen } = pictureScreen()
  const app = render(screen([banner]))
  await wait(3600)
  assert.equal(drawn(written).length, MASCOT_FRAMES.length)
  await wait(1200)
  assert.equal(drawn(written).length, MASCOT_FRAMES.length, "nothing more is sent once it rests")
  app.unmount()
})

test("when the banner is partly above the view the picture is cut like the text, not dropped", async () => {
  const { written, screen } = pictureScreen()
  const app = render(screen([banner]))
  await wait(150)
  // The viewport is 23 rows and the banner block 10 (a margin, 8 rows of squirrel, a margin); 16 more rows push 3 of them above it.
  app.rerender(screen([banner, ...notes(16)]))
  await wait(150)
  const cut = drawn(written).at(-1)
  assert.ok(cut, "a picture is on screen")
  assert.match(cut, /^\x1b\[1;1H\x1b_Ga=T,f=100,i=1,p=1,c=16,r=6,y=64,h=192,/)
  assert.equal(removed(written).length, 0, "it was never taken away")
  app.unmount()
})

test("when the banner scrolls out of view the picture is taken away", async () => {
  const { written, screen } = pictureScreen()
  const app = render(screen([banner]))
  await wait(150)
  assert.equal(removed(written).length, 0)
  app.rerender(screen([banner, ...notes(60)]))
  await wait(150)
  assert.equal(removed(written).length, 1)
  app.unmount()
})

const QUADRANT_CHARS = new Set([..." ▘▝▀▖▌▞▛▗▚▐▜▄▙▟█"])
// What an octant cell may be: the 230 characters of Unicode 16's octant block, or the older ones that draw the other patterns.
const OCTANT_OTHERS = new Set([..." ▘▝▀▖▌▞▛▗▚▐▜▄▙▟█▂▆\u{1fb82}\u{1fb85}\u{1fbe6}\u{1fbe7}"])
const isOctant = (char: string) => OCTANT_OTHERS.has(char) || ((char.codePointAt(0) ?? 0) >= 0x1cd00 && (char.codePointAt(0) ?? 0) <= 0x1cde5)

test("both cell sets are the same size and use only characters of their own kind", () => {
  for (const [frames, allowed] of [[MASCOT_QUADRANTS, (char: string) => QUADRANT_CHARS.has(char)], [MASCOT_OCTANTS, isOctant]] as const) {
    assert.equal(frames.length, 6)
    for (const frame of frames) {
      assert.equal(frame.length, MASCOT_ROWS)
      for (const row of frame) {
        assert.equal(row.length, MASCOT_COLUMNS)
        for (const [char, fg, bg] of row) {
          assert.ok(allowed(char), `U+${char.codePointAt(0)?.toString(16)} is not allowed`)
          assert.ok(char !== " " || (fg === null && bg === null), "a blank cell has no colours")
        }
      }
    }
  }
  assert.ok(MASCOT_OCTANTS.flat(2).some(([char]) => (char.codePointAt(0) ?? 0) >= 0x1cd00), "the octant set really uses octants")
})

test("octants are chosen for Windows Terminal, quadrants elsewhere, and KVIST_GLYPHS decides when it is set", () => {
  assert.equal(mascotGlyphs({ WT_SESSION: "abc" }), "octants")
  assert.equal(mascotGlyphs({}), "quadrants")
  assert.equal(mascotGlyphs({ TERM: "xterm-256color" }), "quadrants")
  assert.equal(mascotGlyphs({ WT_SESSION: "abc", KVIST_GLYPHS: "quadrants" }), "quadrants")
  assert.equal(mascotGlyphs({ KVIST_GLYPHS: "OCTANTS" }), "octants")
  assert.equal(mascotGlyphs({ KVIST_GLYPHS: "sextants" }), "quadrants")
})

test("a frame is drawn from whichever set it is given", () => {
  const drawn = (frames: typeof MASCOT_OCTANTS) => {
    const app = render(createElement(MascotFrame, { index: 5, frames }))
    const out = plain(app.lastFrame())
    app.unmount()
    return out
  }
  assert.ok([...drawn(MASCOT_OCTANTS)].some((char) => (char.codePointAt(0) ?? 0) >= 0x1cd00))
  assert.ok(![...drawn(MASCOT_QUADRANTS)].some((char) => (char.codePointAt(0) ?? 0) >= 0x1cd00))
})

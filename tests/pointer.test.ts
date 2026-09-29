import assert from "node:assert/strict"
import { Readable } from "node:stream"
import { test } from "node:test"

import type { ClipboardResult } from "../clipboard.js"
import { Pointer, extractMouse, splitFrame } from "../pointer.js"
import { MASCOT_PNGS } from "../ui/mascot-images.js"

const press = (col: number, row: number) => `\x1b[<0;${col + 1};${row + 1}M`
const move = (col: number, row: number) => `\x1b[<32;${col + 1};${row + 1}M`
const release = (col: number, row: number) => `\x1b[<0;${col + 1};${row + 1}m`
const wheel = (down: boolean) => `\x1b[<${down ? 65 : 64};5;5M`

const FRAME = ["hello world", "second row", "third"]

// What Ink writes for a 3-row screen: an erase prefix and the rows.
const frameChunk = (lines = FRAME) => `\x1b[2K\x1b[1A\x1b[2K\x1b[1A\x1b[2K\x1b[G${lines.join("\n")}`

// What a terminal would show after these writes: rows painted in place, cleared on demand.
function screenOf(written: unknown[], rows = 3): string[] {
  const screen: string[] = Array(rows).fill("")
  for (const chunk of written) {
    let text = String(chunk)
    if (text.includes("\x1b[2J")) {
      screen.fill("")
      text = text.replace("\x1b[2J", "")
    }
    for (const match of text.matchAll(/\x1b\[(\d+);1H(.*?)\x1b\[0m\x1b\[K/gs)) screen[Number(match[1]) - 1] = match[2] ?? ""
  }
  return screen
}

function setup(now = () => 0, pictures: "kitty" | "sixel" | null = null) {
  const input = new Readable({ read() {} })
  input.setEncoding("utf8")
  const written: unknown[] = []
  const output = { rows: 3, columns: 20, isTTY: true, write: (chunk: unknown) => (written.push(chunk), true) }
  const copies: string[] = []
  const pointer = new Pointer(input as unknown as NodeJS.ReadStream, output as unknown as NodeJS.WriteStream, {
    copy: (text): ClipboardResult => (copies.push(text), { method: "osc52", chars: text.length }),
    now,
    pictures,
  })
  const feed = (data: string) => input.push(data)
  const read = () => pointer.stdin.read() as string | null
  const paint = (lines = FRAME) => pointer.stdout.write(frameChunk(lines))
  return { pointer, feed, read, paint, written, copies }
}

test("mouse reports are read as 0-based cells, with keys left in the rest", () => {
  const { reports, rest } = extractMouse(`a${press(4, 2)}b${release(9, 2)}`)
  assert.deepEqual(reports, [
    { button: 0, col: 4, row: 2, press: true },
    { button: 0, col: 9, row: 2, press: false },
  ])
  assert.equal(rest, "ab")
})

test("a frame is the rows behind Ink's erase prefix; other writes are not", () => {
  assert.deepEqual(splitFrame(frameChunk(), 3)?.lines, FRAME)
  assert.equal(splitFrame(frameChunk(), 3)?.prefix, "\x1b[2K\x1b[1A\x1b[2K\x1b[1A\x1b[2K\x1b[G")
  assert.deepEqual(splitFrame(FRAME.join("\n"), 3)?.prefix, "")
  assert.equal(splitFrame("\x1b[?25l", 3), null)
  assert.equal(splitFrame(frameChunk(), 4), null)
})

test("Ink never sees a mouse report, and gets the keys that came with it", () => {
  const { feed, read } = setup()
  feed(`x${press(1, 1)}y`)
  assert.equal(read(), "xy")
  feed(press(1, 1))
  assert.equal(read(), null)
})

test("the wheel asks to scroll three lines toward older or newer content", () => {
  const { feed, read, pointer } = setup()
  const seen: number[] = []
  const off = pointer.onWheel((lines) => seen.push(lines))
  feed(wheel(false) + wheel(true))
  read()
  assert.deepEqual(seen, [-3, 3])
  off()
  feed(wheel(true))
  read()
  assert.deepEqual(seen, [-3, 3])
})

test("a drag selects cells, paints them inverted and copies the text on release", () => {
  const { feed, read, paint, written, copies, pointer } = setup()
  const results: ClipboardResult[] = []
  pointer.onCopied((result) => results.push(result))
  paint()
  feed(press(6, 0) + move(9, 0) + release(10, 0))
  read()
  assert.deepEqual(copies, ["world"])
  assert.deepEqual(results, [{ method: "osc52", chars: 5 }])
  assert.equal(screenOf(written)[0], "hello \x1b[7mworld\x1b[27m")
})

test("a frame is painted row by row, never with Ink's erase prefix, and only the rows that changed", () => {
  const { paint, written } = setup()
  paint()
  assert.deepEqual(screenOf(written), FRAME)
  assert.ok(written.every((chunk) => !String(chunk).includes("\x1b[2K\x1b[1A")))
  const before = written.length
  paint()
  assert.equal(written.length, before, "an identical frame writes nothing")
  paint(["hello world", "SECOND ROW", "third"])
  const update = String(written.at(-1))
  assert.match(update, /^\x1b\[2;1HSECOND ROW\x1b\[0m\x1b\[K$/)
  assert.deepEqual(screenOf(written), ["hello world", "SECOND ROW", "third"])
})

test("a cleared screen or a resize repaints every row", () => {
  const { paint, written, pointer } = setup()
  paint()
  pointer.stdout.write("\x1b[2J\x1b[H")
  const before = written.length
  paint()
  assert.match(String(written.at(-1)), /\x1b\[1;1Hhello world.*\x1b\[2;1Hsecond row.*\x1b\[3;1Hthird/s)
  assert.ok(written.length > before)
})

test("a plain click selects nothing and copies nothing", () => {
  const { feed, read, paint, copies } = setup()
  paint()
  feed(press(2, 1) + release(2, 1))
  read()
  assert.deepEqual(copies, [])
})

test("double-click takes the word and triple-click the row", () => {
  let time = 0
  const { feed, read, paint, copies } = setup(() => time)
  paint()
  feed(press(7, 0) + release(7, 0))
  time = 100
  feed(press(7, 0) + release(7, 0))
  read()
  assert.deepEqual(copies, ["world"])
  time = 200
  feed(press(7, 0) + release(7, 0))
  read()
  assert.deepEqual(copies, ["world", "hello world"])
  time = 2000
  feed(press(7, 0) + release(7, 0))
  read()
  assert.equal(copies.length, 2)
})

test("frames written while a selection is active keep it painted; a key clears it", () => {
  const { feed, read, paint, written } = setup()
  paint()
  feed(press(0, 2) + move(2, 2) + release(2, 2))
  read()
  paint(["hello world", "second row", "third"])
  assert.equal(screenOf(written)[2], "\x1b[7mthi\x1b[27mrd")
  feed("a")
  assert.equal(read(), "a")
  assert.equal(screenOf(written)[2], "third")
})

test("page keys leave a selection alone", () => {
  const { feed, read, paint, written } = setup()
  paint()
  feed(press(0, 2) + move(2, 2) + release(2, 2))
  read()
  const before = written.length
  feed("\x1b[5~")
  assert.equal(read(), "\x1b[5~")
  assert.equal(written.length, before)
})

const PICTURE = { png: "QUJD", key: "0", row: 1, col: 2, columns: 16, rows: 8, skipTop: 0, skipBottom: 0 }
const placements = (written: unknown[]) => written.filter((chunk) => String(chunk).includes("\x1b_Ga=T"))
const deletions = (written: unknown[]) => written.filter((chunk) => String(chunk).includes("\x1b_Ga=d"))

const CUT = { ...PICTURE, row: -2, skipTop: 2 }

test("kitty: a picture partly above the view is drawn from the first visible row, showing the band that is in view", () => {
  const { pointer, paint, written } = setup(() => 0, "kitty")
  paint()
  pointer.showImage({ ...CUT, png: MASCOT_PNGS[5]! })
  // 8 rows of 256 pixels: 2 rows cut off the top take 64 pixels, 6 rows are left, 192 pixels.
  assert.match(placements(written)[0] as string, /^\x1b\[1;3H\x1b_Ga=T,f=100,i=1,p=1,c=16,r=6,y=64,h=192,/)
})

test("kitty: a picture cut at the bottom shows its top band from where it starts", () => {
  const { pointer, paint, written } = setup(() => 0, "kitty")
  paint()
  pointer.showImage({ ...PICTURE, png: MASCOT_PNGS[5]!, row: 0, skipBottom: 3 })
  assert.match(placements(written)[0] as string, /^\x1b\[1;3H\x1b_Ga=T,f=100,i=1,p=1,c=16,r=5,y=0,h=160,/)
})

test("kitty: scrolling a cut picture sends it again with the new band", () => {
  const { pointer, paint, written } = setup(() => 0, "kitty")
  paint()
  pointer.showImage({ ...PICTURE, png: MASCOT_PNGS[5]!, row: -1, skipTop: 1 })
  pointer.showImage({ ...PICTURE, png: MASCOT_PNGS[5]!, row: -2, skipTop: 2 })
  assert.equal(placements(written).length, 2)
})

test("a picture waits for the first frame, then is drawn once and not again while it is unchanged", () => {
  const { pointer, paint, written } = setup(() => 0, "kitty")
  pointer.showImage(PICTURE)
  assert.equal(placements(written).length, 0)
  paint()
  assert.equal(placements(written).length, 1)
  assert.match(String(placements(written)[0]), /^\x1b\[2;3H\x1b_Ga=T,f=100,i=1,p=1,c=16,r=8/)
  paint(["hello world", "SECOND ROW", "third"])
  pointer.showImage({ ...PICTURE })
  assert.equal(placements(written).length, 1, "text changing around it does not resend it")
})

test("a new frame or a new place sends the picture again, and null takes it away", () => {
  const { pointer, paint, written } = setup(() => 0, "kitty")
  paint()
  pointer.showImage(PICTURE)
  pointer.showImage({ ...PICTURE, key: "1" })
  pointer.showImage({ ...PICTURE, key: "1", row: 0 })
  assert.equal(placements(written).length, 3)
  pointer.showImage(null)
  assert.equal(deletions(written).length, 1)
  pointer.showImage(null)
  assert.equal(deletions(written).length, 1, "nothing to remove twice")
})

test("clearing the screen loses the picture, so the next frame draws it again", () => {
  const { pointer, paint, written } = setup(() => 0, "kitty")
  paint()
  pointer.showImage(PICTURE)
  pointer.stdout.write("\x1b[2J\x1b[H")
  paint()
  assert.equal(placements(written).length, 2)
})

test("a terminal without pictures is never sent one", () => {
  const { pointer, paint, written } = setup(() => 0)
  paint()
  pointer.showImage(PICTURE)
  pointer.stop()
  assert.equal(placements(written).length + deletions(written).length, 0)
})

const SIXEL_PICTURE = { png: MASCOT_PNGS[5]!, key: "5", row: 0, col: 1, columns: 4, rows: 2, skipTop: 0, skipBottom: 0 }
const sixels = (written: unknown[]) => written.map(String).filter((chunk) => chunk.includes("\x1bP0;1;0q"))

test("Sixel: a picture is sized for the cells it covers, drawn where they are, and not sent again unchanged", () => {
  const { pointer, paint, written } = setup(() => 0, "sixel")
  pointer.showImage(SIXEL_PICTURE)
  assert.equal(sixels(written).length, 0, "nothing before the first frame")
  paint()
  assert.equal(sixels(written).length, 1)
  // 4 columns x 2 rows of the assumed 8x16-pixel cell: 32 x 32 pixels, at row 1, column 2.
  assert.match(sixels(written)[0]!, /^\x1b\[1;2H\x1bP0;1;0q"1;1;32;32#/)
  paint(["hello world", "SECOND ROW", "third"])
  pointer.showImage({ ...SIXEL_PICTURE })
  assert.equal(sixels(written).length, 1)
})

test("Sixel: a picture cut at the top is drawn from the first visible row with only the visible pixel rows", () => {
  const { pointer, paint, written } = setup(() => 0, "sixel")
  paint()
  pointer.showImage({ ...SIXEL_PICTURE, row: -1, skipTop: 1 })
  // 2 rows of 16 pixels, the first cut off: 16 pixel rows, at screen row 0.
  assert.match(sixels(written)[0]!, /^\x1b\[1;2H\x1bP0;1;0q"1;1;32;16#/)
})

test("Sixel: the size of a cell the terminal reported is used", () => {
  const input = new Readable({ read() {} })
  const written: unknown[] = []
  const output = { rows: 3, columns: 20, isTTY: true, write: (chunk: unknown) => (written.push(chunk), true) }
  const pointer = new Pointer(input as unknown as NodeJS.ReadStream, output as unknown as NodeJS.WriteStream, { pictures: "sixel", cell: { width: 10, height: 20 } })
  pointer.stdout.write(frameChunk())
  pointer.showImage(SIXEL_PICTURE)
  assert.match(sixels(written)[0]!, /"1;1;40;40#/)
})

test("Sixel: a picture that goes away is erased by writing the rows under it again", () => {
  const { pointer, paint, written } = setup(() => 0, "sixel")
  paint()
  pointer.showImage(SIXEL_PICTURE)
  const before = written.length
  pointer.showImage(null)
  const erase = written.slice(before).map(String).join("")
  assert.match(erase, /\x1b\[1;1Hhello world/)
  assert.match(erase, /\x1b\[2;1Hsecond row/)
  assert.doesNotMatch(erase, /\x1b_G/, "no kitty deletion is sent")
  pointer.showImage(SIXEL_PICTURE)
  assert.equal(sixels(written).length, 2, "and it can be shown again")
})

test("Sixel: moving the picture erases the old cells before drawing at the new ones", () => {
  const { pointer, paint, written } = setup(() => 0, "sixel")
  paint()
  pointer.showImage(SIXEL_PICTURE)
  const before = written.length
  pointer.showImage({ ...SIXEL_PICTURE, row: 1 })
  const chunks = written.slice(before).map(String)
  assert.match(chunks[0]!, /\x1b\[1;1Hhello world/)
  assert.match(chunks.at(-1)!, /^\x1b\[2;2H\x1bP0;1;0q/)
})

test("a screen clear forgets a Sixel picture too, so the next frame draws it again", () => {
  const { pointer, paint, written } = setup(() => 0, "sixel")
  paint()
  pointer.showImage(SIXEL_PICTURE)
  pointer.stdout.write("\x1b[2J\x1b[H")
  paint()
  assert.equal(sixels(written).length, 2)
})

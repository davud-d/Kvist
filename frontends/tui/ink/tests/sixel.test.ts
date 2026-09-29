import assert from "node:assert/strict"
import { test } from "node:test"

import { decodePalettePng, type Indexed } from "../png.js"
import { encodeSixel, scaleNearest } from "../sixel.js"
import { MASCOT_PNGS } from "../ui/skins/claude-code/mascot-images.js"

const image = (width: number, height: number, pixels: number[], palette: [number, number, number][], alpha: number[] = palette.map(() => 255)): Indexed => ({
  width,
  height,
  palette,
  alpha,
  pixels: Uint8Array.from(pixels),
})

test("the mascot's PNGs are read: 256 pixels square, a palette, a transparent background", () => {
  assert.equal(MASCOT_PNGS.length, 6)
  for (const png of MASCOT_PNGS) {
    const decoded = decodePalettePng(png)
    assert.equal(decoded.width, 256)
    assert.equal(decoded.height, 256)
    assert.ok(decoded.palette.length > 8 && decoded.palette.length <= 256)
    assert.equal(decoded.pixels.length, 256 * 256)
    const clear = decoded.pixels.filter((index) => (decoded.alpha[index] ?? 255) === 0).length
    assert.ok(clear > 256 * 256 * 0.3, "most of the picture around the squirrel is transparent")
  }
})

test("something that is not a PNG is refused", () => {
  assert.throws(() => decodePalettePng(Buffer.from("not a png at all").toString("base64")), /not a PNG/)
})

test("scaling takes existing colours only and keeps the corners", () => {
  const small = image(2, 2, [0, 1, 2, 3], [[0, 0, 0], [1, 1, 1], [2, 2, 2], [3, 3, 3]])
  const big = scaleNearest(small, 4, 4)
  assert.deepEqual([...big], [0, 0, 1, 1, 0, 0, 1, 1, 2, 2, 3, 3, 2, 2, 3, 3])
})

test("a solid picture is one colour run per band, and the size and palette are declared", () => {
  const red = image(2, 6, Array(12).fill(0), [[255, 0, 0]])
  assert.equal(encodeSixel(red, 2, 6), '\x1bP0;1;0q"1;1;2;6#0;2;100;0;0#0~~\x1b\\')
})

test("long runs are counted, columns of different colours share a band with $, bands are joined by -", () => {
  // Row 0-5: left half red, right half blue; then a second band of 7 more rows all red.
  const width = 8
  const rows = 12
  const pixels: number[] = []
  for (let y = 0; y < rows; y++) for (let x = 0; x < width; x++) pixels.push(y < 6 ? (x < 4 ? 0 : 1) : 0)
  const out = encodeSixel(image(width, rows, pixels, [[255, 0, 0], [0, 0, 255]]), width, rows)
  assert.equal(out, '\x1bP0;1;0q"1;1;8;12#0;2;100;0;0#1;2;0;0;100#0!4~!4?$#1!4?!4~-#0!8~\x1b\\')
})

test("transparent pixels get no colour at all, so the background shows through", () => {
  const out = encodeSixel(image(2, 6, [0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1], [[10, 20, 30], [0, 0, 0]], [255, 0]), 2, 6)
  assert.match(out, /#0[^#]*\x1b\\$/)
  assert.doesNotMatch(out, /#1/)
})

test("only the asked pixel rows are written, and the size says so", () => {
  // Three bands of 6 rows: red, blue, red. Keep the middle one only.
  const rows = [0, 1, 0]
  const pixels = rows.flatMap((color) => Array(6 * 2).fill(color))
  const picture = image(2, 18, pixels, [[255, 0, 0], [0, 0, 255]])
  const out = encodeSixel(picture, 2, 18, 6, 12)
  assert.match(out, /"1;1;2;6#/)
  assert.doesNotMatch(out, /#0;/, "the red of the cut-off bands is not even defined")
  assert.match(out, /#1;2;0;0;100#1~~\x1b\\$/)
})

test("a real frame becomes a Sixel string that ends the way one must", () => {
  const out = encodeSixel(decodePalettePng(MASCOT_PNGS[5]!), 128, 128)
  assert.ok(out.startsWith('\x1bP0;1;0q"1;1;128;128#'))
  assert.ok(out.endsWith("\x1b\\"))
  assert.ok(out.length > 2000 && out.length < 120_000, `${out.length} characters`)
  assert.ok(!/[^\x1b\x20-\x7e]/.test(out), "printable ASCII apart from the escapes")
})

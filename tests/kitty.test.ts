import assert from "node:assert/strict"
import { test } from "node:test"

import { deleteImage, kittyGraphics, placeImage } from "../src/kitty.js"

test("kitty, Ghostty and Konsole are told apart by their environment, and a multiplexer hides them", () => {
  assert.equal(kittyGraphics({ TERM: "xterm-kitty" }), true)
  assert.equal(kittyGraphics({ TERM: "xterm-256color", KITTY_WINDOW_ID: "3" }), true)
  assert.equal(kittyGraphics({ TERM: "xterm-256color", TERM_PROGRAM: "ghostty" }), true)
  assert.equal(kittyGraphics({ TERM: "xterm-256color", KONSOLE_VERSION: "240500" }), true)
  assert.equal(kittyGraphics({ TERM: "xterm-256color" }), false)
  assert.equal(kittyGraphics({ TERM: "screen-256color", KITTY_WINDOW_ID: "3" }), false)
  assert.equal(kittyGraphics({ TERM: "xterm-kitty", TMUX: "/tmp/tmux-1000/default,1,0" }), false)
})

test("an image goes to its cell in chunks of at most 4096 bytes, only the first carrying the keys", () => {
  const png = "A".repeat(9000)
  const out = placeImage(png, { id: 7, row: 1, col: 4, columns: 16, rows: 8 })
  assert.ok(out.startsWith("\x1b[2;5H"), "the cursor moves to row 2, column 5 first")
  const chunks = [...out.matchAll(/\x1b_G([^;]*);([^\x1b]*)\x1b\\/g)]
  assert.equal(chunks.length, 3)
  assert.equal(chunks[0]?.[1], "a=T,f=100,i=7,p=1,c=16,r=8,C=1,q=2,m=1")
  assert.equal(chunks[1]?.[1], "m=1")
  assert.equal(chunks[2]?.[1], "m=0")
  assert.ok(chunks.every((chunk) => (chunk[2] ?? "").length <= 4096))
  assert.equal(chunks.map((chunk) => chunk[2]).join(""), png)
})

test("a small image is one chunk that says it is the last", () => {
  const out = placeImage("QUJD", { id: 1, row: 0, col: 0, columns: 2, rows: 1 })
  assert.match(out, /^\x1b\[1;1H\x1b_Ga=T,f=100,i=1,p=1,c=2,r=1,C=1,q=2,m=0;QUJD\x1b\\$/)
})

test("a picture that is partly out of view is placed with the band of it that shows", () => {
  const out = placeImage("QUJD", { id: 1, row: 0, col: 0, columns: 16, rows: 6, source: { y: 64, height: 192 } })
  assert.match(out, /a=T,f=100,i=1,p=1,c=16,r=6,y=64,h=192,C=1,q=2,m=0;QUJD/)
})

test("deleting names the image and frees its data", () => {
  assert.equal(deleteImage(7), "\x1b_Ga=d,d=I,i=7,q=2\x1b\\")
})

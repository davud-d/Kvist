import assert from "node:assert/strict"
import { chmodSync, mkdirSync, mkdtempSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { delimiter, join } from "node:path"
import { test } from "node:test"

import { placeRgb, placeRgbAsync } from "../kitty.js"
import { initialState, type SetupState } from "../setup/flow.js"
import { renderScene } from "../setup/scene.js"
import { stringsFor } from "../setup/strings.js"
import { FALLBACK_INTERVAL_MS, FramePacer, FrameReader, MIN_VIDEO_ROWS, SLOW_SEND_MS, VIDEO_FPS, findClip, findFfmpeg, frameSize, videoFit } from "../setup/video.js"

const strings = stringsFor("en")
const plain = (text: string) => text.replace(/\x1b\[[0-9;]*m/g, "")
// A typical terminal cell: 10 x 20 pixels.
const cell = { width: 10, height: 20 }

test("the video takes the largest 16:9 rectangle the room allows, by the cell's own proportions", () => {
  // 64 columns of 10 px are 640 px wide, so 360 px tall: 18 rows of 20 px.
  assert.deepEqual(videoFit(64, 30, cell), { columns: 64, rows: 18 })
  // Short on rows: the width follows the height.
  assert.deepEqual(videoFit(64, 10, cell), { columns: 36, rows: 10 })
  // Short on columns: the height follows the width.
  assert.deepEqual(videoFit(40, 30, cell), { columns: 40, rows: 11 })
  // Tall cells (HiDPI 20 x 40) keep the same picture.
  assert.deepEqual(videoFit(64, 30, { width: 20, height: 40 }), { columns: 64, rows: 18 })
})

test("a rectangle too small to read the terminal text in is not offered", () => {
  assert.equal(videoFit(64, MIN_VIDEO_ROWS - 1, cell), null)
  assert.equal(videoFit(12, 30, cell), null)
})

test("frames are decoded at the size they are shown, at most 960 pixels wide", () => {
  assert.deepEqual(frameSize({ columns: 64, rows: 18 }, cell), { width: 640, height: 360 })
  assert.deepEqual(frameSize({ columns: 64, rows: 18 }, { width: 20, height: 40 }), { width: 960, height: 540 })
})

test("the frame reader hands over only the newest whole frame and keeps the partial rest", () => {
  const reader = new FrameReader(4)
  assert.equal(reader.push(Buffer.from([1, 1])), null)
  assert.deepEqual([...(reader.push(Buffer.from([1, 1, 2, 2, 2, 2, 3, 3])) ?? [])], [2, 2, 2, 2])
  assert.deepEqual([...(reader.push(Buffer.from([3, 3])) ?? [])], [3, 3, 3, 3])
})

test("ffmpeg is KVIST_FFMPEG when it runs, else the first one on PATH, else the installed one", () => {
  const directory = mkdtempSync(join(tmpdir(), "kvist-ffmpeg-"))
  const binary = join(directory, "ffmpeg")
  writeFileSync(binary, "#!/bin/sh\n")
  chmodSync(binary, 0o755)
  const emptyHome = mkdtempSync(join(tmpdir(), "kvist-home-"))
  assert.equal(findFfmpeg({ PATH: ["/nonexistent", directory].join(delimiter) }, "linux", emptyHome), binary)
  assert.equal(findFfmpeg({ KVIST_FFMPEG: binary, PATH: "" }, "linux", emptyHome), binary)
  assert.equal(findFfmpeg({ KVIST_FFMPEG: join(directory, "missing"), PATH: directory }, "linux", emptyHome), null)
  assert.equal(findFfmpeg({ PATH: "/nonexistent" }, "linux", emptyHome), null)

  // The release installer unpacks one beside kvist-core; PATH still wins when it has one.
  const installedDirectory = join(emptyHome, ".local", "share", "kvist", "ffmpeg")
  mkdirSync(installedDirectory, { recursive: true })
  const installed = join(installedDirectory, "ffmpeg")
  writeFileSync(installed, "#!/bin/sh\n")
  chmodSync(installed, 0o755)
  assert.equal(findFfmpeg({ PATH: "/nonexistent" }, "linux", emptyHome), installed)
  assert.equal(findFfmpeg({ PATH: directory }, "linux", emptyHome), binary)
})

test("the demo clip is found beside the sources", () => {
  assert.match(findClip() ?? "", /claude-style\.mp4$/)
})

test("an image can be placed at a cell, and sits in the window's corner without one", () => {
  const rgb = new Uint8Array(12)
  assert.ok(placeRgb(rgb, 2, 2, { id: 8, columns: 4, rows: 2, z: -1, row: 9, col: 14 }).startsWith("\x1b[10;15H"))
  assert.ok(placeRgb(rgb, 2, 2, { id: 8, columns: 4, rows: 2, z: -1 }).startsWith("\x1b[1;1H"))
})

test("compressing on the thread pool gives the same bytes as compressing inline", async () => {
  const rgb = Uint8Array.from({ length: 4 * 4 * 3 }, (_, index) => index * 7)
  const at = { id: 8, columns: 4, rows: 2, z: -1, row: 3, col: 5 }
  assert.equal(await placeRgbAsync(rgb, 4, 4, at), placeRgb(rgb, 4, 4, at))
})

test("every frame goes out at 60 fps while sending is cheap", () => {
  const pacer = new FramePacer()
  for (let frame = 0; frame < 100; frame++) {
    assert.equal(pacer.take(frame * 16.7), true)
    pacer.record(SLOW_SEND_MS - 4)
  }
  assert.equal(pacer.fps, VIDEO_FPS)
})

test("when sending costs nearly a whole frame interval, frames go out 30 times a second", () => {
  const pacer = new FramePacer()
  for (let frame = 0; frame < 40; frame++) {
    pacer.take(frame * 16.7)
    pacer.record(SLOW_SEND_MS + 4)
  }
  assert.equal(pacer.fps, VIDEO_FPS / 2)
  const start = 1000
  assert.equal(pacer.take(start + FALLBACK_INTERVAL_MS), true)
  assert.equal(pacer.take(start + FALLBACK_INTERVAL_MS + 16.7), false)
  assert.equal(pacer.take(start + FALLBACK_INTERVAL_MS + 33.4), true)
})

test("the first frames, which pay for cold code, do not count toward the fallback", () => {
  const pacer = new FramePacer()
  for (let frame = 0; frame < 20; frame++) pacer.record(80)
  for (let frame = 0; frame < 30; frame++) pacer.record(5)
  assert.equal(pacer.fps, VIDEO_FPS)
})

const done: SetupState = { ...initialState(), catalog: "ready", step: "interface", connected: "openai", interfaceIndex: 0 }
const scene = (state: SetupState, rows: number, video: typeof cell | null) =>
  renderScene({ state, strings, cols: 100, rows, level: "truecolor", brightness: 0.55, panel: 1, tick: 0, workspace: "", hover: null, pressed: null, icons: "plain", ghost: null, picture: "image", video })

test("the interface step reserves a video slot inside the panel and leaves the text preview out", () => {
  const shown = scene(done, 50, cell)
  assert.ok(shown.video && shown.panel)
  assert.deepEqual([shown.video.width, shown.video.height], [64, 18])
  assert.ok(shown.video.col > shown.panel.col && shown.video.col + shown.video.width < shown.panel.col + shown.panel.width)
  assert.ok(shown.video.row > shown.panel.row && shown.video.row + shown.video.height < shown.panel.row + shown.panel.height)
  assert.doesNotMatch(shown.rows.map(plain).join("\n"), /Ask Kvist, or \/ for commands/)
})

test("the panel keeps its height when the highlight moves, and only the first interface has the video", () => {
  const first = scene(done, 50, cell)
  const second = scene({ ...done, interfaceIndex: 1 }, 50, cell)
  assert.equal(second.video ?? null, null)
  assert.equal(second.panel?.height, first.panel?.height)
  assert.match(second.rows.map(plain).join("\n"), /Ask Kvist, or \/ for commands/)
})

test("without a decoder, or in a short window, the text preview stays", () => {
  assert.equal(scene(done, 50, null).video ?? null, null)
  assert.match(scene(done, 50, null).rows.map(plain).join("\n"), /Ask Kvist, or \/ for commands/)
  assert.equal(scene(done, 24, cell).video ?? null, null)
})

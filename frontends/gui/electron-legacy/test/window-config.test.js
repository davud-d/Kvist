"use strict"

const assert = require("node:assert/strict")
const { EventEmitter } = require("node:events")
const test = require("node:test")

const { buildWindowOptions, primaryWorkArea, applyMaximizeOnShow } = require("../src/window-config")

function fakeWindow() {
  const emitter = new EventEmitter()
  const calls = []
  emitter.maximize = () => calls.push("maximize")
  emitter.show = () => calls.push("show")
  emitter.setFullScreen = () => calls.push("fullscreen")
  emitter.isDestroyed = () => false
  return { emitter, calls }
}

const fakeScreen = (w, h) => ({ getPrimaryDisplay: () => ({ workAreaSize: { width: w, height: h } }) })

test("options keep a normal resizable frame, no fullscreen/kiosk, no saved bounds", () => {
  const opts = buildWindowOptions(fakeScreen(2560, 1400))
  assert.equal(opts.resizable, true)
  assert.ok(!("fullscreen" in opts))
  assert.ok(!("kiosk" in opts))
  assert.ok(!("x" in opts) && !("y" in opts))
})

test("window is sized to the primary work area", () => {
  const opts = buildWindowOptions(fakeScreen(2560, 1400))
  assert.equal(opts.width, 2560)
  assert.equal(opts.height, 1400)
})

test("falls back to a default size when screen is unavailable", () => {
  const size = primaryWorkArea({ getPrimaryDisplay: () => { throw new Error("not ready") } })
  assert.deepEqual(size, { width: 1180, height: 780 })
})

test("shows then maximizes on ready-to-show", async () => {
  const { emitter, calls } = fakeWindow()
  applyMaximizeOnShow(emitter, { capture: false })
  emitter.emit("ready-to-show")
  await new Promise((resolve) => setImmediate(resolve))
  assert.deepEqual(calls, ["show", "maximize"])
})

test("capture mode never shows or maximizes", async () => {
  const { emitter, calls } = fakeWindow()
  applyMaximizeOnShow(emitter, { capture: true })
  emitter.emit("ready-to-show")
  await new Promise((resolve) => setImmediate(resolve))
  assert.deepEqual(calls, [])
})

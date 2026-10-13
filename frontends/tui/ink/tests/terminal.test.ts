import assert from "node:assert/strict"
import { test } from "node:test"
import { renderWithRawInput } from "../terminal.js"

test("raw mode is enabled before render can query the terminal", () => {
  const changes: boolean[] = []
  const stdin = { isRaw: false, setRawMode(raw: boolean) { this.isRaw = raw; changes.push(raw); return this } }
  const app = {}
  assert.equal(renderWithRawInput(stdin, () => {
    assert.equal(stdin.isRaw, true)
    return app
  }), app)
  assert.deepEqual(changes, [true])
})

test("a failed render restores the previous raw mode", () => {
  for (const initial of [false, true]) {
    const stdin = { isRaw: initial, setRawMode(raw: boolean) { this.isRaw = raw; return this } }
    const error = new Error("render failed")
    assert.throws(() => renderWithRawInput(stdin, () => { throw error }), (caught) => caught === error)
    assert.equal(stdin.isRaw, initial)
  }
})

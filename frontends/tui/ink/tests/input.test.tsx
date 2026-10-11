import assert from "node:assert/strict"
import { setTimeout as sleep } from "node:timers/promises"
import { test } from "node:test"

import { render } from "ink-testing-library"
import { createElement } from "react"

import { ViewContext } from "../ui/context.js"
import { InputBox } from "../ui/input.js"

const plain = (text: string | undefined) => (text ?? "").replace(/\x1b\[[0-9;]*m/g, "")
const view = { workspace: "/w", info: { provider: "p", model: "m", role: "coder", session: "s", kvistVersion: "1" }, effort: "high", pointer: null, expanded: false }
const UP = "\x1b[A"
const DOWN = "\x1b[B"

async function typeKeys(history: string[], ...keys: string[]) {
  const submitted: string[] = []
  const app = render(createElement(ViewContext.Provider, { value: view }, createElement(InputBox, { active: true, history, onSubmit: (text: string) => submitted.push(text) })))
  await sleep(30)
  for (const key of keys) {
    app.stdin.write(key)
    await sleep(30)
  }
  const shown = plain(app.lastFrame())
  app.unmount()
  return { shown, submitted }
}

test("Up in typed text goes to its start first, then recalls history, and Down brings the draft back", async () => {
  const history = ["older", "newer"]
  const typed = await typeKeys(history, "draft", UP, "X")
  assert.match(typed.shown, /Xdraft/)

  const recalled = await typeKeys(history, "draft", UP, UP)
  assert.match(recalled.shown, /newer/)
  assert.doesNotMatch(recalled.shown, /draft/)

  const back = await typeKeys(history, "draft", UP, UP, DOWN)
  assert.match(back.shown, /draft/)
})

test("a recalled slash command does not trap the arrow keys in the command menu", async () => {
  const { shown } = await typeKeys(["plain entry", "/help"], UP, UP)
  assert.match(shown, /plain entry/)
})

test("Alt+Enter and Shift+Enter add a line instead of submitting", async () => {
  const { shown, submitted } = await typeKeys([], "one", "\x1b\r", "two")
  assert.deepEqual(submitted, [])
  assert.match(shown, /one/)
  assert.match(shown, /two/)
})

test("a file image becomes a label and travels as pixels only when submitted", async () => {
  const { mkdtempSync, writeFileSync, rmSync } = await import("node:fs")
  const { tmpdir } = await import("node:os")
  const { join } = await import("node:path")
  const folder = mkdtempSync(join(tmpdir(), "kvist-images-"))
  try {
    const path = join(folder, "screen.png")
    writeFileSync(path, Buffer.from("89504e470d0a1a0a", "hex"))
    const labelled = await typeKeys([], `/image ${path}`, "\r")
    assert.match(labelled.shown, /\[Image #1\]/)
    assert.deepEqual(labelled.submitted, [])
    const sent = await typeKeys([], `/image ${path}`, "\r", " explain", "\r")
    const envelope = JSON.parse(sent.submitted[0]!)
    assert.equal(envelope.type, "kvist-image-goal")
    assert.equal(envelope.images.length, 1)
    assert.match(envelope.images[0].data_url, /^data:image\/png;base64,/)
    const removed = await typeKeys([], `/image ${path}`, "\r", "\x7f", "hello", "\r")
    assert.deepEqual(removed.submitted, ["hello"])
  } finally { rmSync(folder, { recursive: true, force: true }) }
})

test("kitty keyboard capability replies do not become typed text", async () => {
  for (const reply of ["\x1b[?0u", "\x1b[?31u"]) {
    const { shown, submitted } = await typeKeys([], reply, "hello")
    assert.doesNotMatch(shown, /\[\?\d+u/)
    assert.deepEqual(submitted, [])
    const sent = await typeKeys([], reply + "hello", "\r")
    assert.deepEqual(sent.submitted, ["hello"])
  }
})

test("bracketed paste keeps text that looks like a keyboard capability reply", async () => {
  const { submitted } = await typeKeys([], "\x1b[200~[?0u\x1b[201~", "\r")
  assert.deepEqual(submitted, ["[?0u"])
})

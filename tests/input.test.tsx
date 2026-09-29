import assert from "node:assert/strict"
import { setTimeout as sleep } from "node:timers/promises"
import { test } from "node:test"

import { render } from "ink-testing-library"
import { createElement } from "react"

import { ViewContext } from "../src/ui/context.js"
import { InputBox } from "../src/ui/input.js"

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

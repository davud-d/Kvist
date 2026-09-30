import assert from "node:assert/strict"
import { setTimeout as sleep } from "node:timers/promises"
import { test } from "node:test"

import { render } from "ink-testing-library"
import { createElement } from "react"

import { ViewContext } from "../ui/context.js"
import { ClosedBar, InputBox } from "../ui/input.js"
import { applySkin } from "../ui/theme.js"

const plain = (text: string | undefined) => (text ?? "").replace(/\x1b\[[0-9;]*m/g, "")
const view = { workspace: "/w", info: { provider: "p", model: "m", role: "coder", session: "s", kvistVersion: "1" }, effort: "high", pointer: null, expanded: false }
const NOTICE = "conversation over · Enter — new session"

async function pressKeys(active: boolean, ...keys: string[]) {
  let opened = 0
  const app = render(createElement(ViewContext.Provider, { value: view }, createElement(ClosedBar, { notice: NOTICE, active, onNewSession: () => { opened += 1 } })))
  await sleep(30)
  const before = plain(app.lastFrame())
  for (const key of keys) {
    app.stdin.write(key)
    await sleep(30)
  }
  const after = plain(app.lastFrame())
  app.unmount()
  return { before, after, opened }
}

test("the bar shows the core's notice and nothing of its own", async () => {
  const { before } = await pressKeys(true)
  assert.match(before, /conversation over · Enter — new session/)
  assert.doesNotMatch(before, /❯/)
})

test("Enter opens a new session", async () => {
  assert.equal((await pressKeys(true, "\r")).opened, 1)
})

test("typed characters, Escape and a paste change nothing", async () => {
  const { before, after, opened } = await pressKeys(true, "hello", "\x1b", "\x1b[200~pasted text\x1b[201~", " ")
  assert.equal(opened, 0)
  assert.equal(after, before)
})

test("the bar takes no key while a command or a turn is running", async () => {
  assert.equal((await pressKeys(false, "\r")).opened, 0)
})

for (const skin of ["ink", "claude"] as const) {
  test(`the bar is exactly as tall as the input box (${skin} skin)`, async () => {
    applySkin(skin)
    const bar = render(createElement(ViewContext.Provider, { value: view }, createElement(ClosedBar, { notice: NOTICE, active: true, onNewSession: () => {} })))
    const box = render(createElement(ViewContext.Provider, { value: view }, createElement(InputBox, { active: true, history: [], onSubmit: () => {} })))
    await sleep(30)
    const rows = (frame: string | undefined) => plain(frame).split("\n").length
    assert.equal(rows(bar.lastFrame()), rows(box.lastFrame()))
    bar.unmount()
    box.unmount()
    applySkin("ink")
  })
}

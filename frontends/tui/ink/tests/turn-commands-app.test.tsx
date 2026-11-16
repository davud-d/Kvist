import assert from "node:assert/strict"
import path from "node:path"
import { setTimeout as sleep } from "node:timers/promises"
import { test } from "node:test"
import { fileURLToPath } from "node:url"

import { render } from "ink-testing-library"
import { createElement } from "react"

import { App } from "../ui/app.js"

// The whole interface against the scripted core: what a person types, and what is on screen afterwards.

const fake = path.join(path.dirname(fileURLToPath(import.meta.url)), "fixtures", "fake-core.mjs")
const plain = (text: string | undefined) => (text ?? "").replace(/\x1b\[[0-9;?]*[A-Za-z]/g, "")

// Runs `scenario` on a started interface and always unmounts it: a failed wait must not leave the core running.
async function withApp(scenario: (app: ReturnType<typeof render>, screen: Screen) => Promise<void>) {
  const app = render(createElement(App, { command: { command: process.execPath, prefixArgs: [fake] }, workspace: process.cwd(), role: "auto", approvalMode: "ask", skin: "ink", pointer: null }))
  const frame = () => plain(app.lastFrame())
  const waitFor = async (text: string) => {
    for (let waited = 0; waited < 8000; waited += 40) {
      if (frame().includes(text)) return
      await sleep(40)
    }
    assert.fail(`not on screen: ${text}\n${frame()}`)
  }
  const type = async (line: string) => {
    for (const char of line) {
      app.stdin.write(char)
      await sleep(5)
    }
    await sleep(60)
    app.stdin.write("\r")
  }
  try {
    await waitFor("fake-fast")
    await scenario(app, { frame, waitFor, type })
  } finally {
    app.unmount()
  }
}

interface Screen {
  frame: () => string
  waitFor: (text: string) => Promise<void>
  type: (line: string) => Promise<void>
}

test("/council <question> runs as a turn: its answer is on screen and the input is free again", () =>
  withApp(async (_app, { frame, waitFor, type }) => {
    await type("/council is it simple")
    await waitFor("The chosen decision: use security")
    // Free again: a second request goes through.
    await type("hello again")
    await waitFor("done: hello again")
    assert.doesNotMatch(frame(), /Wait for the current run/)
  }))

test("Ctrl+C stops a council that is running, and the input is free afterwards", () =>
  withApp(async (app, { frame, waitFor, type }) => {
    await type("/council slow")
    await sleep(300)
    app.stdin.write("\x03")
    await waitFor("council stopped")
    await type("hello again")
    await waitFor("done: hello again")
    assert.doesNotMatch(frame(), /Wait for the current run/)
  }))

test("a bare /council is not sent: Enter completes the name and waits for the question", () =>
  withApp(async (app, { frame, waitFor, type }) => {
    await type("/council")
    await sleep(500)
    // Nothing was sent: no refusal from the core; the line waits in the input box for its question.
    assert.doesNotMatch(frame(), /Usage: \/council|Unknown command/)
    assert.match(frame(), /❯ +\/council/)
    app.stdin.write("why")
    await sleep(100)
    app.stdin.write("\r")
    await waitFor("The chosen decision: use security")
  }))

test("/best-of asks for the decision as an ordinary choice, whose verdict is on screen", () =>
  withApp(async (app, { waitFor, type }) => {
    await type("/best-of 2 fix the bug")
    await waitFor("Accept e1")
    await waitFor("winner: e1 (tests passed)")
    app.stdin.write("\r")
    await waitFor("The result was accepted")
  }))

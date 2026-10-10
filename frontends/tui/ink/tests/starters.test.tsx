import assert from "node:assert/strict"
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { setTimeout as sleep } from "node:timers/promises"
import { test } from "node:test"

import { render } from "ink-testing-library"
import { createElement } from "react"

import { kindOf, starterSet, startersFor } from "../starters.js"
import { ViewContext } from "../ui/context.js"
import { InputBox } from "../ui/input.js"

const plain = (text: string | undefined) => (text ?? "").replace(/\x1b\[[0-9;]*m/g, "")
const view = { workspace: "/w", info: { provider: "p", model: "m", role: "coder", session: "s", kvistVersion: "1" }, effort: "high", pointer: null, expanded: false }
const UP = "\x1b[A"
const DOWN = "\x1b[B"

test("a folder is empty, a project or just files, and Kvist's own entries do not count", () => {
  assert.equal(kindOf([]), "empty")
  assert.equal(kindOf([".kvist", "KVIST.md", "KVIST-CHAT.md"]), "empty")
  assert.equal(kindOf([".git", "README.md"]), "project")
  assert.equal(kindOf(["package.json"]), "project")
  assert.equal(kindOf(["App.sln"]), "project")
  assert.equal(kindOf([".kvist", "photo.jpg", "notes.txt"]), "files")
})

test("the requests fit the folder, and a git project is offered its history", () => {
  assert.equal(startersFor("empty", "en", false).prompts.length, 3)
  assert.match(startersFor("empty", "en", false).prompts[0]!, /Create a simple to-do/)
  assert.match(startersFor("project", "en", true).prompts[1]!, /changed/)
  assert.match(startersFor("project", "en", false).prompts[1]!, /bugs/)
  assert.match(startersFor("project", "ru", true).prompts[0]!, /Объясни/)
  assert.equal(startersFor("files", "ru", false).prompts.length, 3)
})

test("a folder is read from disk; one that cannot be read offers nothing", () => {
  const folder = mkdtempSync(join(tmpdir(), "kvist-starters-"))
  try {
    assert.equal(starterSet(folder, { LANG: "en_US.UTF-8" })?.prompts[0]?.startsWith("Create a simple"), true)
    mkdirSync(join(folder, ".git"))
    writeFileSync(join(folder, "main.py"), "")
    assert.match(starterSet(folder, { LANG: "en_US.UTF-8" })!.prompts[1]!, /changed/)
    assert.equal(starterSet(join(folder, "missing")), null)
  } finally {
    rmSync(folder, { recursive: true, force: true })
  }
})

async function drive(starters: ReturnType<typeof startersFor> | null, history: string[], ...keys: string[]) {
  const submitted: string[] = []
  const app = render(createElement(ViewContext.Provider, { value: view }, createElement(InputBox, { active: true, history, starters, onSubmit: (text: string) => submitted.push(text) })))
  await sleep(30)
  const first = plain(app.lastFrame())
  for (const key of keys) {
    app.stdin.write(key)
    await sleep(30)
  }
  const shown = plain(app.lastFrame())
  app.unmount()
  return { first, shown, submitted }
}

test("an empty box offers the requests; the arrows choose, Tab fills, Enter then sends", async () => {
  const starters = startersFor("project", "en", true)
  const open = await drive(starters, [])
  assert.match(open.first, /Try one/)
  for (const prompt of starters.prompts) assert.match(open.first, new RegExp(prompt.slice(0, 20)))

  const filled = await drive(starters, [], DOWN, "\t")
  assert.match(filled.shown, /Summarize what has changed/)
  // Once the box has text the list is gone.
  assert.doesNotMatch(filled.shown, /Try one/)
  assert.deepEqual(filled.submitted, [])

  // Enter on the empty box fills rather than sending nothing; the second Enter sends the line.
  const sent = await drive(starters, [], "\r", "\r")
  assert.deepEqual(sent.submitted, [starters.prompts[0]])

  // Up wraps to the last request.
  const wrapped = await drive(starters, [], UP, "\t")
  assert.match(wrapped.shown, /Check how the tests are set up/)
})

test("typed text, a history and a missing set all leave the box as it was", async () => {
  const starters = startersFor("empty", "en", false)
  const typed = await drive(starters, [], "x")
  assert.doesNotMatch(typed.shown, /Try one/)
  // Up in an empty box with starters shown chooses a request; without starters it recalls history.
  const recalled = await drive(null, ["older entry"], UP)
  assert.match(recalled.shown, /older entry/)
  const none = await drive(null, [])
  assert.doesNotMatch(none.first, /Try one/)
  const hidden = await drive(starters, [], "/")
  assert.doesNotMatch(hidden.shown, /Try one/)
})

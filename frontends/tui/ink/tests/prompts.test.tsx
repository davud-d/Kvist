import assert from "node:assert/strict"
import { test } from "node:test"

import { render } from "ink-testing-library"
import { createElement } from "react"

import type { InputOption, InputRequest } from "../protocol.js"
import { Prompt } from "../ui/prompts.js"

const plain = (text: string | undefined) => (text ?? "").replace(/\x1b\[[0-9;]*m/g, "")
const tick = () => new Promise((resolve) => setTimeout(resolve, 30))

const roles: InputOption[] = [
  { value: "auto", label: "Auto", description: "Pick per request" },
  { value: "coder", label: "Coder", description: "Edits code" },
  { value: "chat", label: "Chat", description: "No tools" },
]

const many: InputOption[] = Array.from({ length: 45 }, (_, i) => {
  const n = String(i + 1).padStart(2, "0")
  return { value: `m${n}`, label: `fake-model-${n}`, description: i % 5 === 0 ? `flagship ${n}` : `standard ${n}` }
})

function request(fields: Partial<InputRequest>): InputRequest {
  return { request_id: "r", kind: "choice", title: "Title", body: "", options: null, current_value: "", help_text: "", ...fields }
}

function mount(fields: Partial<InputRequest>) {
  const answers: unknown[] = []
  const app = render(createElement(Prompt, { request: request(fields), onAnswer: (value) => answers.push(value) }))
  return { app, answers, frame: () => plain(app.lastFrame()) }
}

test("a choice dialog has a rounded frame, title, esc, search, counter and hints", () => {
  const { frame } = mount({ title: "Select Agent Role", options: roles, current_value: "coder" })
  const out = frame()
  assert.match(out, /^╭/)
  assert.match(out, /╰.*╯\s*$/)
  assert.match(out, /Select Agent Role\s+esc/)
  assert.match(out, /type to filter/)
  assert.match(out, /2\/3/)
  assert.match(out, /↑↓ move · enter select · esc cancel/)
  assert.match(out, /Coder\s+Edits code/)
})

test("the current value is marked and starts selected", async () => {
  const { app, answers, frame } = mount({ options: roles, current_value: "coder" })
  assert.match(frame(), /● Coder/)
  assert.doesNotMatch(frame(), /● Auto/)
  app.stdin.write("\r")
  await tick()
  assert.deepEqual(answers, ["coder"])
})

test("typing filters by label and description, Enter picks the match", async () => {
  const { app, answers, frame } = mount({ options: roles })
  app.stdin.write("no tools")
  await tick()
  const out = frame()
  assert.match(out, /Chat/)
  assert.doesNotMatch(out, /Coder/)
  assert.match(out, /1\/1/)
  app.stdin.write("\r")
  await tick()
  assert.deepEqual(answers, ["chat"])
})

test("no match shows a line and Enter does nothing", async () => {
  const { app, answers, frame } = mount({ options: roles })
  app.stdin.write("zzzz")
  await tick()
  assert.match(frame(), /No matches/)
  app.stdin.write("\r")
  await tick()
  assert.deepEqual(answers, [])
})

test("Esc cancels with null", async () => {
  const { app, answers } = mount({ options: roles })
  app.stdin.write("\x1b")
  await tick()
  assert.deepEqual(answers, [null])
})

test("a long list scrolls in a window and the selection is always drawn", async () => {
  const { app, frame } = mount({ options: many, current_value: "m01" })
  const rows = () => frame().split("\n").filter((line) => /fake-model-\d\d/.test(line))
  assert.equal(rows().length, 12)
  assert.match(frame(), /1\/45/)
  for (let i = 0; i < 44; i++) app.stdin.write("\x1b[B")
  await tick()
  assert.match(frame(), /45\/45/)
  assert.match(frame(), /fake-model-45/)
  assert.doesNotMatch(frame(), /fake-model-01\b/)
  assert.equal(rows().length, 12)
  app.stdin.write("\x1b[B")
  await tick()
  assert.match(frame(), /1\/45/)
  assert.match(frame(), /fake-model-01/)
})

test("several keys in one chunk are all handled", async () => {
  const { app, answers } = mount({ options: many })
  app.stdin.write("flagship 21\r")
  await tick()
  assert.deepEqual(answers, ["m21"])
})

test("the answer is sent once even when Enter is pressed twice", async () => {
  const { app, answers } = mount({ options: roles })
  app.stdin.write("\r")
  app.stdin.write("\r")
  await tick()
  assert.equal(answers.length, 1)
})

test("a secret dialog masks what is typed and answers with the real value", async () => {
  const { app, answers, frame } = mount({ kind: "secret", title: "API key", help_text: "Stored locally" })
  app.stdin.write("sk-abc123")
  await tick()
  const out = frame()
  assert.doesNotMatch(out, /sk-abc123|abc/)
  assert.match(out, /•{9}/)
  assert.match(out, /API key\s+esc/)
  app.stdin.write("\r")
  await tick()
  assert.deepEqual(answers, ["sk-abc123"])
  // Nothing about the value remains in what is drawn afterwards.
  assert.doesNotMatch(frame(), /sk-abc123/)
})

test("a text dialog shows its default and takes it on an empty Enter", async () => {
  const { app, answers, frame } = mount({ kind: "text", title: "Name", current_value: "chat-default" })
  assert.match(frame(), /chat-default/)
  app.stdin.write("\r")
  await tick()
  assert.deepEqual(answers, ["chat-default"])
})

test("a text dialog does not turn a leading slash into command suggestions", async () => {
  const { app, frame } = mount({ kind: "text", title: "Path" })
  app.stdin.write("/")
  await tick()
  assert.doesNotMatch(frame(), /\/help|Show commands/)
})

test("confirm denies by default: Enter and Esc deny, y approves", async () => {
  for (const [key, expected] of [["\r", false], ["\x1b", false], ["n", false], ["y", true]] as const) {
    const { app, answers, frame } = mount({ kind: "confirm", title: "Run command?", body: "rm -rf build" })
    assert.match(frame(), /Run command\?\s+esc/)
    assert.match(frame(), /rm -rf build/)
    app.stdin.write(key)
    await tick()
    assert.deepEqual(answers, [expected], `key ${JSON.stringify(key)}`)
  }
})

test("a long choice title wraps onto more lines instead of being cut", () => {
  const reason = "This change touches the session format and three frontends, so it is safer to agree on the approach before any file is edited."
  const { frame } = mount({
    title: reason,
    options: [
      { value: "plan", label: "Plan first (safer)", description: "" },
      { value: "start", label: "Start right away (faster, riskier)", description: "" },
    ],
  })
  const out = frame().replace(/\s+/g, " ")
  assert.match(out, /approach before any file is edited\./)
  assert.match(out, /Plan first \(safer\)/)
  assert.match(out, /Start right away/)
})

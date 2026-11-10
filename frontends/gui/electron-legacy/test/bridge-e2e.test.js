"use strict"

const assert = require("node:assert/strict")
const path = require("node:path")
const { once } = require("node:events")
const test = require("node:test")

const { KvistProcess } = require("../src/kvist-process")

test("runs every input request kind through a real child process", async () => {
  const fixture = path.join(__dirname, "fixtures", "mock-kvist")
  const runner = new KvistProcess({
    workspace: __dirname,
    command: process.execPath,
    prefixArgs: [fixture],
  })
  const events = []
  const answers = {
    choice: "smart",
    text: "Kvist GUI",
    secret: "never-log-this",
    confirm: true,
  }

  runner.on("event", (event) => {
    events.push(event)
    if (event.type !== "input_request") return
    if (event.kind === "confirm") {
      runner.respond({ request_id: event.request_id, approved: answers.confirm })
      return
    }
    runner.respond({ request_id: event.request_id, value: answers[event.kind] })
  })

  runner.start("Exercise the complete frontend bridge")
  const [exit] = await once(runner, "exit")

  assert.equal(exit.code, 0)
  assert.deepEqual(
    events.filter((event) => event.type === "input_request").map((event) => event.kind),
    ["choice", "text", "secret", "confirm"],
  )
  assert.equal(events[0].type, "hello")
  assert.equal(events.at(-1).type, "final")
  assert.equal(JSON.stringify(events).includes(answers.secret), false)
  assert.deepEqual(
    events.filter((event) => event.type === "stream_chunk").map((event) => event.text),
    [
      "I found the **stdio boundary**.",
      "I found the **stdio boundary**. The GUI is connected through NDJSON.",
    ],
  )
})

test("forwards status errors and terminal error events from a real child process", async () => {
  const fixture = path.join(__dirname, "fixtures", "mock-kvist")
  const runner = new KvistProcess({
    workspace: __dirname,
    command: process.execPath,
    prefixArgs: [fixture],
  })
  const events = []
  runner.on("event", (event) => events.push(event))

  runner.start("__error__")
  const [exit] = await once(runner, "exit")

  assert.equal(exit.code, 0)
  assert.equal(events[0].type, "hello")
  assert.equal(events.find((event) => event.type === "status").is_error, true)
  assert.deepEqual(events.at(-1), {
    type: "error",
    message: "Synthetic bridge failure",
  })
})

"use strict"

const assert = require("node:assert/strict")
const { EventEmitter, once } = require("node:events")
const { PassThrough } = require("node:stream")
const test = require("node:test")

const {
  KvistProcess,
  NdjsonDecoder,
  normalizeRunOptions,
  redactDiagnostic,
} = require("../src/kvist-process")

function fakeChild() {
  const child = new EventEmitter()
  child.stdout = new PassThrough()
  child.stderr = new PassThrough()
  child.stdin = new PassThrough()
  child.kill = (signal) => {
    queueMicrotask(() => child.emit("exit", null, signal))
    return true
  }
  child.stdin.on("finish", () => queueMicrotask(() => child.emit("exit", 0, null)))
  return child
}

test("NDJSON decoder handles split records and reports malformed input", () => {
  const values = []
  const errors = []
  const decoder = new NdjsonDecoder((value) => values.push(value), (error) => errors.push(error))

  decoder.push('{"type":"hel')
  decoder.push('lo","protocol_version":1}\nnot-json\n')

  assert.deepEqual(values, [{ type: "hello", protocol_version: 1 }])
  assert.equal(errors.length, 1)
})

test("starts Kvist with the required stdio frontend arguments", () => {
  const child = fakeChild()
  let invocation
  const processRunner = new KvistProcess({
    workspace: "/tmp/project",
    command: "kvist",
    spawnImpl: (command, args, options) => {
      invocation = { command, args, options }
      return child
    },
  })
  let stdin = ""
  child.stdin.on("data", (chunk) => { stdin += chunk.toString("utf8") })

  processRunner.start("Fix tests")

  assert.equal(invocation.command, "kvist")
  assert.deepEqual(invocation.args, [
    "--workspace", "/tmp/project",
    "--frontend", "stdio",
    "--role", "auto",
    "--approval-mode", "ask",
  ])
  assert.equal(invocation.options.cwd, "/tmp/project")
  assert.equal(stdin, "Fix tests\n")
})

test("passes provider, model, role, and fail-closed approval overrides", () => {
  const child = fakeChild()
  let args
  const processRunner = new KvistProcess({
    workspace: "/tmp/project",
    command: "kvist",
    spawnImpl: (_command, childArgs) => {
      args = childArgs
      return child
    },
  })

  processRunner.start("Inspect project", {
    provider: "omniroute",
    role: "devops",
    approvalMode: "deny",
    models: {
      fast: "ds-web/deepseek-v4-flash",
      smart: "ds-web/deepseek-v4-pro",
      planner: "anthropic/claude-sonnet-4.6",
    },
  })

  assert.deepEqual(args.slice(-12), [
    "--provider", "omniroute",
    "--fast-model", "ds-web/deepseek-v4-flash",
    "--smart-model", "ds-web/deepseek-v4-pro",
    "--planner-model", "anthropic/claude-sonnet-4.6",
    "--role", "devops",
    "--approval-mode", "deny",
  ])
})

test("requires either zero or three model overrides", () => {
  assert.throws(
    () => normalizeRunOptions({ models: { fast: "one" } }),
    /all three model roles/,
  )
})

test("preserves correlation and never echoes a secret response", () => {
  const child = fakeChild()
  const processRunner = new KvistProcess({
    workspace: "/tmp/project",
    command: "kvist",
    spawnImpl: () => child,
  })
  let stdin = ""
  const diagnostics = []
  child.stdin.on("data", (chunk) => { stdin += chunk.toString("utf8") })
  processRunner.on("diagnostic", (value) => diagnostics.push(value))
  processRunner.start("Configure provider")
  child.stdout.write('{"type":"hello","protocol_version":1}\n')
  child.stdout.write('{"type":"input_request","request_id":"secret-7","kind":"secret"}\n')

  processRunner.respond({ request_id: "secret-7", value: "private-value" })

  const lines = stdin.trim().split("\n")
  assert.deepEqual(JSON.parse(lines[1]), { request_id: "secret-7", value: "private-value" })
  assert.deepEqual(diagnostics, [])
})

test("shutdown denies pending confirmations", async () => {
  const child = fakeChild()
  const processRunner = new KvistProcess({
    workspace: "/tmp/project",
    command: "kvist",
    spawnImpl: () => child,
  })
  let stdin = ""
  child.stdin.on("data", (chunk) => { stdin += chunk.toString("utf8") })
  processRunner.start("Delete generated files")
  child.stdout.write('{"type":"hello","protocol_version":1}\n')
  child.stdout.write('{"type":"input_request","request_id":"danger-1","kind":"confirm"}\n')

  await processRunner.stop()

  const lines = stdin.trim().split("\n")
  assert.deepEqual(JSON.parse(lines[1]), { request_id: "danger-1", approved: false })
})

test("protocol mismatch is surfaced and stops the process", async () => {
  const child = fakeChild()
  const processRunner = new KvistProcess({
    workspace: "/tmp/project",
    command: "kvist",
    spawnImpl: () => child,
  })
  processRunner.start("Inspect project")
  const errorPromise = once(processRunner, "protocol-error")

  child.stdout.write('{"type":"hello","protocol_version":2}\n')

  const [error] = await errorPromise
  assert.match(error.message, /expected 1/)
})

test("diagnostics redact common API key forms", () => {
  const secret = "sk-proj-abcdefghijklmnopqrstuvwxyz"
  assert.equal(redactDiagnostic(`api_key=${secret}`), "api_key=[REDACTED]")
  assert.equal(redactDiagnostic(`Authorization: Bearer ${secret}`), "Authorization: Bearer [REDACTED]")
})

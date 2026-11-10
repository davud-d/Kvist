"use strict"

// One place that runs the core's one-shot operations (--session-op, --config-op,
// --journal-op, --skill-op, ...). Each prints a single JSON object on its last
// stdout line; secrets travel on stdin, never in argv, which `ps` can read.

const { spawn } = require("node:child_process")

const { redactDiagnostic } = require("./kvist-process")

const DEFAULT_TIMEOUT_MS = 30000
const MAX_STDOUT_BYTES = 16 * 1024 * 1024

function lastJsonLine(stdout) {
  const line = stdout.trim().split(/\r?\n/).filter(Boolean).at(-1)
  return JSON.parse(line || "")
}

// Resolves with the parsed payload even when the core exits non-zero: a refusal
// is `{ok: false, error}` and the caller decides what to show.
function runCoreJson(command, args, { cwd, stdin = "", timeout = DEFAULT_TIMEOUT_MS, failure }) {
  return new Promise((resolve, reject) => {
    const child = spawn(command.command, [...command.prefixArgs, ...args], {
      cwd,
      windowsHide: true,
      stdio: ["pipe", "pipe", "pipe"],
    })
    let stdout = ""
    let stderr = ""
    let overflow = false
    const timer = setTimeout(() => child.kill("SIGKILL"), timeout)
    child.stdout.setEncoding("utf8")
    child.stderr.setEncoding("utf8")
    child.stdout.on("data", (chunk) => {
      if (stdout.length + chunk.length > MAX_STDOUT_BYTES) {
        overflow = true
        child.kill("SIGKILL")
        return
      }
      stdout += chunk
    })
    child.stderr.on("data", (chunk) => { stderr = redactDiagnostic(stderr + chunk) })
    child.on("error", () => {
      clearTimeout(timer)
      reject(new Error(failure))
    })
    child.on("close", () => {
      clearTimeout(timer)
      if (overflow) {
        reject(new Error(`${failure} (the answer was too large)`))
        return
      }
      try {
        resolve(lastJsonLine(stdout))
      } catch {
        reject(new Error(stderr.trim().split(/\r?\n/).at(-1) || failure))
      }
    })
    child.stdin.on("error", () => {})
    child.stdin.end(stdin)
  })
}

async function runCoreOk(command, args, options) {
  const payload = await runCoreJson(command, args, options)
  if (!payload.ok) throw new Error(payload.error || payload.message || options.failure)
  return payload
}

module.exports = { runCoreJson, runCoreOk, lastJsonLine }

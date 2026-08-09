"use strict"

const { EventEmitter } = require("node:events")
const { spawn } = require("node:child_process")

const { PROTOCOL_VERSION } = require("./protocol-state")

class NdjsonDecoder {
  constructor(onValue, onError) {
    this.buffer = ""
    this.onValue = onValue
    this.onError = onError
  }

  push(chunk) {
    this.buffer += chunk.toString("utf8")
    const lines = this.buffer.split("\n")
    this.buffer = lines.pop() ?? ""
    for (const rawLine of lines) {
      this.decode(rawLine)
    }
  }

  end() {
    if (this.buffer.trim()) {
      this.decode(this.buffer)
    }
    this.buffer = ""
  }

  decode(rawLine) {
    const line = rawLine.trim()
    if (!line) return
    try {
      this.onValue(JSON.parse(line))
    } catch (error) {
      this.onError(new Error(`Invalid NDJSON from Kvist: ${error.message}`))
    }
  }
}

function redactDiagnostic(value) {
  return String(value)
    .replace(/\b(sk-(?:proj-)?[A-Za-z0-9_-]{16,})\b/g, "[REDACTED]")
    .replace(/(authorization\s*:\s*bearer\s+)[^\s]+/gi, "$1[REDACTED]")
    .replace(/((?:api[_-]?key|token|secret)\s*[=:]\s*)[^\s,;]+/gi, "$1[REDACTED]")
    .slice(-6000)
}

function normalizeRunOptions(options = {}) {
  const provider = String(options.provider || "").trim()
  const role = String(options.role || "auto").trim()
  const approvalMode = String(options.approvalMode || "ask").trim()
  const models = {
    fast: String(options.models?.fast || "").trim(),
    smart: String(options.models?.smart || "").trim(),
    planner: String(options.models?.planner || "").trim(),
  }
  const modelValues = Object.values(models)
  if (modelValues.some(Boolean) && !modelValues.every(Boolean)) {
    throw new Error("Set all three model roles, or leave all of them empty.")
  }
  if (!new Set(["auto", "coder", "devops", "chat"]).has(role)) {
    throw new Error("Unsupported Kvist role.")
  }
  if (!new Set(["ask", "deny"]).has(approvalMode)) {
    throw new Error("Unsupported approval mode.")
  }
  return { provider, role, approvalMode, models }
}

class KvistProcess extends EventEmitter {
  constructor({ workspace, command, prefixArgs = [], spawnImpl = spawn, env = process.env }) {
    super()
    this.workspace = workspace
    this.command = command
    this.prefixArgs = prefixArgs
    this.spawnImpl = spawnImpl
    this.env = env
    this.child = null
    this.handshakeComplete = false
    this.pendingRequests = new Map()
    this.stopping = false
  }

  start(goal, options = {}) {
    if (this.child) throw new Error("Kvist is already running.")
    const normalizedGoal = String(goal).replace(/\r?\n/g, " ").trim()
    if (!normalizedGoal) throw new Error("Task cannot be empty.")
    const runOptions = normalizeRunOptions(options)

    const args = [
      ...this.prefixArgs,
      "--workspace",
      this.workspace,
      "--frontend",
      "stdio",
    ]
    if (runOptions.provider) args.push("--provider", runOptions.provider)
    if (runOptions.models.fast) {
      args.push(
        "--fast-model", runOptions.models.fast,
        "--smart-model", runOptions.models.smart,
        "--planner-model", runOptions.models.planner,
      )
    }
    args.push("--role", runOptions.role, "--approval-mode", runOptions.approvalMode)
    const child = this.spawnImpl(this.command, args, {
      cwd: this.workspace,
      env: this.env,
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true,
    })
    this.child = child

    const decoder = new NdjsonDecoder(
      (event) => this.handleEvent(event),
      (error) => this.handleProtocolError(error),
    )
    child.stdout.on("data", (chunk) => decoder.push(chunk))
    child.stdout.on("end", () => decoder.end())
    child.stderr.on("data", (chunk) => {
      const diagnostic = redactDiagnostic(chunk.toString("utf8"))
      if (diagnostic.trim()) this.emit("diagnostic", diagnostic)
    })
    child.on("error", (error) => this.emit("process-error", error))
    child.on("exit", (code, signal) => {
      this.child = null
      this.pendingRequests.clear()
      this.emit("exit", { code, signal, expected: this.stopping })
    })

    child.stdin.write(normalizedGoal + "\n")
  }

  handleEvent(event) {
    if (!event || typeof event !== "object" || typeof event.type !== "string") {
      this.handleProtocolError(new Error("Kvist emitted a non-event JSON value."))
      return
    }

    if (!this.handshakeComplete) {
      if (event.type !== "hello") {
        this.handleProtocolError(new Error("The first Kvist event was not hello."))
        return
      }
      if (event.protocol_version !== PROTOCOL_VERSION) {
        this.handleProtocolError(
          new Error(`Unsupported Kvist protocol ${event.protocol_version}; expected ${PROTOCOL_VERSION}.`),
        )
        return
      }
      this.handshakeComplete = true
    }

    if (event.type === "input_request" && typeof event.request_id === "string") {
      this.pendingRequests.set(event.request_id, event.kind)
    }
    this.emit("event", event)
  }

  handleProtocolError(error) {
    this.emit("protocol-error", error)
    void this.stop()
  }

  respond(payload) {
    if (!this.child || !this.child.stdin.writable) {
      throw new Error("Kvist is not accepting input.")
    }
    if (!payload || typeof payload.request_id !== "string") {
      throw new TypeError("A request_id is required.")
    }

    const kind = this.pendingRequests.get(payload.request_id)
    if (!kind) throw new Error("The input request is no longer pending.")

    let response
    if (kind === "confirm") {
      response = { request_id: payload.request_id, approved: payload.approved === true }
    } else {
      const value = payload.value
      if (value !== null && typeof value !== "string") {
        throw new TypeError("Input response value must be a string or null.")
      }
      response = { request_id: payload.request_id, value }
    }

    this.pendingRequests.delete(payload.request_id)
    this.child.stdin.write(JSON.stringify(response) + "\n")
  }

  failClosedPending() {
    if (!this.child || !this.child.stdin.writable) return
    for (const [requestId, kind] of this.pendingRequests) {
      const response = kind === "confirm"
        ? { request_id: requestId, approved: false }
        : { request_id: requestId, value: null }
      this.child.stdin.write(JSON.stringify(response) + "\n")
    }
    this.pendingRequests.clear()
  }

  stop() {
    const child = this.child
    if (!child) return Promise.resolve()
    this.stopping = true
    this.failClosedPending()
    child.stdin.end()

    return new Promise((resolve) => {
      let settled = false
      const finish = () => {
        if (settled) return
        settled = true
        resolve()
      }
      child.once("exit", finish)
      const terminateTimer = setTimeout(() => {
        if (this.child === child) child.kill("SIGTERM")
      }, 500)
      const killTimer = setTimeout(() => {
        if (this.child === child) child.kill("SIGKILL")
        finish()
      }, 2500)
      terminateTimer.unref()
      killTimer.unref()
    })
  }
}

module.exports = { KvistProcess, NdjsonDecoder, normalizeRunOptions, redactDiagnostic }

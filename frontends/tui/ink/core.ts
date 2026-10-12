import { spawn, type ChildProcess } from "node:child_process"
import { EventEmitter } from "node:events"
import { existsSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

import { PROTOCOL_VERSION, type CoreEvent, type InputKind, type Reply } from "./protocol.js"

export interface CoreCommand {
  command: string
  prefixArgs: string[]
}

export interface CoreOptions {
  workspace: string
  role: string
  approvalMode: string
  resume?: string
  provider?: string
  models?: { fast: string; smart: string }
  env?: Record<string, string>
}

// The Python launcher passes the exact interpreter/binary it is running as, so
// a frozen `kvist` binary and a source checkout both work without guessing.
export function resolveCoreCommand(env: NodeJS.ProcessEnv = process.env): CoreCommand {
  if (env.KVIST_CORE_CMD) {
    const parts: unknown = JSON.parse(env.KVIST_CORE_CMD)
    if (!Array.isArray(parts) || parts.length === 0 || !parts.every((p) => typeof p === "string")) {
      throw new Error("KVIST_CORE_CMD must be a non-empty JSON array of strings.")
    }
    return { command: parts[0] as string, prefixArgs: parts.slice(1) as string[] }
  }
  const repoRoot = findRepoRoot(path.dirname(fileURLToPath(import.meta.url)))
  const venvPython = path.join(repoRoot, ".venv", "bin", "python")
  const python = existsSync(venvPython) ? venvPython : "python3"
  return { command: python, prefixArgs: [path.join(repoRoot, "kernel", "__main__.py")] }
}

// Without the launcher (`npm run dev`, a bare `node dist-ui/kvist-tui.mjs`) the
// core is the checkout this file sits in; the source and the bundle sit at
// different depths inside it.
function findRepoRoot(start: string): string {
  let dir = start
  while (!existsSync(path.join(dir, "kernel", "__main__.py"))) {
    const parent = path.dirname(dir)
    if (parent === dir) return start
    dir = parent
  }
  return dir
}

export function splitLines(buffer: string): { lines: string[]; rest: string } {
  const parts = buffer.split("\n")
  return { lines: parts.slice(0, -1), rest: parts.at(-1) ?? "" }
}

function coreArgs(options: CoreOptions): string[] {
  const args = ["--workspace", options.workspace, "--frontend", "stdio-multi"]
  if (options.resume !== undefined) args.push("--resume", ...(options.resume ? [options.resume] : []))
  if (options.provider) args.push("--provider", options.provider)
  if (options.models) {
    args.push(
      "--fast-model", options.models.fast,
      "--smart-model", options.models.smart,
    )
  }
  args.push("--role", options.role, "--approval-mode", options.approvalMode)
  return args
}

// Events: "event" (CoreEvent), "diagnostic" (stderr text), "fault" (Error),
// "exit" ({ code, expected }).
export class KvistCore extends EventEmitter {
  private child: ChildProcess | null = null
  private pending = new Map<string, InputKind>()
  private stopping = false

  constructor(private readonly command: CoreCommand) {
    super()
  }

  get running(): boolean {
    return this.child !== null
  }

  start(options: CoreOptions): void {
    if (this.child) throw new Error("Kvist core is already running.")
    this.stopping = false
    const child = spawn(this.command.command, [...this.command.prefixArgs, ...coreArgs(options)], {
      cwd: options.workspace,
      env: { ...process.env, ...options.env },
      stdio: ["pipe", "pipe", "pipe"],
    })
    this.child = child

    let buffer = ""
    let greeted = false
    child.stdout.setEncoding("utf8")
    child.stdout.on("data", (chunk: string) => {
      const { lines, rest } = splitLines(buffer + chunk)
      buffer = rest
      for (const line of lines) greeted = this.decode(line, greeted)
    })
    child.stderr.setEncoding("utf8")
    child.stderr.on("data", (text: string) => this.emit("diagnostic", text))
    child.on("error", (error) => this.emit("fault", error))
    child.on("exit", (code) => {
      this.child = null
      this.pending.clear()
      this.emit("exit", { code, expected: this.stopping })
    })
  }

  private decode(raw: string, greeted: boolean): boolean {
    const line = raw.trim()
    if (!line) return greeted
    let event: CoreEvent
    try {
      event = JSON.parse(line) as CoreEvent
    } catch {
      this.emit("fault", new Error(`Core emitted invalid JSON: ${line.slice(0, 120)}`))
      return greeted
    }
    if (!greeted) {
      if (event.type !== "hello" || event.protocol_version !== PROTOCOL_VERSION) {
        this.emit("fault", new Error(`Unsupported core handshake (expected hello, protocol ${PROTOCOL_VERSION}).`))
        void this.stop()
        return greeted
      }
    }
    if (event.type === "input_request") this.pending.set(event.request_id, event.kind)
    this.emit("event", event)
    return true
  }

  private write(payload: unknown): void {
    const stdin = this.child?.stdin
    if (!stdin?.writable) throw new Error("Kvist core is not accepting input.")
    stdin.write(JSON.stringify(payload) + "\n")
  }

  // A goal is one stdin line; one with line breaks travels as a JSON `goal` message so they survive.
  send(goal: string): void {
    const text = goal.replace(/\r\n?/g, "\n").trim()
    if (!text) return
    if (text.includes("\n")) return this.write({ type: "goal", text })
    const stdin = this.child?.stdin
    if (!stdin?.writable) throw new Error("Kvist core is not accepting input.")
    stdin.write(text + "\n")
  }

  // A slash command that runs inside the core (UI_API.md §8c); its dialogs come
  // back as ordinary input requests and it ends with a `command_result`.
  sendCommand(name: string, args: string[]): void {
    this.write({ type: "command", name, args })
  }

  // `remember` matters only for an approval that was granted.
  respond(requestId: string, answer: boolean | string | null, remember = false): void {
    const kind = this.pending.get(requestId)
    if (!kind) return
    this.pending.delete(requestId)
    const reply: Reply =
      kind === "confirm"
        ? { request_id: requestId, approved: answer === true, ...(answer === true && remember ? { remember: true as const } : {}) }
        : { request_id: requestId, value: typeof answer === "string" ? answer : null }
    this.write(reply)
  }

  // "Stop all": the turn, its helpers, calls and every process the task owns.
  cancel(): void {
    this.write({ type: "cancel" })
  }

  // Pause at the next safe point; the task and its live resources stay.
  pause(): void {
    this.write({ type: "pause" })
  }

  stopHelper(agentId: string): void {
    this.write({ type: "helper_stop", agent_id: agentId })
  }

  // Closing stdin is the core's normal exit signal; open requests deny first.
  stop(): Promise<void> {
    const child = this.child
    if (!child) return Promise.resolve()
    this.stopping = true
    for (const [id, kind] of this.pending) {
      this.write(kind === "confirm" ? { request_id: id, approved: false } : { request_id: id, value: null })
    }
    this.pending.clear()
    child.stdin?.end()
    return new Promise((resolve) => {
      child.once("exit", () => resolve())
      setTimeout(() => child.kill("SIGTERM"), 500).unref()
      setTimeout(() => child.kill("SIGKILL"), 2500).unref()
    })
  }
}

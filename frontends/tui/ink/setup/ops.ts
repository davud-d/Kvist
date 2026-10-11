// One-shot core operations (`--config-op`, no model call): the payload goes in on stdin so an API
// key never lands in argv, and the last line of stdout is the JSON reply.
import { spawn } from "node:child_process"

import type { CoreCommand } from "../core.js"

export interface ProviderInfo {
  id: string
  label: string
  auth_type: "api_key" | "oauth"
  requires_manual_models: boolean
  has_credentials: boolean
}

export interface ConfigInfo {
  ok: true
  saved_provider: string
  providers: ProviderInfo[]
}

export type OpReply<T> = (T & { ok: true }) | { ok: false; error: string }

export interface SetProviderReply { provider: string; models: Record<string, string> }
export interface ListModelsReply { provider: string; models: string[]; error?: string | null }
export interface RegistrationReply { opened: boolean; url: string | null }

// A ChatGPT sign-in waits for the person in the browser.
export const LOGIN_TIMEOUT_MS = 190_000
const DEFAULT_TIMEOUT_MS = 30_000

export function configOp<T>(command: CoreCommand, op: string, payload: unknown = null, timeoutMs = DEFAULT_TIMEOUT_MS, signal?: AbortSignal): Promise<OpReply<T>> {
  return new Promise((resolve) => {
    let out = ""
    const child = spawn(command.command, [...command.prefixArgs, "--config-op", op], { stdio: ["pipe", "pipe", "ignore"] })
    const timer = setTimeout(() => {
      child.kill()
      resolve({ ok: false, error: "Kvist did not answer in time." })
    }, timeoutMs)
    signal?.addEventListener("abort", () => {
      clearTimeout(timer)
      child.kill()
      resolve({ ok: false, error: "Cancelled." })
    })
    child.stdout.on("data", (chunk: Buffer) => (out += chunk.toString()))
    child.on("error", (error) => {
      clearTimeout(timer)
      resolve({ ok: false, error: error.message })
    })
    child.on("close", () => {
      clearTimeout(timer)
      try {
        resolve(JSON.parse(out.trim().split("\n").at(-1) ?? "") as OpReply<T>)
      } catch {
        resolve({ ok: false, error: "Kvist returned an unreadable reply." })
      }
    })
    child.stdin.on("error", () => undefined)
    child.stdin.end(JSON.stringify(payload))
  })
}

// The release installer (scripts/release/install.sh), driven as a child process: `--plan` prints
// the facts about this machine, `--only … --events` installs and reports one JSON object per line.
export interface InstallScript {
  path: string
  // A folder of assets the bootstrap has already downloaded (`--cache`); the rest is downloaded.
  cache?: string
}

export interface PlanComponent {
  id: string
  available: boolean
  // Why an unavailable item is not offered: "present" or "unsupported".
  reason?: string
  // Paths and commands the component would touch or run.
  targets: string[]
}

export interface PlanReply {
  os: string
  arch: string
  shell: string
  privilege: "root" | "cached" | "password" | "none"
  components: PlanComponent[]
}

export type InstallEvent =
  | { event: "step" | "warning" | "error"; message: string }
  | { event: "component"; id: string; status: "start" | "done" | "failed" }
  | { event: "done" }

export type InstallReply = { ok: true } | { ok: false; error: string }

// The bootstrap that starts the installer screen names the script in KVIST_INSTALL_SCRIPT.
export function installScriptFromEnv(env: NodeJS.ProcessEnv): InstallScript | null {
  const path = env.KVIST_INSTALL_SCRIPT
  if (!path) return null
  return env.KVIST_INSTALL_CACHE ? { path, cache: env.KVIST_INSTALL_CACHE } : { path }
}

function scriptArgs(script: InstallScript, rest: string[]): string[] {
  return [script.path, ...(script.cache ? ["--cache", script.cache] : []), ...rest]
}

export function readPlan(script: InstallScript): Promise<OpReply<PlanReply>> {
  return new Promise((resolve) => {
    let out = ""
    const child = spawn("bash", scriptArgs(script, ["--plan"]), { stdio: ["ignore", "pipe", "ignore"] })
    child.stdout.on("data", (chunk: Buffer) => (out += chunk.toString()))
    child.on("error", (error) => resolve({ ok: false, error: error.message }))
    child.on("close", (code) => {
      if (code !== 0) return resolve({ ok: false, error: `The installer exited with code ${code}.` })
      try {
        resolve({ ok: true, ...(JSON.parse(out) as PlanReply) })
      } catch {
        resolve({ ok: false, error: "The installer returned an unreadable plan." })
      }
    })
  })
}

// Runs the chosen components; resolves when the script ends. A line that is not JSON is ignored.
export function runInstall(script: InstallScript, only: string[], onEvent: (event: InstallEvent) => void, signal?: AbortSignal): Promise<InstallReply> {
  return new Promise((resolve) => {
    let buffer = ""
    let failure = ""
    const child = spawn("bash", scriptArgs(script, ["--only", only.join(","), "--events"]), { stdio: ["ignore", "pipe", "ignore"] })
    signal?.addEventListener("abort", () => child.kill())
    const take = (line: string) => {
      let event: InstallEvent
      try {
        event = JSON.parse(line) as InstallEvent
      } catch {
        return
      }
      if (event.event === "error") failure = event.message
      onEvent(event)
    }
    child.stdout.on("data", (chunk: Buffer) => {
      buffer += chunk.toString()
      const lines = buffer.split("\n")
      buffer = lines.pop() ?? ""
      lines.forEach(take)
    })
    child.on("error", (error) => resolve({ ok: false, error: error.message }))
    child.on("close", (code) => {
      if (buffer) take(buffer)
      resolve(code === 0 ? { ok: true } : { ok: false, error: failure || `The installer exited with code ${code}.` })
    })
  })
}

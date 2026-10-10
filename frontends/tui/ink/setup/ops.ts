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

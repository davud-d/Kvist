import { execFile } from "node:child_process"

import type { CoreCommand } from "./core.js"
import type { JournalEvent } from "./protocol.js"

export interface SessionStatus {
  ok: boolean
  error?: string
  active_session?: string
  sessions?: string[]
  // Only `list` reports these: `journal_bytes` is 0 for a session nothing happened in.
  session_items?: { name: string; has_checkpoint: boolean; journal_bytes: number }[]
}

export interface SessionHistory {
  ok: boolean
  error?: string
  events: JournalEvent[]
}

// Session lifecycle is a one-shot mode of the core; the UI never touches .kvist/.
function runOp<T extends { ok: boolean; error?: string }>(command: CoreCommand, workspace: string, extra: string[], input?: string): Promise<T | { ok: false; error: string }> {
  const args = [...command.prefixArgs, "--workspace", workspace, ...extra]
  return new Promise((resolve) => {
    const child = execFile(command.command, args, { cwd: workspace, timeout: 20_000, maxBuffer: 64 * 1024 * 1024 }, (error, stdout) => {
      try {
        resolve(JSON.parse(stdout.trim().split("\n").at(-1) ?? "") as T)
      } catch {
        resolve({ ok: false, error: error?.message ?? "Unreadable session-op reply." })
      }
    })
    if (input !== undefined) child.stdin?.end(input)
  })
}

export function sessionOp(
  command: CoreCommand,
  workspace: string,
  op: "status" | "list" | "launch" | "new" | "switch" | "delete",
  name?: string,
): Promise<SessionStatus> {
  return runOp<SessionStatus>(command, workspace, ["--session-op", op, ...(name ? ["--session-name", name] : [])])
}

const HISTORY_LIMIT = 500

// The newest events of a session's journal, oldest first (UI_API.md, "Restoring a session's feed").
export async function sessionHistory(command: CoreCommand, workspace: string, name: string): Promise<SessionHistory> {
  const reply = await runOp<{ ok: boolean; error?: string; events?: { event: JournalEvent }[] }>(command, workspace, [
    "--session-op", "history", "--session-name", name, "--history-limit", String(HISTORY_LIMIT),
  ])
  return { ok: reply.ok, error: reply.error, events: "events" in reply ? (reply.events ?? []).map((entry) => entry.event) : [] }
}

// The workspace's last inputs, oldest first, kept by the core across restarts.
export async function inputHistory(command: CoreCommand, workspace: string): Promise<string[]> {
  const reply = await runOp<{ ok: boolean; error?: string; entries?: string[] }>(command, workspace, ["--session-op", "input-history"])
  return "entries" in reply ? (reply.entries ?? []) : []
}

// The core skips blank, repeated and over-long inputs and keeps the last hundred.
export function addInputHistory(command: CoreCommand, workspace: string, text: string): Promise<unknown> {
  return runOp(command, workspace, ["--session-op", "input-history-add"], text)
}

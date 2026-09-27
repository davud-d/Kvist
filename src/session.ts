import { execFile } from "node:child_process"

import type { CoreCommand } from "./core.js"

export interface SessionStatus {
  ok: boolean
  error?: string
  active_session?: string
  sessions?: string[]
}

// Session lifecycle is a one-shot mode of the core; the UI never touches .kvist/.
export function sessionOp(
  command: CoreCommand,
  workspace: string,
  op: "status" | "launch" | "new" | "switch" | "delete",
  name?: string,
): Promise<SessionStatus> {
  const args = [...command.prefixArgs, "--workspace", workspace, "--session-op", op]
  if (name) args.push("--session-name", name)
  return new Promise((resolve) => {
    execFile(command.command, args, { cwd: workspace, timeout: 20_000, maxBuffer: 8 * 1024 * 1024 }, (error, stdout) => {
      try {
        resolve(JSON.parse(stdout.trim().split("\n").at(-1) ?? "") as SessionStatus)
      } catch {
        resolve({ ok: false, error: error?.message ?? "Unreadable session-op reply." })
      }
    })
  })
}

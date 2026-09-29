import { appendFileSync, mkdirSync } from "node:fs"
import path from "node:path"

/**
 * Sends Node process warnings (MaxListenersExceededWarning, deprecations) to `file`
 * instead of stderr, where they would print over the screen Ink is drawing.
 * Removes Node's default `warning` listener, so it must run before the first render.
 */
export function routeWarningsToLog(file: string): void {
  mkdirSync(path.dirname(file), { recursive: true })
  process.removeAllListeners("warning")
  process.on("warning", (warning) => {
    try {
      appendFileSync(file, `${new Date().toISOString()} ${warning.stack ?? `${warning.name}: ${warning.message}`}\n`)
    } catch {
      // A log that cannot be written (read-only workspace, full disk) must not become a crash or a message over the UI.
    }
  })
}

// What stays on the terminal after the fullscreen skin exits: the alternate
// screen is gone, so this is all that is left of the session on the real screen.
// The classic terminal prints the same words (main.render_exit_message).
import { LOGO_COLORS, LOGO_WIDTH, logoRows } from "./ui/logo.js"

const DIM = "\x1b[2m"
const BOLD = "\x1b[1m"
const RED = "\x1b[31m"
const RESET = "\x1b[0m"

function rgb(hex: string): string {
  const n = Number.parseInt(hex.slice(1), 16)
  return `\x1b[38;2;${(n >> 16) & 255};${(n >> 8) & 255};${n & 255}m`
}

// `session` is the session to come back to, or null when there is none worth resuming.
export function farewellText(session: string | null, columns: number): string {
  const size = columns >= LOGO_WIDTH.large + 2 ? "large" : "compact"
  const logo = logoRows(size).map((row) => `${rgb(LOGO_COLORS.shade)}${row.shade}${rgb(LOGO_COLORS.main)}${row.main}${RESET}`)
  const lines = ["", ...logo, "", `${DIM}Kvist agent${RESET} ${RED}stopped.${RESET}`]
  if (session) lines.push(`${DIM}To continue, start the agent and resume session ${RESET}${BOLD}${DIM}${session}${RESET}${DIM}.${RESET}`)
  return lines.join("\n") + "\n\n"
}

// The terminal window title ("Kvist | session name"), set with OSC 2 so i3 and Hyprland name
// the window after the session instead of the shell's directory.

const PRODUCT = "Kvist"
const MAX_TITLE_CHARS = 80
// Sessions run one per process, so one remembered value is enough to skip repeated writes.
let pushed = false
let written: string | null = null

/** `Kvist | <title>`, or `Kvist` while the session has none. Control characters are dropped: the title comes from a model. */
export function formatWindowTitle(sessionTitle: string | undefined): string {
  const clean = (sessionTitle ?? "").replace(/[\p{Cc}\p{Cf}]/gu, "").trim()
  return clean ? `${PRODUCT} | ${clean.slice(0, MAX_TITLE_CHARS)}` : PRODUCT
}

function write(sequence: string): void {
  if (!process.stdout.isTTY) return
  process.stdout.write(sequence)
}

export function setWindowTitle(sessionTitle: string | undefined): void {
  const title = formatWindowTitle(sessionTitle)
  if (title === written) return
  if (!pushed) {
    pushed = true
    write("\x1b[22;2t")
  }
  written = title
  write(`\x1b]2;${title}\x07`)
}

/** Gives the terminal back the title it had before Kvist set one. */
export function restoreWindowTitle(): void {
  if (!pushed) return
  pushed = false
  written = null
  write("\x1b[23;2t")
}

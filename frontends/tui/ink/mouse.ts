// Mouse reporting is switched on only while a screen wants clicks. The
// terminal keeps the mode after we exit, which would take away text
// selection from the user's shell, so every way out turns it off.

const CLICKS = "\x1b[?1000h\x1b[?1006h"
// Also reports the pointer moving with a button held: what a drag selection needs.
const CLICKS_AND_DRAGS = "\x1b[?1002h\x1b[?1006h"
// Also reports the pointer moving with no button held: what hover effects need.
const CLICKS_AND_HOVER = "\x1b[?1003h\x1b[?1006h"
const OFF = "\x1b[?1000l\x1b[?1002l\x1b[?1003l\x1b[?1006l"
const SIGNALS = ["SIGINT", "SIGTERM", "SIGHUP"] as const

let out: NodeJS.WriteStream | null = null

function release(): void {
  if (!out) return
  const stream = out
  out = null
  process.removeListener("exit", release)
  for (const signal of SIGNALS) process.removeListener(signal, onSignal)
  stream.write(OFF)
}

// Our listener replaces the default action, so the signal is re-raised.
function onSignal(signal: NodeJS.Signals): void {
  release()
  process.kill(process.pid, signal)
}

export function enableMouse(stream: NodeJS.WriteStream, drags = false, hover = false): void {
  if (out || !stream.isTTY) return
  out = stream
  stream.write(hover ? CLICKS_AND_HOVER : drags ? CLICKS_AND_DRAGS : CLICKS)
  process.on("exit", release)
  for (const signal of SIGNALS) process.on(signal, onSignal)
}

export function disableMouse(): void {
  release()
}

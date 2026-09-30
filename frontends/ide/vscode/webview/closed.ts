// A session end_session closed (UI_API.md, "Closed sessions"): the composer is gone, and the
// only key the panel takes is Enter, which opens a new session.

export interface KeyPress {
  key: string
  shiftKey: boolean
  ctrlKey: boolean
  altKey: boolean
  metaKey: boolean
  // The element the key went to; a control keeps its own Enter.
  targetTag: string
}

const CONTROLS = new Set(["BUTTON", "SELECT", "INPUT", "TEXTAREA", "A"])

export function opensNewSession(press: KeyPress): boolean {
  if (press.key !== "Enter" || press.shiftKey || press.ctrlKey || press.altKey || press.metaKey) return false
  return !CONTROLS.has(press.targetTag.toUpperCase())
}

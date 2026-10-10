// Turns the bytes a terminal sends into key, mouse and paste events.
export type KeyName =
  | "up" | "down" | "left" | "right" | "home" | "end" | "pageup" | "pagedown"
  | "enter" | "escape" | "backspace" | "delete" | "tab" | "shift-tab" | "char"

export type InputEvent =
  | { type: "key"; key: KeyName; text: string; ctrl: boolean }
  | { type: "mouse"; kind: "press" | "release" | "wheel" | "move"; row: number; col: number; delta: number }
  | { type: "paste"; text: string }

const CSI_KEYS: Record<string, KeyName> = {
  A: "up", B: "down", C: "right", D: "left", H: "home", F: "end", Z: "shift-tab",
}
const TILDE_KEYS: Record<string, KeyName> = {
  "1": "home", "3": "delete", "4": "end", "5": "pageup", "6": "pagedown", "7": "home", "8": "end",
}
const PASTE_START = "\x1b[200~"
const PASTE_END = "\x1b[201~"
// CSI: parameters, intermediates, final byte; SS3: one letter.
const CSI = /^\x1b\[([0-9;<?]*)([ -/]*)([@-~])/
const SS3 = /^\x1bO([A-Za-z])/

function key(name: KeyName, text = "", ctrl = false): InputEvent {
  return { type: "key", key: name, text, ctrl }
}

// Parses as much of `data` as is complete. A sequence cut off at the end of the chunk stays in
// `rest`; with `final` set (the terminal has been quiet for a moment) a lone ESC is the Escape key.
export function parseInput(data: string, final = false): { events: InputEvent[]; rest: string } {
  const events: InputEvent[] = []
  let at = 0
  while (at < data.length) {
    const rest = data.slice(at)
    if (rest.startsWith(PASTE_START)) {
      const end = rest.indexOf(PASTE_END)
      if (end < 0) return { events, rest }
      events.push({ type: "paste", text: rest.slice(PASTE_START.length, end) })
      at += end + PASTE_END.length
      continue
    }
    if (rest[0] === "\x1b") {
      if (rest.length === 1) {
        if (!final) return { events, rest }
        events.push(key("escape"))
        at += 1
        continue
      }
      // The old mouse encoding, ESC [ M and three bytes, for terminals without SGR reports (macOS's
      // Terminal.app): read it as the same events, never as typed characters.
      if (rest.startsWith("\x1b[M")) {
        if (rest.length < 6 && !final) return { events, rest }
        const [button = 32, col = 33, row = 33] = [...rest.slice(3, 6)].map((char) => char.charCodeAt(0))
        const code = button - 32
        const place = { row: row - 33, col: col - 33 }
        if (code & 64) events.push({ type: "mouse", kind: "wheel", ...place, delta: code & 1 ? 1 : -1 })
        else if (code & 32) events.push({ type: "mouse", kind: "move", ...place, delta: 0 })
        else events.push({ type: "mouse", kind: (code & 3) === 3 ? "release" : "press", ...place, delta: 0 })
        at += Math.min(6, rest.length)
        continue
      }
      const csi = CSI.exec(rest)
      if (csi) {
        at += csi[0].length
        const [, params = "", , finalByte = ""] = csi
        if (params.startsWith("<") && (finalByte === "M" || finalByte === "m")) {
          const [button = 0, col = 1, row = 1] = params.slice(1).split(";").map(Number)
          if (button & 64) events.push({ type: "mouse", kind: "wheel", row: row - 1, col: col - 1, delta: button & 1 ? 1 : -1 })
          else if (button & 32) events.push({ type: "mouse", kind: "move", row: row - 1, col: col - 1, delta: 0 })
          else if ((button & 3) === 0) events.push({ type: "mouse", kind: finalByte === "M" ? "press" : "release", row: row - 1, col: col - 1, delta: 0 })
        } else if (finalByte === "~") {
          const name = TILDE_KEYS[params.split(";")[0] ?? ""]
          if (name) events.push(key(name))
        } else if (CSI_KEYS[finalByte]) {
          events.push(key(CSI_KEYS[finalByte]!))
        }
        continue
      }
      const ss3 = SS3.exec(rest)
      if (ss3) {
        const name = CSI_KEYS[ss3[1]!]
        if (name) events.push(key(name))
        at += ss3[0].length
        continue
      }
      // An incomplete sequence waits for its tail; anything else is Escape followed by a key.
      if (!final && /^\x1b[[O]?[0-9;<?]*[ -/]*$/.test(rest)) return { events, rest }
      events.push(key("escape"))
      at += 1
      continue
    }
    const char = String.fromCodePoint(rest.codePointAt(0)!)
    at += char.length
    if (char === "\r" || char === "\n") events.push(key("enter"))
    else if (char === "\t") events.push(key("tab"))
    else if (char === "\x7f" || char === "\b") events.push(key("backspace"))
    else if (char < " ") events.push(key("char", String.fromCharCode(char.charCodeAt(0) + 96), true))
    else events.push(key("char", char))
  }
  return { events, rest: "" }
}

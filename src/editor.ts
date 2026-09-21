// Pure single-line editing so the key handling is testable without a terminal.

export interface Editor {
  text: string
  cursor: number
}

export interface KeyLike {
  leftArrow?: boolean
  rightArrow?: boolean
  backspace?: boolean
  delete?: boolean
  ctrl?: boolean
  meta?: boolean
  home?: boolean
  end?: boolean
}

export const emptyEditor: Editor = { text: "", cursor: 0 }

export function insert(ed: Editor, raw: string): Editor {
  // A lone \r would move the terminal cursor and corrupt the row; other
  // control characters have no business in a goal either.
  const chunk = raw.replace(/\r\n|\r|\n/g, " ").replace(/[\x00-\x08\x0b-\x1f\x7f]/g, "")
  return { text: ed.text.slice(0, ed.cursor) + chunk + ed.text.slice(ed.cursor), cursor: ed.cursor + chunk.length }
}

function wordStart(text: string, cursor: number): number {
  let i = cursor
  while (i > 0 && text[i - 1] === " ") i--
  while (i > 0 && text[i - 1] !== " ") i--
  return i
}

// Returns null when the key is not an editing key (the caller may use it).
export function applyKey(ed: Editor, input: string, key: KeyLike): Editor | null {
  const { text, cursor } = ed
  if (key.leftArrow) return { text, cursor: Math.max(0, cursor - 1) }
  if (key.rightArrow) return { text, cursor: Math.min(text.length, cursor + 1) }
  if (key.home) return { text, cursor: 0 }
  if (key.end) return { text, cursor: text.length }
  // Terminals disagree on whether Backspace is "backspace" or "delete".
  if (key.backspace || key.delete) {
    if (cursor === 0) return ed
    return { text: text.slice(0, cursor - 1) + text.slice(cursor), cursor: cursor - 1 }
  }
  if (key.ctrl) {
    if (input === "a") return { text, cursor: 0 }
    if (input === "e") return { text, cursor: text.length }
    if (input === "u") return { text: text.slice(cursor), cursor: 0 }
    if (input === "k") return { text: text.slice(0, cursor), cursor }
    if (input === "w") {
      const start = wordStart(text, cursor)
      return { text: text.slice(0, start) + text.slice(cursor), cursor: start }
    }
    return null
  }
  if (key.meta || !input) return null
  return insert(ed, input)
}

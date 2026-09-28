// Pure single-line editing so the key handling is testable without a terminal.
import stringWidth from "string-width"

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
  // A lone \r would move the terminal cursor and corrupt the row. A tab is
  // printed at a terminal-defined tab stop while the layout counts it as zero
  // cells, so the frame prints wider than Ink measures and its row-by-row
  // erase then leaves a stale row behind — the same for the other control
  // characters, which have no business in a goal either.
  const chunk = raw.replace(/\r\n|\r|\n|\t/g, " ").replace(/[\x00-\x08\x0b-\x1f\x7f]/g, "")
  return { text: ed.text.slice(0, ed.cursor) + chunk + ed.text.slice(ed.cursor), cursor: ed.cursor + chunk.length }
}

// The visible part of the single input line, cursor split out for rendering.
export interface WindowLine {
  before: string
  at: string
  after: string
}

function takeCells(text: string, cells: number): string {
  let out = ""
  for (const char of text) {
    if (stringWidth(out + char) > cells) break
    out += char
  }
  return out
}

function takeCellsFromEnd(text: string, cells: number): string {
  let out = ""
  for (const char of [...text].reverse()) {
    if (stringWidth(char + out) > cells) break
    out = char + out
  }
  return out
}

// The box shows a window of the line, not the whole line: Ink redraws the live
// region in place by erasing the previous frame row by row, and a frame that
// reaches the terminal height makes Ink fall back to a full-screen clear on
// every redraw. `cells` is the display budget of the box; the window keeps the
// cursor visible, with a quarter of the budget left for the text after it.
export function windowLine(ed: Editor, cells: number): WindowLine {
  const at = ed.text.slice(ed.cursor, ed.cursor + 1)
  const before = ed.text.slice(0, ed.cursor)
  const after = ed.text.slice(ed.cursor + 1)
  if (stringWidth(ed.text) <= cells) return { before, at, after }
  const shownAfter = takeCells(after, Math.floor(cells / 4))
  const shownBefore = takeCellsFromEnd(before, cells - stringWidth(at) - stringWidth(shownAfter) - 2)
  return {
    before: shownBefore.length < before.length ? `…${shownBefore}` : shownBefore,
    at,
    after: shownAfter.length < after.length ? `${shownAfter}…` : shownAfter,
  }
}

function wordStart(text: string, cursor: number): number {
  let i = cursor
  while (i > 0 && text[i - 1] === " ") i--
  while (i > 0 && text[i - 1] !== " ") i--
  return i
}

function wordEnd(text: string, cursor: number): number {
  let i = cursor
  while (i < text.length && text[i] === " ") i++
  while (i < text.length && text[i] !== " ") i++
  return i
}

// Returns null when the key is not an editing key (the caller may use it).
export function applyKey(ed: Editor, input: string, key: KeyLike): Editor | null {
  const { text, cursor } = ed
  if (key.leftArrow) return { text, cursor: Math.max(0, cursor - 1) }
  if (key.rightArrow) return { text, cursor: Math.min(text.length, cursor + 1) }
  if (key.home) return { text, cursor: 0 }
  if (key.end) return { text, cursor: text.length }
  if (key.backspace) {
    if (cursor === 0) return ed
    return { text: text.slice(0, cursor - 1) + text.slice(cursor), cursor: cursor - 1 }
  }
  // Ink reports the Backspace key as "backspace" and the Delete key (ESC [ 3 ~) as "delete".
  if (key.delete) {
    const end = key.ctrl ? wordEnd(text, cursor) : Math.min(text.length, cursor + 1)
    return { text: text.slice(0, cursor) + text.slice(end), cursor }
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

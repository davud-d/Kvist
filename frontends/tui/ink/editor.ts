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

// `keepNewlines` is for the goal box, which sends multi-line text; other fields are one line.
export function insert(ed: Editor, raw: string, keepNewlines = false): Editor {
  // A lone \r would move the terminal cursor and corrupt the row. A tab is
  // printed at a terminal-defined tab stop while the layout counts it as zero
  // cells, so the frame prints wider than Ink measures and its row-by-row
  // erase then leaves a stale row behind — the same for the other control
  // characters, which have no business in a goal either.
  const flattened = keepNewlines ? raw.replace(/\r\n|\r/g, "\n").replace(/\t/g, " ") : raw.replace(/\r\n|\r|\n|\t/g, " ")
  const chunk = flattened.replace(/[\x00-\x08\x0b-\x1f\x7f]/g, "")
  return { text: ed.text.slice(0, ed.cursor) + chunk + ed.text.slice(ed.cursor), cursor: ed.cursor + chunk.length }
}

// One screen row of the text: offsets into `text`, `end` exclusive and before any newline.
export interface Row {
  start: number
  end: number
}

// Breaks the text into screen rows of at most `cells` display cells: at every
// newline, and inside a logical line wherever the next character would not fit.
export function wrapRows(text: string, cells: number): Row[] {
  const rows: Row[] = []
  let start = 0
  let used = 0
  let at = 0
  for (const char of text) {
    if (char === "\n") {
      rows.push({ start, end: at })
      at += 1
      start = at
      used = 0
      continue
    }
    const width = stringWidth(char)
    if (used > 0 && used + width > cells) {
      rows.push({ start, end: at })
      start = at
      used = 0
    }
    used += width
    at += char.length
  }
  rows.push({ start, end: at })
  return rows
}

// The row holding the cursor; a cursor at a wrap point belongs to the row that starts there.
export function cursorRow(rows: readonly Row[], cursor: number): number {
  let found = 0
  rows.forEach((row, i) => { if (row.start <= cursor) found = i })
  return found
}

// Moves the cursor one screen row up or down, keeping its display column; null on the first or last row.
export function moveRow(ed: Editor, cells: number, delta: -1 | 1): Editor | null {
  const rows = wrapRows(ed.text, cells)
  const current = cursorRow(rows, ed.cursor)
  const target = rows[current + delta]
  if (!target) return null
  const column = stringWidth(ed.text.slice(rows[current]!.start, ed.cursor))
  let cursor = target.start
  while (cursor < target.end && stringWidth(ed.text.slice(target.start, cursor + 1)) <= column) cursor++
  return { text: ed.text, cursor }
}

const isSpace = (char: string | undefined) => char !== undefined && /\s/.test(char)

function wordStart(text: string, cursor: number): number {
  let i = cursor
  while (i > 0 && isSpace(text[i - 1])) i--
  while (i > 0 && !isSpace(text[i - 1])) i--
  return i
}

function wordEnd(text: string, cursor: number): number {
  let i = cursor
  while (i < text.length && isSpace(text[i])) i++
  while (i < text.length && !isSpace(text[i])) i++
  return i
}

export function lineStart(text: string, cursor: number): number {
  return cursor === 0 ? 0 : text.lastIndexOf("\n", cursor - 1) + 1
}

export function lineEnd(text: string, cursor: number): number {
  const at = text.indexOf("\n", cursor)
  return at < 0 ? text.length : at
}

// Returns null when the key is not an editing key (the caller may use it).
export function applyKey(ed: Editor, input: string, key: KeyLike): Editor | null {
  const { text, cursor } = ed
  if (key.leftArrow) return { text, cursor: Math.max(0, cursor - 1) }
  if (key.rightArrow) return { text, cursor: Math.min(text.length, cursor + 1) }
  if (key.home) return { text, cursor: lineStart(text, cursor) }
  if (key.end) return { text, cursor: lineEnd(text, cursor) }
  if (key.backspace) {
    if (cursor === 0) return ed
    if (key.meta || key.ctrl) {
      const start = wordStart(text, cursor)
      return { text: text.slice(0, start) + text.slice(cursor), cursor: start }
    }
    return { text: text.slice(0, cursor - 1) + text.slice(cursor), cursor: cursor - 1 }
  }
  // Ink reports the Backspace key as "backspace" and the Delete key (ESC [ 3 ~) as "delete".
  if (key.delete) {
    const end = key.ctrl ? wordEnd(text, cursor) : Math.min(text.length, cursor + 1)
    return { text: text.slice(0, cursor) + text.slice(end), cursor }
  }
  if (key.ctrl) {
    if (input === "a") return { text, cursor: lineStart(text, cursor) }
    if (input === "e") return { text, cursor: lineEnd(text, cursor) }
    if (input === "u") {
      const start = lineStart(text, cursor)
      return { text: text.slice(0, start) + text.slice(cursor), cursor: start }
    }
    if (input === "k") return { text: text.slice(0, cursor) + text.slice(lineEnd(text, cursor)), cursor }
    if (input === "w") {
      const start = wordStart(text, cursor)
      return { text: text.slice(0, start) + text.slice(cursor), cursor: start }
    }
    return null
  }
  if (key.meta || !input) return null
  return insert(ed, input)
}

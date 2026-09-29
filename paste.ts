// Large pastes stay out of the input line: the line holds a short label and the
// text is put back when the goal is submitted.

export interface PasteStore {
  next: number
  texts: Map<number, string>
}

export const PASTE_MIN_LINES = 4
export const PASTE_MIN_CHARS = 800

const LABEL = /\[Pasted text #(\d+) (?:\+\d+ lines|\d+ chars)\]/g
const LABEL_AT_END = /\[Pasted text #\d+ (?:\+\d+ lines|\d+ chars)\]$/

export function emptyPasteStore(): PasteStore {
  return { next: 1, texts: new Map() }
}

function normalize(text: string): string {
  return text.replace(/\r\n|\r/g, "\n")
}

export function isLargePaste(text: string): boolean {
  const body = normalize(text)
  return body.length >= PASTE_MIN_CHARS || body.split("\n").length >= PASTE_MIN_LINES
}

// Stores `text` and returns the label that stands for it in the input line.
export function collapsePaste(store: PasteStore, text: string): string {
  const body = normalize(text)
  const id = store.next++
  store.texts.set(id, body)
  const extraLines = body.split("\n").length - 1
  return extraLines > 0 ? `[Pasted text #${id} +${extraLines} lines]` : `[Pasted text #${id} ${body.length} chars]`
}

// A label whose text is not in the store (typed by hand, or left from an
// earlier goal) stays as written.
export function expandPastes(store: PasteStore, line: string): string {
  return line.replace(LABEL, (label, id: string) => store.texts.get(Number(id)) ?? label)
}

// Length of the label that ends exactly at `cursor`, so Backspace removes it whole.
export function labelLengthBefore(line: string, cursor: number): number {
  return LABEL_AT_END.exec(line.slice(0, cursor))?.[0].length ?? 0
}

// Pasting the text a label in the line already stands for asks to see it: the
// range of that label, or null when the line has none.
export function findRepeatedLabel(store: PasteStore, line: string, pasted: string): { start: number; end: number } | null {
  const body = normalize(pasted)
  for (const match of line.matchAll(LABEL)) {
    if (store.texts.get(Number(match[1])) === body) return { start: match.index, end: match.index + match[0].length }
  }
  return null
}

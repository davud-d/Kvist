// Pure logic of the modal dialogs (choice / text / confirm), so key handling,
// filtering and the scroll window are testable without a terminal.

import { windowStart } from "./commands.js"
import { applyKey, emptyEditor, insert, type Editor, type KeyLike } from "./editor.js"
import type { InputOption } from "./protocol.js"

export const DIALOG_ROWS = 12

export interface DialogKey extends KeyLike {
  upArrow?: boolean
  downArrow?: boolean
  pageUp?: boolean
  pageDown?: boolean
  return?: boolean
  escape?: boolean
}

// `value: null` is a cancelled dialog.
export type Done = { value: string | null }

// Every word of the query must occur in the label or the description.
export function filterOptions(options: readonly InputOption[], query: string): InputOption[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean)
  if (!words.length) return [...options]
  return options.filter((option) => {
    const haystack = `${option.label} ${option.description ?? ""}`.toLowerCase()
    return words.every((word) => haystack.includes(word))
  })
}

export interface ChoiceModel {
  query: Editor
  selected: number
}

// The current value starts under the cursor.
export function initialChoice(options: readonly InputOption[], current: string): ChoiceModel {
  const at = options.findIndex((option) => option.value === current)
  return { query: emptyEditor, selected: Math.max(0, at) }
}

export function choiceWindow(count: number, selected: number, size: number): { start: number; end: number } {
  const start = windowStart(count, selected, size)
  return { start, end: Math.min(count, start + size) }
}

export function counterLabel(count: number, selected: number): string {
  return count ? `${selected + 1}/${count}` : "0/0"
}

function move(selected: number, count: number, key: DialogKey, size: number): number {
  if (key.upArrow) return (selected - 1 + count) % count
  if (key.downArrow) return (selected + 1) % count
  if (key.pageUp) return Math.max(0, selected - size)
  return Math.min(count - 1, selected + size)
}

// A slow link can deliver "text\r" as one chunk: the text is typed, then Enter.
function splitTrailingEnter(input: string, key: DialogKey): { input: string; key: DialogKey } | null {
  if (key.return || input.length <= 1 || !/[\r\n]$/.test(input)) return null
  return { input: input.replace(/[\r\n]+$/, ""), key }
}

export function stepChoice(
  model: ChoiceModel,
  options: readonly InputOption[],
  size: number,
  input: string,
  key: DialogKey,
): { model: ChoiceModel; done?: Done } {
  if (key.escape) return { model, done: { value: null } }
  const chunk = splitTrailingEnter(input, key)
  if (chunk) {
    const typed = stepChoice(model, options, size, chunk.input, key).model
    return stepChoice(typed, options, size, "", { return: true })
  }
  const shown = filterOptions(options, model.query.text)
  const selected = Math.min(model.selected, Math.max(0, shown.length - 1))
  if (key.return) {
    const picked = shown[selected]
    return picked ? { model, done: { value: picked.value } } : { model }
  }
  if (key.upArrow || key.downArrow || key.pageUp || key.pageDown) {
    return { model: { ...model, selected: shown.length ? move(selected, shown.length, key, size) : 0 } }
  }
  const query = applyKey(model.query, input, key)
  if (!query) return { model }
  // Narrowing or widening the list invalidates the old position.
  return { model: { query, selected: query.text === model.query.text ? selected : 0 } }
}

// Text and secret dialogs: an empty answer stands for the shown default.
export function stepLine(
  editor: Editor,
  fallback: string,
  input: string,
  key: DialogKey,
): { editor: Editor; done?: Done } {
  if (key.escape) return { editor, done: { value: null } }
  const chunk = splitTrailingEnter(input, key)
  if (chunk) {
    const typed = insert(editor, chunk.input)
    return { editor: typed, done: { value: typed.text || fallback } }
  }
  if (key.return) return { editor, done: { value: editor.text || fallback } }
  return { editor: applyKey(editor, input, key) ?? editor }
}

// Only an explicit `y` approves; Enter, Esc and `n` all deny.
export function stepConfirm(input: string, key: DialogKey): boolean | null {
  const letter = input.toLowerCase()
  if (letter === "y") return true
  if (letter === "n" || key.escape || key.return) return false
  return null
}

// A paste is data, never Enter: surrounding whitespace (the newline a copied
// key or name usually carries) is dropped instead of becoming part of it.
export function pasteChoice(model: ChoiceModel, text: string): ChoiceModel {
  return { query: insert(model.query, text.trim()), selected: 0 }
}

export function pasteLine(editor: Editor, text: string): Editor {
  return insert(editor, text.trim())
}

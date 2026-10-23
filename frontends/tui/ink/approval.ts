// Pure logic of the approval screen (dangerous command / file overwrite):
// which choices exist, how keys and mouse reports map onto them. No terminal
// access here, so all of it is testable.

import type { InputRequest } from "./protocol.js"

// Declared here rather than taken from dialog.ts: that would pull the editor
// and its string-width into the OpenCode package, which imports this file but
// has none of Ink's node_modules.
export interface ApprovalKey {
  escape?: boolean
  ctrl?: boolean
  upArrow?: boolean
  downArrow?: boolean
  return?: boolean
}

// Enter and digits typed in the moment the screen appears most likely belong
// to whatever the user was typing, not to the question.
export const ARM_MS = 400
export const DOUBLE_CLICK_MS = 500
// A cursor-position reply older than this belongs to a click nobody is waiting for.
export const CLICK_TTL_MS = 500
// Rows drawn under the option list: a spacer and the hint line.
export const ROWS_AFTER_LIST = 2

export type Decision = "yes" | "remember" | "no"

export interface ApprovalOption {
  decision: Decision
  label: string
}

export type Subject =
  | { kind: "command"; command: string; reason: string }
  | { kind: "overwrite"; path: string; diff: string }

const text = (value: unknown): string => (typeof value === "string" ? value : "")

// Cores that predate the field send none; an unknown kind is treated the same.
export function subjectOf(request: InputRequest): Subject | null {
  const raw = request.subject
  if (!raw || typeof raw !== "object") return null
  if (raw.kind === "command" && text(raw.command)) return { kind: "command", command: text(raw.command), reason: text(raw.reason) }
  if (raw.kind === "overwrite" && text(raw.path)) return { kind: "overwrite", path: text(raw.path), diff: text(raw.diff) }
  return null
}

// "Don't ask again" exists only when the core offered a pattern: dangerous
// commands never carry one, and that protection must not depend on the UI.
export function approvalOptions(request: InputRequest): ApprovalOption[] {
  const pattern = text(request.remember_pattern)
  const yes: ApprovalOption = { decision: "yes", label: "Yes" }
  const no: ApprovalOption = { decision: "no", label: "No" }
  if (subjectOf(request)?.kind === "overwrite" || !pattern) return [yes, no]
  return [yes, { decision: "remember", label: `Yes, and don't ask again for: ${pattern}` }, no]
}

export interface ApprovalModel {
  selected: number
  armedAt: number
}

export function initialApproval(now: number): ApprovalModel {
  return { selected: 0, armedAt: now + ARM_MS }
}

export interface ApprovalStep {
  model: ApprovalModel
  decision?: Decision
}

// Esc and Ctrl+C deny at any moment (the safe answer needs no arming).
export function stepApproval(model: ApprovalModel, options: readonly ApprovalOption[], input: string, key: ApprovalKey, now: number): ApprovalStep {
  const count = options.length
  if (key.escape || (key.ctrl && input === "c")) return { model, decision: "no" }
  if (key.upArrow) return { model: { ...model, selected: (model.selected - 1 + count) % count } }
  if (key.downArrow) return { model: { ...model, selected: (model.selected + 1) % count } }
  const armed = now >= model.armedAt
  if (key.return) return armed ? { model, decision: options[model.selected]!.decision } : { model }
  const digit = /^[1-9]$/.test(input) ? Number(input) - 1 : -1
  if (digit < 0 || digit >= count || !armed) return { model }
  return { model: { ...model, selected: digit }, decision: options[digit]!.decision }
}

export interface MouseReport {
  button: number
  x: number
  y: number
  press: boolean
}

// SGR mouse report: ESC [ < button ; x ; y (M press | m release).
export function parseMouse(data: string): MouseReport | null {
  const match = /^\x1b\[<(\d+);(\d+);(\d+)([Mm])$/.exec(data)
  if (!match) return null
  return { button: Number(match[1]), x: Number(match[2]), y: Number(match[3]), press: match[4] === "M" }
}

// Plain left press only: no modifiers, motion or wheel bits.
export function isLeftPress(report: MouseReport): boolean {
  return report.press && report.button === 0
}

// Reply to the ESC [ 6 n cursor-position query: ESC [ row ; col R.
export function parseCursorReport(data: string): number | null {
  const match = /^\x1b\[(\d+);(\d+)R$/.exec(data)
  return match ? Number(match[1]) : null
}

// Input that reached a key handler as text after Ink stripped the escape.
export function isTerminalReport(input: string): boolean {
  return /^\[<\d+;\d+;\d+[Mm]$/.test(input) || /^\[\d+;\d+R$/.test(input)
}

// The frame is bottom-anchored: the cursor rests on the row under it, so
// the list's last row is fixed relative to the reported cursor row, however
// tall everything above it is.
export function optionAt(clickRow: number, cursorRow: number, rowsBelowPrompt: number, count: number): number | null {
  const lastRow = cursorRow - 1 - rowsBelowPrompt - ROWS_AFTER_LIST
  const index = clickRow - (lastRow - (count - 1))
  return index >= 0 && index < count ? index : null
}

export interface ClickState {
  index: number | null
  at: number
}

// One click puts the cursor on the option, a second one on the same option
// shortly after chooses it.
export function stepClick(state: ClickState, index: number, now: number): { state: ClickState; choose: boolean } {
  if (state.index === index && now - state.at <= DOUBLE_CLICK_MS) return { state: { index: null, at: 0 }, choose: true }
  return { state: { index, at: now }, choose: false }
}

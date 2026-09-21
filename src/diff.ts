// Line diff for the edit cards. Pure and dependency-free: a common prefix and
// suffix are peeled off first, and only the changed middle goes through a
// line LCS, which is skipped when it would cost too much.

export type RowKind = "ctx" | "del" | "add"

export interface DiffRow {
  kind: RowKind
  text: string
  oldNo: number | null
  newNo: number | null
}

export interface SplitRow {
  left: DiffRow | null
  right: DiffRow | null
}

// A 500x500 middle is the most a quadratic table may cover; beyond it the
// whole middle is shown as removed-then-added, which is still correct.
const MAX_LCS_CELLS = 250_000
const DEFAULT_CONTEXT = 3

type Op = { kind: RowKind; text: string }

// "a\nb\n" is two lines and "" is none, so a trailing newline adds no phantom row.
export function toLines(text: string): string[] {
  if (text === "") return []
  const lines = text.replace(/\r\n?/g, "\n").split("\n")
  if (lines.at(-1) === "") lines.pop()
  return lines
}

function commonPrefix(a: string[], b: string[]): number {
  let n = 0
  while (n < a.length && n < b.length && a[n] === b[n]) n++
  return n
}

function commonSuffix(a: string[], b: string[], limit: number): number {
  let n = 0
  while (n < limit && a[a.length - 1 - n] === b[b.length - 1 - n]) n++
  return n
}

function replaceAll(a: string[], b: string[]): Op[] {
  return [...a.map((text): Op => ({ kind: "del", text })), ...b.map((text): Op => ({ kind: "add", text }))]
}

function diffMiddle(a: string[], b: string[]): Op[] {
  if (a.length === 0 || b.length === 0 || a.length * b.length > MAX_LCS_CELLS) return replaceAll(a, b)
  const width = b.length + 1
  // lcs[i * width + j] is the LCS length of a[i:] and b[j:].
  const lcs = new Uint16Array((a.length + 1) * width)
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      lcs[i * width + j] = a[i] === b[j] ? lcs[(i + 1) * width + j + 1]! + 1 : Math.max(lcs[(i + 1) * width + j]!, lcs[i * width + j + 1]!)
    }
  }
  const ops: Op[] = []
  let i = 0
  let j = 0
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) ops.push({ kind: "ctx", text: a[i++]! }), j++
    else if (lcs[(i + 1) * width + j]! >= lcs[i * width + j + 1]!) ops.push({ kind: "del", text: a[i++]! })
    else ops.push({ kind: "add", text: b[j++]! })
  }
  while (i < a.length) ops.push({ kind: "del", text: a[i++]! })
  while (j < b.length) ops.push({ kind: "add", text: b[j++]! })
  return ops
}

// Numbers start at `firstLine` on both sides: everything above the replaced
// block is identical in the old and the new file.
export function diffLines(oldText: string, newText: string, firstLine = 1, context = DEFAULT_CONTEXT): DiffRow[] {
  const a = toLines(oldText)
  const b = toLines(newText)
  const head = commonPrefix(a, b)
  if (head === a.length && head === b.length) return []
  const tail = commonSuffix(a, b, Math.min(a.length, b.length) - head)
  const ops = diffMiddle(a.slice(head, a.length - tail), b.slice(head, b.length - tail))

  const rows: DiffRow[] = []
  let oldNo = firstLine + Math.max(0, head - context)
  let newNo = oldNo
  const both = (text: string) => rows.push({ kind: "ctx", text, oldNo: oldNo++, newNo: newNo++ })
  for (const text of a.slice(Math.max(0, head - context), head)) both(text)
  for (const op of ops) {
    if (op.kind === "ctx") both(op.text)
    else if (op.kind === "del") rows.push({ kind: "del", text: op.text, oldNo: oldNo++, newNo: null })
    else rows.push({ kind: "add", text: op.text, oldNo: null, newNo: newNo++ })
  }
  for (const text of a.slice(a.length - tail, a.length - tail + context)) both(text)
  return rows
}

// Side-by-side: a run of removals sits opposite the run of additions that
// follows it, line for line; the shorter side is left empty.
export function pairRows(rows: DiffRow[]): SplitRow[] {
  const out: SplitRow[] = []
  let i = 0
  while (i < rows.length) {
    const row = rows[i]!
    if (row.kind === "ctx") {
      out.push({ left: row, right: row })
      i++
      continue
    }
    const dels: DiffRow[] = []
    const adds: DiffRow[] = []
    for (; i < rows.length && rows[i]!.kind !== "ctx"; i++) (rows[i]!.kind === "del" ? dels : adds).push(rows[i]!)
    for (let k = 0; k < Math.max(dels.length, adds.length); k++) out.push({ left: dels[k] ?? null, right: adds[k] ?? null })
  }
  return out
}

// Width of the line-number gutter for a set of rows.
export function numberWidth(rows: DiffRow[]): number {
  let max = 1
  for (const row of rows) max = Math.max(max, row.oldNo ?? 0, row.newNo ?? 0)
  return String(max).length
}

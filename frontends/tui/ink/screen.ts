// Text selection over a rendered frame. The frame is an array of ANSI strings,
// one per screen row; every function here works on terminal cells, so a wide
// character counts as two columns and escape sequences count as none.
import stringWidth from "string-width"

const ESCAPE = /\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)/y
const SEGMENTER = new Intl.Segmenter(undefined, { granularity: "grapheme" })

export interface Cell {
  text: string
  // First screen column of the character and how many it covers.
  col: number
  width: number
  // Where the character sits in the ANSI string.
  from: number
  to: number
}

export interface Point {
  row: number
  col: number
}

// Both ends inclusive, in reading order.
export interface Selection {
  start: Point
  end: Point
}

function escapeAt(line: string, index: number): string | null {
  ESCAPE.lastIndex = index
  return ESCAPE.exec(line)?.[0] ?? null
}

// The visible characters of one row. Zero-width graphemes are dropped.
export function cellsOf(line: string): Cell[] {
  const cells: Cell[] = []
  let col = 0
  let index = 0
  while (index < line.length) {
    const escape = escapeAt(line, index)
    if (escape) {
      index += escape.length
      continue
    }
    let end = index
    while (end < line.length && !escapeAt(line, end)) end++
    for (const { segment, index: at } of SEGMENTER.segment(line.slice(index, end))) {
      const width = stringWidth(segment)
      if (width > 0) cells.push({ text: segment, col, width, from: index + at, to: index + at + segment.length })
      col += width
    }
    index = end
  }
  return cells
}

export function cellAt(cells: readonly Cell[], col: number): Cell | undefined {
  return cells.find((cell) => col >= cell.col && col < cell.col + cell.width)
}

export function order(a: Point, b: Point): Selection {
  return a.row < b.row || (a.row === b.row && a.col <= b.col) ? { start: a, end: b } : { start: b, end: a }
}

// The part of row `row` a selection covers, as inclusive columns, or null.
function span(selection: Selection, row: number, cells: readonly Cell[]): [number, number] | null {
  const { start, end } = selection
  if (row < start.row || row > end.row) return null
  const last = cells.length ? (cells.at(-1)!.col + cells.at(-1)!.width - 1) : 0
  return [row === start.row ? start.col : 0, row === end.row ? end.col : last]
}

export function selectedText(lines: readonly string[], selection: Selection): string {
  const out: string[] = []
  for (let row = selection.start.row; row <= selection.end.row && row < lines.length; row++) {
    const cells = cellsOf(lines[row] ?? "")
    const range = span(selection, row, cells)
    if (!range) continue
    out.push(cells.filter((cell) => cell.col + cell.width > range[0] && cell.col <= range[1]).map((cell) => cell.text).join("").trimEnd())
  }
  return out.join("\n")
}

const INVERSE_ON = "\x1b[7m"
const INVERSE_OFF = "\x1b[27m"

// The row with the cells of `range` in inverse video. A colour or reset inside
// the range would switch inverse off again, so it is re-armed after each one.
export function highlightLine(line: string, range: [number, number]): string {
  const cells = cellsOf(line).filter((cell) => cell.col + cell.width > range[0] && cell.col <= range[1])
  const first = cells[0]
  const last = cells.at(-1)
  if (!first || !last) return line
  const inside = line.slice(first.from, last.to).replace(/\x1b\[[0-9;]*m/g, (sgr) => sgr + INVERSE_ON)
  return line.slice(0, first.from) + INVERSE_ON + inside + INVERSE_OFF + line.slice(last.to)
}

export function highlight(lines: readonly string[], selection: Selection): string[] {
  return lines.map((line, row) => {
    const range = span(selection, row, cellsOf(line))
    return range ? highlightLine(line, range) : line
  })
}

// Word boundaries as iTerm2 draws them: a path or URL stays in one piece.
const SEPARATOR = /[\s()[\]{}<>"'`,;]/

// The run of same-kind characters around `col`.
export function wordAt(line: string, col: number): [number, number] | null {
  const cells = cellsOf(line)
  const index = cells.findIndex((cell) => col >= cell.col && col < cell.col + cell.width)
  if (index < 0) return null
  const kind = SEPARATOR.test(cells[index]!.text)
  let from = index
  let to = index
  while (from > 0 && SEPARATOR.test(cells[from - 1]!.text) === kind) from--
  while (to < cells.length - 1 && SEPARATOR.test(cells[to + 1]!.text) === kind) to++
  return [cells[from]!.col, cells[to]!.col + cells[to]!.width - 1]
}

// Every character of the row, without the padding after the last one.
export function lineAt(line: string): [number, number] | null {
  const last = cellsOf(line).findLast((cell) => cell.text.trim() !== "")
  return last ? [0, last.col + last.width - 1] : null
}

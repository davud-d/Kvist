// A grid of terminal cells, each a character with colours, written out as ANSI rows.
import { colorCode, pack, unpack, type ColorLevel, type RGB } from "./color.js"

export const NO_COLOR = -1

export const BOLD = 1
export const DIM = 2
export const INVERSE = 4
export const UNDERLINE = 8

export interface Style {
  fg?: RGB
  bg?: RGB
  attrs?: number
}

export class Canvas {
  readonly chars: string[]
  readonly fg: Int32Array
  readonly bg: Int32Array
  readonly attrs: Uint8Array

  constructor(readonly cols: number, readonly rows: number) {
    this.chars = new Array<string>(cols * rows).fill(" ")
    this.fg = new Int32Array(cols * rows).fill(NO_COLOR)
    this.bg = new Int32Array(cols * rows).fill(NO_COLOR)
    this.attrs = new Uint8Array(cols * rows)
  }

  inside(col: number, row: number): boolean {
    return col >= 0 && col < this.cols && row >= 0 && row < this.rows
  }

  set(col: number, row: number, char: string, style: Style): void {
    if (!this.inside(col, row)) return
    const at = row * this.cols + col
    this.chars[at] = char
    this.fg[at] = style.fg ? pack(style.fg) : NO_COLOR
    this.bg[at] = style.bg ? pack(style.bg) : NO_COLOR
    this.attrs[at] = style.attrs ?? 0
  }

  // Writes `text` from (col, row). A style without a background keeps the one already there,
  // so text can sit on the picture or on the panel without restating its colour.
  text(col: number, row: number, text: string, style: Style): number {
    let at = col
    for (const char of text) {
      if (this.inside(at, row)) {
        const index = row * this.cols + at
        this.chars[index] = char
        this.fg[index] = style.fg ? pack(style.fg) : NO_COLOR
        if (style.bg) this.bg[index] = pack(style.bg)
        this.attrs[index] = style.attrs ?? 0
      }
      at++
    }
    return at - col
  }

  fill(col: number, row: number, width: number, height: number, style: Style, char = " "): void {
    for (let y = row; y < row + height; y++) for (let x = col; x < col + width; x++) this.set(x, y, char, style)
  }

  backgroundAt(col: number, row: number): RGB | null {
    const value = this.bg[row * this.cols + col]!
    return value === NO_COLOR ? null : unpack(value)
  }

  // One ANSI string per row. A row ends with a reset, never with an erase: the last column is
  // written like any other, so the picture reaches the edge.
  toRows(level: ColorLevel): string[] {
    const rows: string[] = []
    for (let row = 0; row < this.rows; row++) {
      let out = ""
      let fg = NO_COLOR
      let bg = NO_COLOR
      let attrs = 0
      for (let col = 0; col < this.cols; col++) {
        const at = row * this.cols + col
        const wantFg = level === "none" ? NO_COLOR : this.fg[at]!
        const wantBg = level === "none" ? NO_COLOR : this.bg[at]!
        const wantAttrs = this.attrs[at]!
        const codes: string[] = []
        if (wantAttrs !== attrs) {
          // Attributes only turn off through a full reset, so colours are restated after one.
          codes.push("0")
          if (wantAttrs & BOLD) codes.push("1")
          if (wantAttrs & DIM) codes.push("2")
          if (wantAttrs & UNDERLINE) codes.push("4")
          if (wantAttrs & INVERSE) codes.push("7")
          attrs = wantAttrs
          fg = NO_COLOR
          bg = NO_COLOR
        }
        if (wantFg !== fg) {
          codes.push(wantFg === NO_COLOR ? "39" : colorCode(level, unpack(wantFg), false))
          fg = wantFg
        }
        if (wantBg !== bg) {
          codes.push(wantBg === NO_COLOR ? "49" : colorCode(level, unpack(wantBg), true))
          bg = wantBg
        }
        if (codes.length) out += `\x1b[${codes.join(";")}m`
        out += this.chars[at]
      }
      rows.push(out + "\x1b[0m")
    }
    return rows
  }
}

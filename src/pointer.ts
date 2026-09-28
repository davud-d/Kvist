// Mouse handling for the fullscreen (alternate screen) interface, the way
// Claude Code does it: the app owns the mouse, so the terminal can no longer
// select text itself. The wheel scrolls the transcript, and a drag, double-click
// or triple-click selects cells of the rendered frame, which is copied to the
// clipboard on release. Shift+drag still reaches the terminal's own selection.
//
// Ink offers no hook for either side, so the pointer stands between Ink and
// the terminal: it strips mouse reports out of the input before Ink reads it,
// and it sees each frame Ink writes so it can hit-test it and paint the selection.
// It also paints the frame itself: Ink erases the whole screen and rewrites it on every
// update, which shows as flicker at the pace a spinner and a stream update it, so only
// the rows that changed are written, in place.
import { copyToClipboard, type ClipboardResult } from "./clipboard.js"
import { deleteImage, placeImage } from "./kitty.js"
import { disableMouse, enableMouse } from "./mouse.js"
import { decodePalettePng, pngHeight, type Indexed } from "./png.js"
import { encodeSixel } from "./sixel.js"
import { cellsOf, cellAt, highlight, lineAt, order, selectedText, wordAt, type Point, type Selection } from "./screen.js"

const REPORT = /\x1b\[<(\d+);(\d+);(\d+)([Mm])/g
// Ink's eraseLines(previous frame height) in front of the new frame.
const ERASE = /^(?:\x1b\[2K\x1b\[1A)*\x1b\[2K\x1b\[G/
// Clearing the terminal: what is painted is no longer what was last written.
const CLEARS = /\x1b\[2J|\x1b\[3J|\x1bc/
const MULTI_CLICK_MS = 500
const WHEEL_LINES = 3
const PAGE_KEYS = /^\x1b\[[56]~$/

export interface MouseReport {
  button: number
  // 0-based screen cell.
  col: number
  row: number
  press: boolean
}

export function extractMouse(data: string): { reports: MouseReport[]; rest: string } {
  const reports = [...data.matchAll(REPORT)].map((match) => ({
    button: Number(match[1]),
    col: Number(match[2]) - 1,
    row: Number(match[3]) - 1,
    press: match[4] === "M",
  }))
  return { reports, rest: data.replace(REPORT, "") }
}

export interface Frame {
  prefix: string
  lines: string[]
}

// Ink writes a frame of exactly `rows` lines behind an erase prefix; anything
// else it writes (cursor and sync sequences, other output) is not a frame.
export function splitFrame(chunk: string, rows: number): Frame | null {
  const prefix = ERASE.exec(chunk)?.[0] ?? ""
  const lines = chunk.slice(prefix.length).split("\n")
  return lines.length === rows ? { prefix, lines } : null
}

type Wheel = (lines: number) => void
type Copied = (result: ClipboardResult) => void

export interface PointerOptions {
  copy?: (text: string) => ClipboardResult
  now?: () => number
  // How the terminal draws pictures, if it does: see showImage.
  pictures?: "kitty" | "sixel" | null
  // Size of a cell in pixels, which Sixel needs to make a picture fit its cells.
  cell?: { width: number; height: number } | null
}

// A cell of this size is assumed when the terminal does not say. A picture made for it is never
// larger than the cells it is meant for, whichever terminal font is in use, so it cannot spill over text.
const ASSUMED_CELL = { width: 8, height: 16 }

// A picture to keep over a rectangle of cells. `key` names what is shown (the frame), so an
// unchanged picture at an unchanged place is not sent again.
export interface ShownImage {
  png: string
  key: string
  row: number
  col: number
  columns: number
  rows: number
  // Rows of the picture that are out of view at the top and at the bottom; only the rest is drawn.
  skipTop: number
  skipBottom: number
}

const IMAGE_ID = 1

// A view of `target` that answers some properties itself and hands the rest,
// bound to the real stream, through.
function view<T extends object>(target: T, own: Record<string, unknown>): T {
  return new Proxy(target, {
    get(real, name) {
      if (typeof name === "string" && name in own) return own[name]
      const value = Reflect.get(real, name, real)
      return typeof value === "function" ? value.bind(real) : value
    },
    set: (real, name, value) => Reflect.set(real, name, value, real),
  })
}

export class Pointer {
  readonly stdin: NodeJS.ReadStream
  readonly stdout: NodeJS.WriteStream
  private frame: Frame | null = null
  // The rows as they stand on the terminal (highlight included), or null when unknown.
  private painted: string[] | null = null
  private paintedColumns = 0
  private selection: Selection | null = null
  private anchor: Point | null = null
  private dragging = false
  private moved = false
  private clicks = 0
  private lastPress: { at: number; row: number; col: number } | null = null
  private image: ShownImage | null = null
  // What is on the terminal now, as "key@row,col,columns,rows", or null for nothing.
  private imageDrawn: string | null = null
  readonly pictures: "kitty" | "sixel" | null
  readonly images: boolean
  private readonly cell: { width: number; height: number }
  // Sixel only: the rows a picture covers, and its decoded frames and encoded strings.
  private sixelRows: { row: number; rows: number } | null = null
  private readonly decoded = new Map<string, Indexed>()
  private readonly encoded = new Map<string, string>()
  private readonly wheelListeners = new Set<Wheel>()
  private readonly copiedListeners = new Set<Copied>()
  private readonly copy: (text: string) => ClipboardResult
  private readonly now: () => number

  constructor(private readonly input: NodeJS.ReadStream, private readonly output: NodeJS.WriteStream, options: PointerOptions = {}) {
    this.copy = options.copy ?? copyToClipboard
    this.now = options.now ?? Date.now
    this.pictures = options.pictures ?? null
    this.images = this.pictures !== null
    this.cell = options.cell ?? ASSUMED_CELL
    this.stdin = view(input, { read: (size?: number) => this.read(size) })
    this.stdout = view(output, { write: (chunk: unknown, ...rest: unknown[]) => this.write(chunk, rest) })
  }

  start(): void {
    enableMouse(this.output, true)
  }

  stop(): void {
    disableMouse()
    this.showImage(null)
  }

  // Keeps `image` over its cells until told otherwise. A kitty picture belongs to the terminal's
  // graphics layer, above the text, so the cells under it must be left blank; it stays where it was
  // put when the text around it changes, so the caller moves or removes it with the layout. A Sixel
  // picture is part of the cells, and text written over them erases it.
  showImage(image: ShownImage | null): void {
    if (!this.images) return
    this.image = image
    // Nothing is on screen before the first frame; the frame will bring the picture.
    if (this.painted) this.drawImage()
  }

  private drawImage(): void {
    const wanted = this.image ? `${this.image.key}@${this.image.row},${this.image.col},${this.image.columns},${this.image.rows},${this.image.skipTop},${this.image.skipBottom}` : null
    if (wanted === this.imageDrawn) return
    this.imageDrawn = wanted
    if (this.pictures === "sixel") return this.drawSixel()
    if (!this.image) {
      this.output.write(deleteImage(IMAGE_ID))
      return
    }
    const { png, row, col, columns, rows, skipTop, skipBottom } = this.image
    const visible = rows - skipTop - skipBottom
    const size = visible < rows ? pngHeight(png) : 0
    const source = visible < rows ? { y: Math.round((skipTop / rows) * size), height: Math.round((visible / rows) * size) } : undefined
    this.output.write(placeImage(png, { id: IMAGE_ID, row: row + skipTop, col, columns, rows: visible, source }))
  }

  private sixelOf(image: ShownImage): string {
    const width = image.columns * this.cell.width
    const height = image.rows * this.cell.height
    const from = image.skipTop * this.cell.height
    const to = (image.rows - image.skipBottom) * this.cell.height
    const key = `${image.key}@${width}x${height}:${from}-${to}`
    let text = this.encoded.get(key)
    if (text === undefined) {
      let decoded = this.decoded.get(image.key)
      if (!decoded) {
        decoded = decodePalettePng(image.png)
        this.decoded.set(image.key, decoded)
      }
      text = encodeSixel(decoded, width, height, from, to)
      this.encoded.set(key, text)
    }
    return text
  }

  private drawSixel(): void {
    const before = this.sixelRows
    const shown = this.image ? { row: this.image.row + this.image.skipTop, rows: this.image.rows - this.image.skipTop - this.image.skipBottom } : null
    this.sixelRows = shown
    // A picture that moved, shrank or went away leaves its old cells behind: writing text over them is the only eraser.
    if (before && (!shown || before.row !== shown.row || before.rows !== shown.rows)) {
      const painted = this.painted
      for (let row = before.row; painted && row < Math.min(before.row + before.rows, painted.length); row++) painted[row] = "\0"
      this.paint(false)
    }
    if (this.image && shown) this.output.write(`\x1b[${shown.row + 1};${this.image.col + 1}H${this.sixelOf(this.image)}`)
  }

  // Scroll requests from the wheel: negative moves toward older content.
  onWheel(listener: Wheel): () => void {
    this.wheelListeners.add(listener)
    return () => this.wheelListeners.delete(listener)
  }

  onCopied(listener: Copied): () => void {
    this.copiedListeners.add(listener)
    return () => this.copiedListeners.delete(listener)
  }

  // Ink reads its input through this: mouse reports are taken out, keys pass.
  private read(size?: number): string | Buffer | null {
    for (;;) {
      const chunk = this.input.read(size) as string | Buffer | null
      if (chunk === null) return null
      const { reports, rest } = extractMouse(chunk.toString())
      for (const report of reports) this.handle(report)
      if (rest === "") continue
      if (this.selection && !PAGE_KEYS.test(rest)) this.select(null)
      return rest
    }
  }

  private write(chunk: unknown, rest: unknown[]): boolean {
    if (typeof chunk === "string" && this.output.rows) {
      const frame = splitFrame(chunk, this.output.rows)
      if (frame) {
        this.frame = frame
        this.paint()
        return true
      }
      if (CLEARS.test(chunk)) {
        this.painted = null
        this.imageDrawn = null
        this.sixelRows = null
      }
    }
    return (this.output.write as (...args: unknown[]) => boolean)(chunk, ...rest)
  }

  // Writes the rows of the current frame that differ from what the terminal shows, each at its
  // own position and closed with a reset and an erase-to-end-of-line. A first frame, a resize or
  // a cleared screen writes every row.
  private paint(withImage = true): void {
    if (!this.frame) return
    const lines = this.selection ? highlight(this.frame.lines, this.selection) : this.frame.lines
    const columns = this.output.columns ?? 0
    const everything = !this.painted || this.painted.length !== lines.length || columns !== this.paintedColumns
    let out = everything && this.painted ? "\x1b[2J" : ""
    lines.forEach((line, row) => {
      if (everything || this.painted?.[row] !== line) out += `\x1b[${row + 1};1H${line}\x1b[0m\x1b[K`
    })
    // A copy: a Sixel eraser marks rows of it for repainting, and the frame's own rows must stay as they are.
    this.painted = [...lines]
    this.paintedColumns = columns
    if (out) this.output.write(out)
    // Clearing the screen took the picture with it.
    if (everything) {
      this.imageDrawn = null
      this.sixelRows = null
    }
    if (withImage) this.drawImage()
  }

  private select(selection: Selection | null): void {
    this.selection = selection
    this.paint()
  }

  private handle(report: MouseReport): void {
    if (report.button & 64) {
      for (const listener of this.wheelListeners) listener((report.button & 1 ? 1 : -1) * WHEEL_LINES)
      return
    }
    if ((report.button & 3) !== 0) return
    const at: Point = { row: report.row, col: report.col }
    if (report.button & 32) return this.drag(at)
    if (report.press) return this.press(at)
    this.release(at)
  }

  private press(at: Point): void {
    const now = this.now()
    const again = this.lastPress && now - this.lastPress.at < MULTI_CLICK_MS && this.lastPress.row === at.row && this.lastPress.col === at.col
    this.clicks = again ? Math.min(3, this.clicks + 1) : 1
    this.lastPress = { at: now, ...at }
    this.anchor = at
    this.dragging = true
    this.moved = false
    const line = this.frame?.lines[at.row]
    const range = line === undefined || this.clicks === 1 ? null : this.clicks === 2 ? wordAt(line, at.col) : lineAt(line)
    this.select(range ? { start: { row: at.row, col: range[0] }, end: { row: at.row, col: range[1] } } : null)
  }

  private drag(at: Point): void {
    if (!this.dragging || !this.anchor || this.clicks > 1) return
    const line = this.frame?.lines[at.row]
    // A cell past the end of the text still selects up to the last character.
    const cells = line === undefined ? [] : cellsOf(line)
    const head = cellAt(cells, at.col) ? at : { row: at.row, col: Math.max(0, (cells.at(-1)?.col ?? 0) + (cells.at(-1)?.width ?? 1) - 1) }
    this.moved = true
    this.select(order(this.anchor, head))
  }

  private release(at: Point): void {
    if (!this.dragging) return
    // A quick drag may report no motion before the button comes up.
    if (this.anchor && (at.row !== this.anchor.row || at.col !== this.anchor.col)) this.drag(at)
    this.dragging = false
    if (!this.selection || !this.frame || (!this.moved && this.clicks === 1)) return
    const text = selectedText(this.frame.lines, this.selection)
    if (!text) return
    const result = this.copy(text)
    for (const listener of this.copiedListeners) listener(result)
  }
}

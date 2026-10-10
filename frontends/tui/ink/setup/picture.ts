// The key art as real pixels for a terminal that shows images (kitty graphics). Everything that
// dims or darkens the picture is done here, in the pixels: brightness, the darker middle, and the
// pane the panel sits on. The cells above it then carry only text.
import { sampleFramed } from "./backdrop.js"
import { PANEL_COLOR } from "./scene.js"

// Below INT32_MIN / 2, so cells with a colour of their own (the highlighted row, buttons) are
// drawn over the picture and not under it.
export const PICTURE_Z = -1_500_000_000
// The picture is not worth more pixels than its source has.
export const MAX_PICTURE_WIDTH = 1280

export interface PictureSize {
  width: number
  height: number
}

// Pixel size for a window of `cols` x `rows` cells, from the cell size the terminal reported (or
// an assumed one), within what the source can fill; `quality` below 1 makes a smaller picture
// the terminal scales up, for frames in motion.
export function pictureSize(cols: number, rows: number, cell: { width: number; height: number } | null, quality = 1): PictureSize {
  const cellWidth = cell?.width ?? 10
  const cellHeight = cell?.height ?? 20
  const scale = Math.min(1, MAX_PICTURE_WIDTH / (cols * cellWidth)) * quality
  return { width: Math.max(1, Math.round(cols * cellWidth * scale)), height: Math.max(1, Math.round(rows * cellHeight * scale)) }
}

export interface PanelPane {
  // The panel's rectangle in cells.
  col: number
  row: number
  width: number
  height: number
  // 0 leaves the picture alone, 1 is the finished pane.
  opacity: number
  // A large pane lets more of the picture through than a dialog does.
  glass: boolean
}

const smooth = (t: number) => t * t * (3 - 2 * t)

let vignette: { key: string; map: Float32Array } | null = null

// Per pixel factor: darker toward the middle, lighter at the edges, as the cell painter does.
function vignetteMap(width: number, height: number): Float32Array {
  const key = `${width}x${height}`
  if (vignette?.key === key) return vignette.map
  const map = new Float32Array(width * height)
  const cx = (width - 1) / 2
  const cy = (height - 1) / 2
  for (let y = 0; y < height; y++) {
    const dy = (y - cy) / cy
    for (let x = 0; x < width; x++) {
      const radius = Math.min(1, Math.hypot((x - cx) / cx, dy))
      map[y * width + x] = 0.72 + 0.28 * smooth(radius)
    }
  }
  vignette = { key, map }
  return map
}

export function renderPicture(size: PictureSize, cols: number, rows: number, brightness: number, pane: PanelPane | null): Uint8Array {
  const source = sampleFramed(size.width, size.height).rgb
  const factors = vignetteMap(size.width, size.height)
  const out = new Uint8Array(source.length)
  for (let at = 0; at < factors.length; at++) {
    const factor = factors[at]! * brightness
    out[at * 3] = Math.min(255, source[at * 3]! * factor)
    out[at * 3 + 1] = Math.min(255, source[at * 3 + 1]! * factor)
    out[at * 3 + 2] = Math.min(255, source[at * 3 + 2]! * factor)
  }
  if (!pane || pane.opacity <= 0) return out
  const x0 = Math.round((pane.col * size.width) / cols)
  const x1 = Math.round(((pane.col + pane.width) * size.width) / cols)
  const y0 = Math.round((pane.row * size.height) / rows)
  const y1 = Math.round(((pane.row + pane.height) * size.height) / rows)
  // The pane's edge is soft: it fades out over a few pixels outside the panel's border.
  const soft = Math.max(2, Math.round(size.height / 70))
  for (let y = Math.max(0, y0 - soft); y < Math.min(size.height, y1 + soft); y++) {
    for (let x = Math.max(0, x0 - soft); x < Math.min(size.width, x1 + soft); x++) {
      const outside = Math.max(x0 - x, x - (x1 - 1), y0 - y, y - (y1 - 1), 0)
      const weight = pane.opacity * (1 - outside / (soft + 1))
      const at = (y * size.width + x) * 3
      for (let c = 0; c < 3; c++) {
        const under = out[at + c]!
        // Pressed dark: a share of the picture's value, the rest the panel's colour.
        const finished = pane.glass ? under * 0.5 * 0.5 + PANEL_COLOR[c]! * 0.5 : under * 0.5 * 0.14 + PANEL_COLOR[c]! * 0.86
        out[at + c] = Math.round(under + (finished - under) * weight)
      }
    }
  }
  return out
}

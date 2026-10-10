// The key art as pixel grids sized for a window, in cells or in real pixels.
import { decodePalettePng } from "../png.js"
import { BACKDROP_PNG } from "./backdrop-data.js"

export interface PixelGrid {
  width: number
  height: number
  // r, g, b per pixel, row by row.
  rgb: Uint8Array
}

let art: PixelGrid | null = null
const cache = new Map<string, PixelGrid>()

export function artwork(): PixelGrid {
  if (art) return art
  const indexed = decodePalettePng(BACKDROP_PNG)
  const rgb = new Uint8Array(indexed.width * indexed.height * 3)
  indexed.pixels.forEach((index, at) => {
    const color = indexed.palette[index]!
    rgb[at * 3] = color[0]
    rgb[at * 3 + 1] = color[1]
    rgb[at * 3 + 2] = color[2]
  })
  art = { width: indexed.width, height: indexed.height, rgb }
  return art
}

// Where the picture's interest sits vertically: the squirrel and the wordmark are a little
// below the middle, so a window wider than the picture crops the sky and the water first.
export const ANCHOR_Y = 0.52

// Fills `width` x `height` pixels with the picture, scaled to cover them and cropped around the
// anchor. Each pixel is the average of the source area it covers, or the nearest source pixel
// when the grid is larger than the picture. The pixels are taken to be square.
export function sampleBackdrop(width: number, height: number, anchorY = ANCHOR_Y): PixelGrid {
  const source = artwork()
  const scaleFactor = Math.max(width / source.width, height / source.height)
  const viewHeight = height / scaleFactor
  const left = (source.width - width / scaleFactor) / 2
  const top = Math.max(0, (source.height - viewHeight) * anchorY)
  return resample(width, height, left, top, 1 / scaleFactor, `cover@${anchorY}`)
}

function resample(width: number, height: number, left: number, top: number, step: number, mode: string): PixelGrid {
  const key = `${mode} ${width}x${height}`
  const hit = cache.get(key)
  if (hit) return hit
  const source = artwork()
  const rgb = new Uint8Array(width * height * 3)
  for (let y = 0; y < height; y++) {
    const sy0 = top + y * step
    const y0 = Math.min(source.height - 1, Math.floor(sy0))
    const y1 = Math.min(source.height, Math.max(y0 + 1, Math.ceil(sy0 + step)))
    for (let x = 0; x < width; x++) {
      const sx0 = left + x * step
      const x0 = Math.min(source.width - 1, Math.floor(sx0))
      const x1 = Math.min(source.width, Math.max(x0 + 1, Math.ceil(sx0 + step)))
      let r = 0
      let g = 0
      let b = 0
      for (let sy = y0; sy < y1; sy++) {
        for (let sx = x0; sx < x1; sx++) {
          const at = (sy * source.width + sx) * 3
          r += source.rgb[at]!
          g += source.rgb[at + 1]!
          b += source.rgb[at + 2]!
        }
      }
      const count = (y1 - y0) * (x1 - x0)
      const out = (y * width + x) * 3
      rgb[out] = Math.round(r / count)
      rgb[out + 1] = Math.round(g / count)
      rgb[out + 2] = Math.round(b / count)
    }
  }
  const grid = { width, height, rgb }
  if (cache.size > 4) cache.clear()
  cache.set(key, grid)
  return grid
}

// The share of the window the whole picture takes in `sampleFramed`.
export const FRAME_FILL = 0.88
// How bright the blurred surround is next to the picture.
const SURROUND = 0.42
// The surround is built at a twelfth of the size and scaled up, which is the blur.
const BLUR_DIVISOR = 12

// The whole picture, uncropped and a little smaller than the window, in a surround that is the
// same picture enlarged to cover the window, blurred and darkened. The picture's edge fades into
// it over a few pixels, so the eye takes the surround for depth and not for a frame.
export function sampleFramed(width: number, height: number): PixelGrid {
  const key = `framed ${width}x${height}`
  const hit = cache.get(key)
  if (hit) return hit
  const source = artwork()
  const smallWidth = Math.max(2, Math.round(width / BLUR_DIVISOR))
  const smallHeight = Math.max(2, Math.round(height / BLUR_DIVISOR))
  const small = sampleBackdrop(smallWidth, smallHeight).rgb
  const rgb = new Uint8Array(width * height * 3)
  for (let y = 0; y < height; y++) {
    const fy = Math.min(smallHeight - 1, Math.max(0, ((y + 0.5) * smallHeight) / height - 0.5))
    const y0 = Math.floor(fy)
    const y1 = Math.min(smallHeight - 1, y0 + 1)
    const wy = fy - y0
    for (let x = 0; x < width; x++) {
      const fx = Math.min(smallWidth - 1, Math.max(0, ((x + 0.5) * smallWidth) / width - 0.5))
      const x0 = Math.floor(fx)
      const x1 = Math.min(smallWidth - 1, x0 + 1)
      const wx = fx - x0
      for (let c = 0; c < 3; c++) {
        const top = small[(y0 * smallWidth + x0) * 3 + c]! * (1 - wx) + small[(y0 * smallWidth + x1) * 3 + c]! * wx
        const bottom = small[(y1 * smallWidth + x0) * 3 + c]! * (1 - wx) + small[(y1 * smallWidth + x1) * 3 + c]! * wx
        rgb[(y * width + x) * 3 + c] = Math.round((top * (1 - wy) + bottom * wy) * SURROUND)
      }
    }
  }
  const fit = Math.min((width * FRAME_FILL) / source.width, (height * FRAME_FILL) / source.height)
  const innerWidth = Math.max(1, Math.round(source.width * fit))
  const innerHeight = Math.max(1, Math.round(source.height * fit))
  const inner = resample(innerWidth, innerHeight, 0, 0, 1 / fit, "whole")
  const left = Math.floor((width - innerWidth) / 2)
  const top = Math.floor((height - innerHeight) / 2)
  const feather = Math.max(2, Math.round(Math.min(width, height) * 0.012))
  for (let y = 0; y < innerHeight; y++) {
    for (let x = 0; x < innerWidth; x++) {
      const edge = Math.min(x, y, innerWidth - 1 - x, innerHeight - 1 - y)
      const keep = edge >= feather ? 1 : (edge + 0.5) / feather
      const from = (y * innerWidth + x) * 3
      const to = ((top + y) * width + left + x) * 3
      for (let c = 0; c < 3; c++) rgb[to + c] = Math.round(inner.rgb[from + c]! * keep + rgb[to + c]! * (1 - keep))
    }
  }
  const grid = { width, height, rgb }
  cache.set(key, grid)
  return grid
}

// Quadrant glyphs by which of the cell's four pixels (top-left 1, top-right 2, bottom-left 4,
// bottom-right 8) are painted in the foreground colour.
const QUADRANTS = [" ", "▘", "▝", "▀", "▖", "▌", "▞", "▛", "▗", "▚", "▐", "▜", "▄", "▙", "▟", "█"]

export interface CellArt {
  cols: number
  rows: number
  chars: string[]
  // r, g, b per cell, foreground and background.
  fg: Uint8Array
  bg: Uint8Array
}

let cellCache: CellArt | null = null

const luma = (r: number, g: number, b: number) => 0.299 * r + 0.587 * g + 0.114 * b

// The picture as terminal cells, four pixels each: the cell holds two colours at most, so its
// pixels are split in two groups where the sum of squared differences from the group averages
// is smallest, and the glyph is the quadrant shape of the group painted in the foreground colour.
// Twice the horizontal detail of half blocks, in every font that has the block characters.
export function sampleCells(cols: number, rows: number): CellArt {
  if (cellCache && cellCache.cols === cols && cellCache.rows === rows) return cellCache
  // A cell is twice as tall as wide, so a 2x2 block of quadrant pixels is 2x4 square ones, and
  // the vertical pairs are averaged.
  const grid = sampleFramed(cols * 2, rows * 4)
  const chars = new Array<string>(cols * rows)
  const fg = new Uint8Array(cols * rows * 3)
  const bg = new Uint8Array(cols * rows * 3)
  const pixel = new Float64Array(12)
  for (let row = 0; row < rows; row++) {
    for (let col = 0; col < cols; col++) {
      for (let q = 0; q < 4; q++) {
        const x = col * 2 + (q & 1)
        const y = row * 4 + (q >> 1) * 2
        for (let c = 0; c < 3; c++) pixel[q * 3 + c] = (grid.rgb[(y * grid.width + x) * 3 + c]! + grid.rgb[((y + 1) * grid.width + x) * 3 + c]!) / 2
      }
      const order = [0, 1, 2, 3].sort((a, b) => luma(pixel[a * 3]!, pixel[a * 3 + 1]!, pixel[a * 3 + 2]!) - luma(pixel[b * 3]!, pixel[b * 3 + 1]!, pixel[b * 3 + 2]!))
      let best = Infinity
      let bestMask = 0
      let bestLow: number[] = [0, 0, 0]
      let bestHigh: number[] = [0, 0, 0]
      for (let split = 1; split < 4; split++) {
        const low = order.slice(0, split)
        const high = order.slice(split)
        const mean = (group: number[]) => [0, 1, 2].map((c) => group.reduce((sum, q) => sum + pixel[q * 3 + c]!, 0) / group.length)
        const lowMean = mean(low)
        const highMean = mean(high)
        let error = 0
        for (const q of low) for (let c = 0; c < 3; c++) error += (pixel[q * 3 + c]! - lowMean[c]!) ** 2
        for (const q of high) for (let c = 0; c < 3; c++) error += (pixel[q * 3 + c]! - highMean[c]!) ** 2
        if (error < best) {
          best = error
          bestMask = high.reduce((mask, q) => mask | (1 << q), 0)
          bestLow = lowMean
          bestHigh = highMean
        }
      }
      const at = row * cols + col
      chars[at] = QUADRANTS[bestMask]!
      for (let c = 0; c < 3; c++) {
        fg[at * 3 + c] = Math.round(bestHigh[c]!)
        bg[at * 3 + c] = Math.round(bestLow[c]!)
      }
    }
  }
  cellCache = { cols, rows, chars, fg, bg }
  return cellCache
}

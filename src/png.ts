// Reads the small palette PNGs that make_mascot_frames.py writes (8 bits per pixel, a palette,
// transparency by palette entry, no interlacing), which is all the Sixel path needs. Anything else
// is refused rather than guessed at.
import { inflateSync } from "node:zlib"

export interface Indexed {
  width: number
  height: number
  palette: readonly (readonly [number, number, number])[]
  // Opacity of each palette entry, 0-255.
  alpha: readonly number[]
  // One palette index per pixel, row by row.
  pixels: Uint8Array
}

const SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])

function paeth(left: number, up: number, upLeft: number): number {
  const guess = left + up - upLeft
  const toLeft = Math.abs(guess - left)
  const toUp = Math.abs(guess - up)
  const toUpLeft = Math.abs(guess - upLeft)
  if (toLeft <= toUp && toLeft <= toUpLeft) return left
  return toUp <= toUpLeft ? up : upLeft
}

// Undoes the per-row filters (none, sub, up, average, paeth) of a one-byte-per-pixel image.
function unfilter(raw: Buffer, width: number, height: number): Uint8Array {
  const pixels = new Uint8Array(width * height)
  for (let y = 0; y < height; y++) {
    const filter = raw[y * (width + 1)]
    const line = raw.subarray(y * (width + 1) + 1, (y + 1) * (width + 1))
    for (let x = 0; x < width; x++) {
      const left = x > 0 ? pixels[y * width + x - 1]! : 0
      const up = y > 0 ? pixels[(y - 1) * width + x]! : 0
      const upLeft = x > 0 && y > 0 ? pixels[(y - 1) * width + x - 1]! : 0
      const predicted = filter === 0 ? 0 : filter === 1 ? left : filter === 2 ? up : filter === 3 ? (left + up) >> 1 : paeth(left, up, upLeft)
      if (filter! > 4) throw new Error(`unknown PNG filter ${filter}`)
      pixels[y * width + x] = (line[x]! + predicted) & 255
    }
  }
  return pixels
}

export function decodePalettePng(base64: string): Indexed {
  const data = Buffer.from(base64, "base64")
  if (!data.subarray(0, 8).equals(SIGNATURE)) throw new Error("not a PNG")
  let width = 0
  let height = 0
  let palette: [number, number, number][] = []
  let alpha: number[] = []
  const compressed: Buffer[] = []
  for (let at = 8; at < data.length; ) {
    const length = data.readUInt32BE(at)
    const type = data.toString("latin1", at + 4, at + 8)
    const body = data.subarray(at + 8, at + 8 + length)
    if (type === "IHDR") {
      width = body.readUInt32BE(0)
      height = body.readUInt32BE(4)
      if (body[8] !== 8 || body[9] !== 3 || body[12] !== 0) throw new Error("only 8-bit palette PNGs without interlacing are read")
    } else if (type === "PLTE") {
      palette = Array.from({ length: length / 3 }, (_, i) => [body[3 * i]!, body[3 * i + 1]!, body[3 * i + 2]!])
    } else if (type === "tRNS") {
      alpha = Array.from(body)
    } else if (type === "IDAT") {
      compressed.push(body)
    }
    at += length + 12
  }
  const pixels = unfilter(inflateSync(Buffer.concat(compressed)), width, height)
  return { width, height, palette, alpha: palette.map((_, i) => alpha[i] ?? 255), pixels }
}

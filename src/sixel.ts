// Sixel: a picture as text a terminal draws pixel by pixel (DEC's format, spoken by Windows
// Terminal 1.22+, WezTerm, mintty, foot, iTerm2, Konsole and others). Six pixel rows make a
// band; within a band each colour is written as a run of 6-bit columns, and `$` goes back
// to the left edge for the next colour.
import type { Indexed } from "./png.js"

const TRANSPARENT_BELOW = 128
// A run of at least this many equal columns is written as `!count` and the character.
const RUN_FROM = 4

// The picture scaled to width x height by nearest neighbour. It takes existing palette entries only,
// so pixel art stays crisp and no new colours appear.
export function scaleNearest(image: Indexed, width: number, height: number): Uint8Array {
  const scaled = new Uint8Array(width * height)
  for (let y = 0; y < height; y++) {
    const from = Math.min(image.height - 1, Math.floor(((y + 0.5) * image.height) / height))
    for (let x = 0; x < width; x++) {
      scaled[y * width + x] = image.pixels[from * image.width + Math.min(image.width - 1, Math.floor(((x + 0.5) * image.width) / width))]!
    }
  }
  return scaled
}

function runs(columns: Uint8Array): string {
  let out = ""
  for (let x = 0; x < columns.length; ) {
    let end = x
    while (end < columns.length && columns[end] === columns[x]) end++
    const char = String.fromCharCode(63 + columns[x]!)
    out += end - x >= RUN_FROM ? `!${end - x}${char}` : char.repeat(end - x)
    x = end
  }
  return out
}

// The picture as one DCS string. P2=1 leaves the pixels no colour is written for as they were, which
// is what makes the transparent parts show the terminal's background.
export function encodeSixel(image: Indexed, width: number, height: number): string {
  const pixels = scaleNearest(image, width, height)
  const used = new Set<number>()
  const bands: string[] = []
  for (let top = 0; top < height; top += 6) {
    const columns = new Map<number, Uint8Array>()
    for (let dy = 0; dy < 6 && top + dy < height; dy++) {
      for (let x = 0; x < width; x++) {
        const color = pixels[(top + dy) * width + x]!
        if ((image.alpha[color] ?? 255) < TRANSPARENT_BELOW) continue
        const column = columns.get(color) ?? new Uint8Array(width)
        column[x]! |= 1 << dy
        columns.set(color, column)
        used.add(color)
      }
    }
    bands.push([...columns].map(([color, column]) => `#${color}${runs(column)}`).join("$"))
  }
  const palette = [...used].sort((a, b) => a - b).map((color) => {
    const [r, g, b] = image.palette[color]!
    return `#${color};2;${Math.round((r * 100) / 255)};${Math.round((g * 100) / 255)};${Math.round((b * 100) / 255)}`
  })
  return `\x1bP0;1;0q"1;1;${width};${height}${palette.join("")}${bands.join("-")}\x1b\\`
}

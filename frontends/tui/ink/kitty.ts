// The kitty graphics protocol, the part the welcome animation needs: show a PNG over a
// rectangle of cells, and take it away. kitty, Ghostty and Konsole speak it. Whether a
// terminal does is decided from the environment alone (as terminal_image.py does for the
// classic terminal): asking would mean reading its answer from stdin, which Ink owns.
import { deflateSync } from "node:zlib"

const CHUNK = 4096

export function kittyGraphics(env: NodeJS.ProcessEnv): boolean {
  const term = (env.TERM ?? "").toLowerCase()
  // A multiplexer swallows graphics escapes unless passthrough is set up, and TERM_PROGRAM
  // still names the terminal outside it.
  if (env.TMUX || term.startsWith("screen") || term.startsWith("tmux")) return false
  const program = (env.TERM_PROGRAM ?? "").toLowerCase()
  return term === "xterm-kitty" || Boolean(env.KITTY_WINDOW_ID) || program === "ghostty" || Boolean(env.GHOSTTY_RESOURCES_DIR) || Boolean(env.KONSOLE_VERSION)
}

export interface Placement {
  // The terminal keeps one image per id; showing a new one under the same id replaces it.
  id: number
  // The top-left cell, 0-based, and the size in cells; the picture is scaled to fit them.
  row: number
  col: number
  columns: number
  rows: number
  // A band of the picture to show instead of all of it, in the picture's own pixels, when part of
  // it is out of view: the terminal scales it to `rows`.
  source?: { y: number; height: number }
}

// Shows `png` (base64) at the placement. C=1 leaves the cursor where it was, q=2 keeps the
// terminal from answering into stdin, and the data goes in chunks of at most 4096 bytes.
export function placeImage(png: string, at: Placement): string {
  const chunks = Array.from({ length: Math.ceil(png.length / CHUNK) }, (_, index) => png.slice(index * CHUNK, (index + 1) * CHUNK))
  const parts = chunks.map((chunk, index) => {
    const more = index < chunks.length - 1 ? 1 : 0
    const crop = at.source ? `,y=${at.source.y},h=${at.source.height}` : ""
    const keys = index === 0 ? `a=T,f=100,i=${at.id},p=1,c=${at.columns},r=${at.rows}${crop},C=1,q=2,m=${more}` : `m=${more}`
    return `\x1b_G${keys};${chunk}\x1b\\`
  })
  return `\x1b[${at.row + 1};${at.col + 1}H${parts.join("")}`
}

// Removes the image and frees its data.
export function deleteImage(id: number): string {
  return `\x1b_Ga=d,d=I,i=${id},q=2\x1b\\`
}

// Shows raw RGB pixels (3 bytes each, zlib-compressed on the wire) behind the text: `z` is below
// the cell backgrounds, so a cell with a colour of its own covers the picture and a cell with
// none lets it through. Sent under an id that already exists, it replaces that image.
export function placeRgb(rgb: Uint8Array, width: number, height: number, at: { id: number; columns: number; rows: number; z: number }): string {
  const data = deflateSync(rgb, { level: 1 }).toString("base64")
  const chunks = Array.from({ length: Math.ceil(data.length / CHUNK) }, (_, index) => data.slice(index * CHUNK, (index + 1) * CHUNK))
  const parts = chunks.map((chunk, index) => {
    const more = index < chunks.length - 1 ? 1 : 0
    const keys = index === 0 ? `a=T,f=24,s=${width},v=${height},o=z,i=${at.id},p=1,c=${at.columns},r=${at.rows},z=${at.z},C=1,q=2,m=${more}` : `m=${more}`
    return `\x1b_G${keys};${chunk}\x1b\\`
  })
  return `\x1b[1;1H${parts.join("")}`
}

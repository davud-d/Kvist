// The squirrel of the welcome block: six frames played once at two a second, then it
// stays on the last one. The frames are cells made by make_mascot_frames.py, so
// this only draws them; a cell without a background lets the terminal's own show through.
//
// A terminal that can draw pictures (kitty graphics protocol) is shown the frames as real
// images instead: the same animation, as small PNGs over the same rectangle of cells, which
// are left blank then. The picture is in the terminal's graphics layer and does not move
// with the text, so `syncMascotImage` keeps it over the mascot's cells, or removes it while
// they are scrolled out of view or partly hidden.
import { Box, Text, measureElement, type DOMElement } from "ink"
import { useContext, useEffect, useRef, useState } from "react"

import type { Pointer } from "../pointer.js"
import { ViewContext } from "./context.js"
import { MASCOT_OCTANTS, MASCOT_QUADRANTS, type MascotCell, type MascotFrames } from "./mascot-frames.js"
import { MASCOT_PNGS } from "./mascot-images.js"

export const MASCOT_FPS = 2
export const MASCOT_ROWS = MASCOT_QUADRANTS[0]?.length ?? 0
export const MASCOT_COLUMNS = MASCOT_QUADRANTS[0]?.[0]?.length ?? 0
const FRAME_COUNT = MASCOT_QUADRANTS.length

export type Glyphs = "octants" | "quadrants"

// Octants are twice as sharp, but only a font that has Unicode 16's octants draws them, and a
// terminal cannot be asked which glyphs its font has. Windows Terminal ships Cascadia Code, which
// has them since 2404; anywhere else the quadrants that every font draws are used. KVIST_GLYPHS
// overrides the guess, for a Windows Terminal set to another font or a terminal known to do octants.
export function mascotGlyphs(env: NodeJS.ProcessEnv): Glyphs {
  const chosen = (env.KVIST_GLYPHS ?? "").toLowerCase()
  if (chosen === "octants" || chosen === "quadrants") return chosen
  return env.WT_SESSION ? "octants" : "quadrants"
}

export interface Run {
  text: string
  fg: string | null
  bg: string | null
}

// One row as runs of cells that share both colours, so the blank cells around the squirrel are one Text.
export function runsOf(row: readonly MascotCell[]): Run[] {
  const runs: Run[] = []
  for (const [char, fg, bg] of row) {
    const last = runs.at(-1)
    if (last && last.fg === fg && last.bg === bg) last.text += char
    else runs.push({ text: char, fg, bg })
  }
  return runs
}

// Where the mascot's cells are, for `syncMascotImage`, and which frame is showing.
export const mascotSlot: { node: DOMElement | null; frame: number } = { node: null, frame: 0 }

// The nearest ancestor that clips its children, whose rectangle is all that can be seen.
function clippingAncestor(node: DOMElement): DOMElement | null {
  for (let parent = node.parentNode; parent; parent = parent.parentNode) {
    if (parent.style.overflowY === "hidden" || parent.style.overflow === "hidden") return parent
  }
  return null
}

// Puts the picture over the mascot's cells if they are all in view, and takes it away if not.
export function syncMascotImage(pointer: Pointer | null): void {
  if (!pointer?.images) return
  const node = mascotSlot.node
  if (!node) return pointer.showImage(null)
  const { x, y, width, height } = measureElement(node)
  const clip = clippingAncestor(node)
  const view = clip ? measureElement(clip) : null
  const inView = width > 0 && height > 0 && (!view || (y >= view.y && y + height <= view.y + view.height))
  if (!inView) return pointer.showImage(null)
  pointer.showImage({ png: MASCOT_PNGS[mascotSlot.frame] ?? "", key: String(mascotSlot.frame), row: y, col: x, columns: width, rows: height })
}

function mascotFrames(): MascotFrames {
  return mascotGlyphs(process.env) === "octants" ? MASCOT_OCTANTS : MASCOT_QUADRANTS
}

export function MascotFrame({ index, frames = mascotFrames() }: { index: number; frames?: MascotFrames }) {
  const frame = frames[Math.min(index, frames.length - 1)] ?? []
  return (
    <Box flexDirection="column" flexShrink={0}>
      {frame.map((row, y) => (
        <Text key={y}>
          {runsOf(row).map((run, x) => (
            <Text key={x} color={run.fg ?? undefined} backgroundColor={run.bg ?? undefined}>{run.text}</Text>
          ))}
        </Text>
      ))}
    </Box>
  )
}

export function Mascot() {
  const { pointer } = useContext(ViewContext)
  const slot = useRef<DOMElement>(null)
  const [index, setIndex] = useState(0)
  const pictures = pointer?.images ?? false
  useEffect(() => {
    if (index >= FRAME_COUNT - 1) return
    const timer = setTimeout(() => setIndex(index + 1), 1000 / MASCOT_FPS)
    return () => clearTimeout(timer)
  }, [index])
  useEffect(() => {
    mascotSlot.node = slot.current
    return () => {
      mascotSlot.node = null
      syncMascotImage(pointer)
    }
  }, [pointer])
  useEffect(() => {
    mascotSlot.frame = index
    syncMascotImage(pointer)
  }, [index, pointer])
  // The cells stay empty under a picture, or the squirrel would show through its transparent parts.
  if (pictures) return <Box ref={slot} width={MASCOT_COLUMNS} height={MASCOT_ROWS} flexShrink={0} />
  return <MascotFrame index={index} />
}

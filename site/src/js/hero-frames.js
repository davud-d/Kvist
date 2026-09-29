/**
 * hero-frames.js — the Kvist banner animation, ported from the Python source.
 *
 * The port is straight from banner_frames.py (the state machine, expressed as
 * a pure function of elapsed time) and banner.py (the 5x7 bitmaps and the
 * upper-half-block raster: two pixel rows share one cell, the top row painted
 * as the cell's foreground and the bottom row as its background).
 *
 * Every duration below is the integer millisecond value from banner_frames.py.
 * What is deliberately NOT ported is colour: the Python module carries literal
 * RGB, the page carries roles. A frame here returns a role per slot and the
 * renderer resolves it through tokens.css.
 *
 * No DOM in this file: the contrast measurement tool runs the same frames in
 * Node, so the number in the report comes from the code that ships.
 */

/* banner.py uses NUL as the cursor glyph. A page cannot, so the sentinel is a
   character that appears in neither the wordmark nor the prompt. */
export const CURSOR = "|";
export const PROMPT = ">";
export const TEXT = "Kvist";

/* -- timings, banner_frames.py ------------------------------------------- */
export const TYPE_FRAME_MS = 45;
export const TYPE_GAP_MS = 50;
export const DECAY_FRAME_MS = 50;
export const DECAY_GAP_MS = 30;
export const HOLD_MS = 2500;
export const IDLE_MS = 900;
export const CURSOR_ON_MS = 530;
export const CURSOR_OFF_MS = 530;
export const BOOT_BLINK_MS = 600;

/* Ramps are roles, resolved against tokens.css by the renderer. A colour is
   [roleA, roleB, mix]: 0 is roleA, 1 is roleB. `glyph` is --hero-glyph,
   `flash` is --hero-flash, `plate` is --hero-plate, `prompt` is --hero-flash
   at full strength (the settled `>` in banner.py is PROMPT_WHITE). */
const TYPE_RAMP = [
  ["flash", "glyph", 0],
  ["flash", "glyph", 0.45],
  ["flash", "glyph", 1],
];
const DECAY_RAMP = [
  ["glyph", "plate", 0.35],
  ["glyph", "plate", 0.66],
  ["glyph", "plate", 0.84],
];
const PROMPT_COLOR = ["flash", "flash", 0];
const BOOT_APPEARANCE = [TYPE_RAMP[0], TYPE_RAMP[1], PROMPT_COLOR];
const CURSOR_MID = ["glyph", "plate", 0.18];

export const BOOT_RAMP_MS = BOOT_APPEARANCE.length * TYPE_FRAME_MS; // 135
export const BOOT_MS = BOOT_RAMP_MS + BOOT_BLINK_MS; // 735
export const LETTER_MS = 2 * TYPE_FRAME_MS + TYPE_GAP_MS; // 140
export const DECAY_MS = DECAY_RAMP.length * DECAY_FRAME_MS; // 150
export const DELETE_LETTER_MS = DECAY_MS + DECAY_GAP_MS; // 180
export const CURSOR_PERIOD_MS = CURSOR_ON_MS + DECAY_MS + CURSOR_OFF_MS;

/** The instant the whole word stands on screen for the first time. */
export const WORD_COMPLETE_MS = BOOT_MS + TEXT.length * LETTER_MS;

export function cycleMs(letters) {
  return letters * LETTER_MS + HOLD_MS + letters * DELETE_LETTER_MS + IDLE_MS;
}

function cursorColorAt(elapsedMs) {
  let phase = elapsedMs % CURSOR_PERIOD_MS;
  if (phase < CURSOR_ON_MS) return CURSOR_MID;
  phase -= CURSOR_ON_MS;
  if (phase < DECAY_MS) return DECAY_RAMP[Math.floor(phase / DECAY_FRAME_MS)];
  return null;
}

/**
 * The animation state at `elapsedMs`, as a Map of slot -> [glyph, colour].
 * Slot 0 is the prompt, slots 1..n hold the letters, and the cursor takes
 * whichever slot it has reached. Absent slots are background.
 */
export function frameAt(elapsedMs, text = TEXT) {
  const now = Math.round(elapsedMs);
  const letters = text.length;
  const frame = new Map();

  if (now < BOOT_RAMP_MS) {
    frame.set(0, [PROMPT, BOOT_APPEARANCE[Math.floor(now / TYPE_FRAME_MS)]]);
    frame.set(1, [CURSOR, CURSOR_MID]);
    return frame;
  }

  frame.set(0, [PROMPT, PROMPT_COLOR]);

  if (now < BOOT_MS) {
    const color = cursorColorAt(now - BOOT_RAMP_MS);
    if (color) frame.set(1, [CURSOR, color]);
    return frame;
  }

  let phase = (now - BOOT_MS) % cycleMs(letters);

  const typeMs = letters * LETTER_MS;
  if (phase < typeMs) {
    const settled = Math.floor(phase / LETTER_MS);
    const within = phase % LETTER_MS;
    for (let slot = 1; slot <= settled; slot += 1) {
      frame.set(slot, [text[slot - 1], TYPE_RAMP[2]]);
    }
    const slot = settled + 1;
    const step = Math.min(
      Math.floor(within / TYPE_FRAME_MS),
      TYPE_RAMP.length - 1,
    );
    frame.set(slot, [text[slot - 1], TYPE_RAMP[step]]);
    frame.set(slot + 1, [CURSOR, CURSOR_MID]);
    return frame;
  }
  phase -= typeMs;

  if (phase < HOLD_MS) {
    for (let slot = 1; slot <= letters; slot += 1) {
      frame.set(slot, [text[slot - 1], TYPE_RAMP[2]]);
    }
    const color = cursorColorAt(phase);
    if (color) frame.set(1 + letters, [CURSOR, color]);
    return frame;
  }
  phase -= HOLD_MS;

  const deleteMs = letters * DELETE_LETTER_MS;
  if (phase < deleteMs) {
    const removed = Math.floor(phase / DELETE_LETTER_MS);
    const within = phase % DELETE_LETTER_MS;
    const slot = letters - removed;
    for (let kept = 1; kept < slot; kept += 1) {
      frame.set(kept, [text[kept - 1], TYPE_RAMP[2]]);
    }
    if (within < DECAY_MS) {
      frame.set(slot, [
        text[slot - 1],
        DECAY_RAMP[Math.floor(within / DECAY_FRAME_MS)],
      ]);
      frame.set(slot + 1, [CURSOR, CURSOR_MID]);
    } else {
      frame.set(slot, [CURSOR, CURSOR_MID]);
    }
    return frame;
  }
  phase -= deleteMs;

  const color = cursorColorAt(phase);
  if (color) frame.set(1, [CURSOR, color]);
  return frame;
}

/* -- glyph bitmaps, banner.py FONT --------------------------------------- */
/* 5 columns x 7 rows, '#' foreground, '.' background. Only the glyphs the
   wordmark needs are carried over; the terminal keeps the other 64. */
export const GLYPH_W = 5;
export const GLYPH_H = 7;
export const CELL_ROWS = 4; // two pixel rows per cell, 7 rows -> 4 cells
export const SLOT_W = 6;

export const FONT = {
  K: ["#...#", "#..#.", "#.#..", "##...", "#.#..", "#..#.", "#...#"],
  v: [".....", ".....", "#...#", "#...#", "#...#", ".#.#.", "..#.."],
  i: ["..#..", ".....", "..#..", "..#..", "..#..", "..#..", "..#.."],
  s: [".....", ".....", ".####", "#....", ".###.", "....#", "####."],
  t: ["..#..", "..#..", "#####", "..#..", "..#..", "..#..", "...##"],
  ">": ["#....", ".#...", "..#..", "...#.", "..#..", ".#...", "#...."],
  "|": ["##...", "##...", "##...", "##...", "##...", "##...", "##..."],
  " ": [".....", ".....", ".....", ".....", ".....", ".....", "....."],
};

/** Slots the composition occupies: prompt, every letter, one cursor slot. */
export const SLOT_COUNT = TEXT.length + 2;

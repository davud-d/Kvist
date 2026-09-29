#!/usr/bin/env node
/**
 * cast-compile.mjs — asciinema v2 cast -> compact replay JSON.
 *
 *   node tools/cast-compile.mjs assets/demo/run-en.cast assets/demo/run-en.json
 *
 * A 5 MB cast is mostly redundant: prompt_toolkit repaints the whole banner
 * many times a second. Running it through the terminal emulator and keeping
 * only rows that actually changed removes that redundancy without touching
 * what the recording shows.
 *
 * The one thing that is dropped is the tail after `/bye`: the driver's own
 * teardown clears the screen and prints a goodbye, which would otherwise
 * become the poster frame. Nothing the agent did is trimmed.
 *
 * Output shape:
 *   {
 *     w, h, duration,
 *     rows:   [ spans, ... ],            // dictionary of distinct row states
 *     frames: [ [t, row, id, row, id …] ],  // flat: changed rows only
 *     final:  [ id, ... ],               // last frame as dictionary ids
 *   }
 * A span is [text, fg, bg, bold] where fg/bg index --ansi-0..15, or -1 for the
 * default foreground and -2 for the default background.
 *
 * The dictionary is what makes this small. The banner cycles through a bounded
 * set of states, so the same row content recurs hundreds of times; storing each
 * distinct row once and referring to it by id turns megabytes into kilobytes.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";
import { Screen } from "./vt.mjs";

const here = dirname(fileURLToPath(import.meta.url));

/* A frame is taken whenever a burst of writes finishes, which is the only
   moment the screen is in a state anybody saw. There is no additional sample
   interval: a rate gate measured against the compressed clock suppresses
   exactly the frames that matter, because a repaint advances that clock by
   almost nothing. Identical frames cost nothing — emit() drops them, and the
   row dictionary stores each distinct line once. */
/* Nothing interesting happens during a model call; collapse those gaps. */
const MAX_GAP_MS = 900;
/* A full-screen repaint arrives as a burst: erase, then the new content, a few
   milliseconds apart. Sampling inside that burst captures the erase and misses
   the content — which silently drops whole screens, such as an option picker.
   Writes closer together than this are treated as one frame. */
const COALESCE_MS = 40;

/** Palette from tokens.css, dark theme — used only to snap colours to slots. */
function palette() {
  const css = readFileSync(resolve(here, "../src/tokens.css"), "utf8");
  const scope = css.slice(css.indexOf(':root[data-theme="dark"]'));
  const root = css.slice(0, css.indexOf("@media"));
  const pick = (name) => {
    const inDark = new RegExp(`--${name}\\s*:\\s*([^;]+);`).exec(scope);
    const inRoot = new RegExp(`--${name}\\s*:\\s*([^;]+);`).exec(root);
    return (inDark ? inDark[1] : inRoot ? inRoot[1] : "#000000").trim();
  };
  const literal = (value) => {
    const chain = /^var\(\s*--([a-z0-9-]+)\s*\)$/i.exec(value);
    return chain ? literal(pick(chain[1])) : value;
  };
  const rgb = (value) => {
    const hex = /^#([0-9a-f]{6})$/i.exec(literal(value));
    if (!hex) return [0, 0, 0];
    return [0, 2, 4].map((i) => parseInt(hex[1].slice(i, i + 2), 16));
  };
  return Array.from({ length: 16 }, (_, i) => rgb(pick(`ansi-${i}`)));
}

function parseCast(text) {
  const lines = text.split("\n").filter(Boolean);
  const header = JSON.parse(lines[0]);
  const events = [];
  for (let i = 1; i < lines.length; i += 1) {
    let event;
    try { event = JSON.parse(lines[i]); } catch { continue; }
    if (Array.isArray(event) && event[1] === "o") events.push([event[0], event[2]]);
  }
  return { header, events };
}

function rowKey(spans_) {
  return JSON.stringify(spans_);
}

function main() {
  const [, , inPath, outPath] = process.argv;
  if (!inPath || !outPath) {
    console.error("usage: cast-compile.mjs <in.cast> <out.json>");
    process.exit(2);
  }

  const { header, events } = parseCast(readFileSync(inPath, "utf8"));
  const cols = header.width || 100;
  const rows = header.height || 32;
  const screen = new Screen(cols, rows, palette());

  const frames = [];
  const table = [];
  const ids = new Map();
  const previous = new Array(rows).fill(-1);
  let clock = 0;      // compressed timeline
  let lastRaw = 0;

  const intern = (spans_) => {
    const key = rowKey(spans_);
    let id = ids.get(key);
    if (id === undefined) {
      id = table.length;
      table.push(spans_);
      ids.set(key, id);
    }
    return id;
  };

  const emit = (t) => {
    const snap = screen.snapshot();
    const frame = [Math.round(t)];
    for (let r = 0; r < rows; r += 1) {
      const id = intern(snap[r]);
      if (id !== previous[r]) {
        frame.push(r, id);
        previous[r] = id;
      }
    }
    if (frame.length === 1) return;
    frames.push(frame);
  };

  for (let index = 0; index < events.length; index += 1) {
    const [raw, data] = events[index];
    const gap = Math.min((raw - lastRaw) * 1000, MAX_GAP_MS);
    clock += Math.max(0, gap);
    lastRaw = raw;
    screen.write(data);

    const next = events[index + 1];
    const midBurst = next !== undefined && (next[0] - raw) * 1000 < COALESCE_MS;
    if (!midBurst) emit(clock);
  }
  emit(clock + 1);

  /* Walk back past the /bye teardown to the last frame that still holds the
     run on screen; that frame is the poster and the no-JavaScript fallback. */
  const grid = new Array(rows).fill(0);
  const filled = [];
  for (const frame of frames) {
    for (let i = 1; i < frame.length; i += 2) grid[frame[i]] = frame[i + 1];
    filled.push(grid.filter((id) => table[id].length > 0).length);
  }
  let last = frames.length - 1;
  while (last > 0 && filled[last] < 8) last -= 1;

  const kept = frames.slice(0, last + 1);
  const poster = new Array(rows).fill(0);
  for (const frame of kept) {
    for (let i = 1; i < frame.length; i += 2) poster[frame[i]] = frame[i + 1];
  }

  const out = {
    w: cols,
    h: rows,
    duration: kept.length ? kept[kept.length - 1][0] + 400 : 0,
    rows: table,
    frames: kept,
    final: poster,
  };

  const json = JSON.stringify(out);
  writeFileSync(outPath, json);
  const gz = gzipSync(Buffer.from(json), { level: 9 }).length;
  const kb = (n) => (n / 1024).toFixed(1) + " KB";
  console.log(
    `${inPath} -> ${outPath}: ${kept.length} frames ` +
    `(${frames.length - kept.length} teardown frames dropped), ` +
    `${table.length} distinct rows, ${kb(json.length)} raw / ${kb(gz)} gzip, ` +
    `${Math.round(out.duration / 1000)}s`,
  );
}

main();

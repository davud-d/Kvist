#!/usr/bin/env node
/**
 * hero-contrast.mjs — contrast of the hero text over the hero animation,
 * measured frame by frame instead of judged by eye.
 *
 *   node tools/hero-contrast.mjs
 *
 * It imports the same frame module the browser runs, replays the intro plus a
 * full cycle at 10 ms resolution, and for every colour the canvas paints in
 * that frame composites it through the scrim onto the page background and
 * measures it against --tx. The worst of all of them is the answer — in the
 * dark theme that is the brightest colour, in the light theme the darkest, so
 * neither theme is checked against the wrong end of its own palette.
 *
 * The measurement is deliberately pessimistic: it assumes the brightest pixel
 * of the frame sits directly under the text and is veiled only by
 * --hero-veil-text. During the intro the raster is in the clear band above the
 * text, so the real contrast there is higher than the number reported.
 *
 * Exits non-zero below 4.5:1.
 */
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { frameAt, cycleMs, TEXT, WORD_COMPLETE_MS } from "../src/js/hero-frames.js";

const here = dirname(fileURLToPath(import.meta.url));
const css = readFileSync(resolve(here, "../src/tokens.css"), "utf8");

/* Mirrors the two phases in src/js/hero.js. */
const INTRO_MS = 1200;
const SETTLE_MS = 420;
const STEP_MS = 10;
const MIN = 4.5;

/* -- tokens ---------------------------------------------------------------- */

function scope(name) {
  const marker = name === "root" ? ":root {" : `:root[data-theme="${name}"] {`;
  const at = css.indexOf(marker);
  if (at < 0) return {};
  let depth = 1;
  let i = at + marker.length;
  const from = i;
  while (i < css.length && depth > 0) {
    if (css[i] === "{") depth += 1;
    else if (css[i] === "}") depth -= 1;
    i += 1;
  }
  const out = {};
  for (const line of css.slice(from, i - 1).split(";")) {
    const decl = /--([a-z0-9-]+)\s*:\s*([^;]+)/i.exec(line);
    if (decl) out[decl[1].trim()] = decl[2].trim();
  }
  return out;
}

function tokens(theme) {
  const table = { ...scope("root"), ...scope(theme) };
  const literal = (name, seen = new Set()) => {
    if (seen.has(name)) return "#000000";
    seen.add(name);
    const raw = table[name] ?? "#000000";
    const chain = /^var\(\s*--([a-z0-9-]+)\s*\)$/i.exec(raw);
    return chain ? literal(chain[1], seen) : raw;
  };
  return { literal, number: (name) => Number(literal(name)) };
}

function rgb(value) {
  const text = String(value).trim();
  const hex = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(text);
  if (hex) {
    const h = hex[1].length === 3 ? hex[1].replace(/./g, (c) => c + c) : hex[1];
    return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
  }
  const list = /^([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)$/.exec(text);
  if (list) return [1, 2, 3].map((i) => Number(list[i]));
  return [0, 0, 0];
}

/* -- WCAG ------------------------------------------------------------------ */

const channel = (v) => {
  const s = v / 255;
  return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
};
const luminance = ([r, g, b]) =>
  0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
const contrast = (a, b) => {
  const [hi, lo] = [luminance(a), luminance(b)].sort((p, q) => q - p);
  return (hi + 0.05) / (lo + 0.05);
};
const over = (top, bottom, alpha) =>
  top.map((c, i) => alpha * c + (1 - alpha) * bottom[i]);
const mix = (a, b, t) => a.map((c, i) => c + (b[i] - c) * t);

/* -- measurement ----------------------------------------------------------- */

function measure(theme) {
  const t = tokens(theme);
  const roles = {
    glyph: rgb(t.literal("hero-glyph")),
    flash: rgb(t.literal("hero-flash")),
    plate: rgb(t.literal("hero-plate")),
  };
  const ink = rgb(t.literal("ink"));
  const tx = rgb(t.literal("tx"));
  const scrim = rgb(t.literal("hero-scrim"));
  const veil = t.number("hero-veil-text");
  const settleAlpha = t.number("hero-settle");

  const total = INTRO_MS + SETTLE_MS + cycleMs(TEXT.length) + 500;
  let worst = Infinity;
  let worstAt = 0;
  let samples = 0;

  for (let wall = 0; wall <= total; wall += STEP_MS) {
    const settled =
      wall <= INTRO_MS ? 0 : Math.min(1, (wall - INTRO_MS) / SETTLE_MS);
    const eased = 1 - (1 - settled) ** 3;
    const canvasAlpha = 1 - (1 - settleAlpha) * eased;

    // The colours this frame paints: the plate always, plus each glyph's role.
    const frame = frameAt(wall);
    const painted = [roles.plate];
    for (const [, [, role]] of frame) {
      painted.push(mix(roles[role[0]], roles[role[1]], role[2]));
    }

    // Every one of them, composited: canvas over page, then scrim over that.
    samples += 1;
    for (const color of painted) {
      const onPage = over(color, ink, canvasAlpha);
      const behindText = over(scrim, onPage, veil);
      const ratio = contrast(tx, behindText);
      if (ratio < worst) {
        worst = ratio;
        worstAt = wall;
      }
    }
  }

  return { theme, worst, worstAt, samples, veil, settleAlpha };
}

const results = [measure("dark"), measure("light")];
const w = (s, n) => String(s).padEnd(n);

console.log("HERO TEXT OVER THE ANIMATION — worst frame of intro + one cycle");
console.log("-".repeat(66));
console.log(w("THEME", 8) + w("FRAMES", 9) + w("VEIL", 7) + w("SETTLE", 9) + w("WORST", 10) + "STATUS");
for (const r of results) {
  console.log(
    w(r.theme, 8) + w(r.samples, 9) + w(r.veil.toFixed(2), 7) +
    w(r.settleAlpha.toFixed(2), 9) +
    w(r.worst.toFixed(2) + ":1", 10) +
    (r.worst >= MIN ? "pass" : "FAIL") + `  (at ${r.worstAt} ms)`,
  );
}
console.log("-".repeat(66));
console.log(`word complete at ${WORD_COMPLETE_MS} ms of animation time; intro budget ${INTRO_MS} ms`);

if (results.some((r) => r.worst < MIN)) process.exit(1);

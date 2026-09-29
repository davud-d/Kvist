#!/usr/bin/env node
/**
 * contrast.mjs — WCAG contrast over every token pair in src/tokens.css.
 *
 *   node tools/contrast.mjs            # both themes, table on stdout
 *   node tools/contrast.mjs --json     # machine-readable
 *
 * Exits non-zero if any pair that CARRIES TEXT falls below 4.5:1, so it can
 * gate a build. Run it after every edit to tokens.css.
 *
 * --line / --line-2 are dividers, never text, so they are not held to 4.5:1.
 * They are held to a different rule that matters more on this project: the
 * owner's display runs at brightness 35, where anything darker than #1a1a1a
 * disappears. Their check is a brightness floor, and it also fails the build.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const tokensPath = resolve(here, "../src/tokens.css");

/* -- token extraction ------------------------------------------------------ */

/** Values under `:root[data-theme="<name>"]`, falling back to bare `:root`. */
function readThemes(css) {
  const base = block(css, /:root\s*\{/);
  const dark = { ...base, ...block(css, /:root\[data-theme="dark"\]\s*\{/) };
  const light = { ...base, ...block(css, /:root\[data-theme="light"\]\s*\{/) };
  return { dark, light };
}

function block(css, opener) {
  const match = opener.exec(css);
  if (!match) return {};
  let depth = 1;
  let index = match.index + match[0].length;
  const start = index;
  while (index < css.length && depth > 0) {
    if (css[index] === "{") depth += 1;
    else if (css[index] === "}") depth -= 1;
    index += 1;
  }
  const body = css.slice(start, index - 1);
  const out = {};
  for (const line of body.split(";")) {
    const decl = /--([a-z0-9-]+)\s*:\s*([^;]+)/i.exec(line);
    if (decl) out[decl[1].trim()] = decl[2].trim();
  }
  return out;
}

/** Resolve var() chains down to a literal colour, or null for non-colours. */
function resolve_(name, table, seen = new Set()) {
  if (seen.has(name)) return null;
  seen.add(name);
  const raw = table[name];
  if (!raw) return null;
  const chain = /^var\(\s*--([a-z0-9-]+)\s*\)$/i.exec(raw);
  if (chain) return resolve_(chain[1], table, seen);
  return parse(raw);
}

function parse(value) {
  const hex = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(value.trim());
  if (hex) {
    const h = hex[1].length === 3 ? hex[1].replace(/./g, (c) => c + c) : hex[1];
    return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
  }
  const rgb = /^rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)/i.exec(value.trim());
  if (rgb) return [1, 2, 3].map((i) => Math.round(Number(rgb[i])));
  return null;
}

/* -- WCAG ------------------------------------------------------------------ */

const channel = (v) => {
  const s = v / 255;
  return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
};
const luminance = ([r, g, b]) =>
  0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);

export function ratio(a, b) {
  const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
}

/* -- the pairs that actually carry text ------------------------------------ */

const SURFACES = ["ink", "panel"];
const TEXT = ["tx", "dim", "faint", "acc", "ok"];
const BORDERS = ["line", "line-2"];
const TEXT_MIN = 4.5;
/* #1a1a1a — below this a border vanishes on a display at brightness 35. */
const BORDER_FLOOR = luminance([0x1a, 0x1a, 0x1a]);

function audit(theme, table) {
  const rows = [];
  for (const surface of SURFACES) {
    const bg = resolve_(surface, table);
    if (!bg) continue;
    for (const token of TEXT) {
      const fg = resolve_(token, table);
      if (!fg) continue;
      rows.push({
        theme,
        fg: `--${token}`,
        bg: `--${surface}`,
        ratio: ratio(fg, bg),
        min: TEXT_MIN,
        kind: "text",
      });
    }
  }
  // Inverted blocks: --ink text on a --paper fill.
  const paper = resolve_("paper", table);
  const ink = resolve_("ink", table);
  if (paper && ink) {
    rows.push({
      theme, fg: "--ink", bg: "--paper",
      ratio: ratio(ink, paper), min: TEXT_MIN, kind: "text",
    });
  }
  // Every ANSI slot the replay can paint, over the replay's own surface.
  const replayBg = resolve_("panel", table);
  for (let i = 0; i < 16; i += 1) {
    const fg = resolve_(`ansi-${i}`, table);
    if (!fg || !replayBg) continue;
    if (i === 0) continue;                       // ansi-0 IS the background
    rows.push({
      theme, fg: `--ansi-${i}`, bg: "--panel",
      ratio: ratio(fg, replayBg), min: TEXT_MIN, kind: "text",
    });
  }
  return rows;
}

/** Dark-theme borders must stay at or above the #1a1a1a brightness floor. */
function auditBorders(theme, table) {
  if (theme !== "dark") return [];
  return BORDERS.flatMap((token) => {
    const value = resolve_(token, table);
    if (!value) return [];
    return [{
      theme,
      token: `--${token}`,
      lum: luminance(value),
      floor: BORDER_FLOOR,
      ok: luminance(value) >= BORDER_FLOOR,
    }];
  });
}

/* -- report ---------------------------------------------------------------- */

const css = readFileSync(tokensPath, "utf8");
const { dark, light } = readThemes(css);
const rows = [...audit("dark", dark), ...audit("light", light)];
const borders = [...auditBorders("dark", dark), ...auditBorders("light", light)];
const failed = rows.filter((r) => r.ratio < r.min);
const dimBorders = borders.filter((b) => !b.ok);

if (process.argv.includes("--json")) {
  console.log(JSON.stringify({ rows, borders, failed, dimBorders }, null, 2));
} else {
  const w = (s, n) => String(s).padEnd(n);
  console.log(`${w("THEME", 7)}${w("FOREGROUND", 14)}${w("BACKGROUND", 12)}${w("RATIO", 9)}${w("MIN", 6)}STATUS`);
  console.log("-".repeat(56));
  let theme = "";
  for (const r of rows) {
    if (r.theme !== theme) { theme = r.theme; }
    const ok = r.ratio >= r.min;
    console.log(
      w(r.theme, 7) + w(r.fg, 14) + w(r.bg, 12) +
      w(r.ratio.toFixed(2) + ":1", 9) + w(r.min.toFixed(1), 6) +
      (ok ? "pass" : "FAIL"),
    );
  }
  console.log("-".repeat(56));
  console.log(`${rows.length} text pairs, ${failed.length} below 4.5:1`);
  console.log("");
  console.log("BORDER BRIGHTNESS FLOOR (dark theme, #1a1a1a)");
  console.log("-".repeat(56));
  for (const b of borders) {
    console.log(
      w(b.token, 14) + w(b.lum.toFixed(4), 10) +
      w(">= " + b.floor.toFixed(4), 14) + (b.ok ? "pass" : "TOO DIM"),
    );
  }
  console.log("-".repeat(56));
}

if (failed.length > 0 || dimBorders.length > 0) process.exit(1);

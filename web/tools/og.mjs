#!/usr/bin/env node
/**
 * og.mjs — the social card and the tab icon, drawn with the terminal's own
 * 5x7 bitmap font.
 *
 *   node tools/og.mjs dist/assets/og.png
 *
 * There is no image library here and no headless browser: both images are flat
 * rectangles on a grid, so a small PNG encoder is enough. The font is the one
 * exported from banner.py, and the colours come from tokens.css, which means
 * repainting the palette repaints them on the next build.
 */
import { deflateSync } from "node:zlib";
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const FONT = JSON.parse(readFileSync(resolve(here, "../src/font5x7.json"), "utf8"));
const css = readFileSync(resolve(here, "../src/tokens.css"), "utf8");

/* -- tokens ---------------------------------------------------------------- */

function token(name) {
  const dark = css.slice(css.indexOf(':root[data-theme="dark"]'));
  const root = css.slice(0, css.indexOf("@media"));
  const find = (scope) =>
    new RegExp(`--${name}\\s*:\\s*([^;]+);`).exec(scope)?.[1]?.trim();
  const value = find(dark) ?? find(root) ?? "#000000";
  const chain = /^var\(\s*--([a-z0-9-]+)\s*\)$/i.exec(value);
  return chain ? token(chain[1]) : value;
}

function rgb(value) {
  const hex = /^#([0-9a-f]{6})$/i.exec(value);
  if (!hex) return [0, 0, 0];
  return [0, 2, 4].map((i) => parseInt(hex[1].slice(i, i + 2), 16));
}

/* -- a surface to draw flat blocks on -------------------------------------- */

function surface(width, height) {
  const pixels = Buffer.alloc(width * height * 3);

  const fill = (x0, y0, w, h, color) => {
    const [r, g, b] = color;
    for (let y = Math.max(0, y0); y < Math.min(height, y0 + h); y += 1) {
      for (let x = Math.max(0, x0); x < Math.min(width, x0 + w); x += 1) {
        const at = (y * width + x) * 3;
        pixels[at] = r;
        pixels[at + 1] = g;
        pixels[at + 2] = b;
      }
    }
  };

  /** Draw a string in the 5x7 font at `scale` pixels per font pixel. */
  const text = (string, x, y, scale, color) => {
    let cursor = x;
    for (const ch of string) {
      const glyph = FONT[ch] ?? FONT[" "];
      for (let row = 0; row < glyph.length; row += 1) {
        for (let column = 0; column < glyph[row].length; column += 1) {
          if (glyph[row][column] !== "#") continue;
          fill(cursor + column * scale, y + row * scale, scale, scale, color);
        }
      }
      cursor += 6 * scale;
    }
  };

  return { width, height, pixels, fill, text };
}

const measure = (string, scale) => string.length * 6 * scale;

/* -- PNG ------------------------------------------------------------------- */

function crc32(data) {
  let crc = ~0;
  for (const byte of data) {
    crc ^= byte;
    for (let i = 0; i < 8; i += 1) crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
  }
  return ~crc >>> 0;
}

function chunk(type, data) {
  const head = Buffer.alloc(8);
  head.writeUInt32BE(data.length, 0);
  head.write(type, 4, "ascii");
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(Buffer.concat([head.subarray(4), data])), 0);
  return Buffer.concat([head, data, crc]);
}

function png({ width, height, pixels }) {
  const raw = Buffer.alloc((width * 3 + 1) * height);
  for (let y = 0; y < height; y += 1) {
    raw[y * (width * 3 + 1)] = 0; // filter: none — the image is flat blocks
    pixels.copy(raw, y * (width * 3 + 1) + 1, y * width * 3, (y + 1) * width * 3);
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 2; // truecolour
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", ihdr),
    chunk("IDAT", deflateSync(raw, { level: 9 })),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

/* -- the images ------------------------------------------------------------ */

const ink = rgb(token("ink"));
const panel = rgb(token("panel"));
const line = rgb(token("line"));
const tx = rgb(token("tx"));
const faint = rgb(token("faint"));
const acc = rgb(token("acc"));

function card() {
  const s = surface(1200, 630);
  s.fill(0, 0, s.width, s.height, ink);

  // The terminal plate, the same motif the hero opens with.
  const [px, py, pw, ph] = [90, 96, s.width - 180, 300];
  s.fill(px, py, pw, ph, panel);
  s.fill(px, py, pw, 2, line);
  s.fill(px, py + ph - 2, pw, 2, line);
  s.fill(px, py, 2, ph, line);
  s.fill(px + pw - 2, py, 2, ph, line);

  const scale = 12;
  const mark = "> Kvist";
  const markX = px + (pw - measure(mark, scale)) / 2 + scale;
  s.text(mark, markX, py + 108, scale, tx);
  // The cursor block, in the accent, exactly where the animation parks it.
  s.fill(markX + measure(mark, scale), py + 108, scale * 2, scale * 7, acc);

  const l1 = "one workspace. one action per step.";
  const l2 = "a supervised terminal coding agent";
  const l3 = "python . mit . beta";
  s.text(l1, (s.width - measure(l1, 4)) / 2, 452, 4, tx);
  s.text(l2, (s.width - measure(l2, 4)) / 2, 500, 4, faint);
  s.text(l3, (s.width - measure(l3, 3)) / 2, 552, 3, faint);
  return s;
}

function icon() {
  const s = surface(64, 64);
  s.fill(0, 0, 64, 64, ink);
  s.fill(2, 2, 60, 60, panel);
  // The prompt and its cursor: the smallest piece of the mark that still
  // reads at 16 pixels in a tab strip.
  s.text(">", 8, 15, 5, tx);
  s.fill(42, 15, 10, 35, acc);
  return s;
}

const out = process.argv[2] ?? resolve(here, "../dist/assets/og.png");
mkdirSync(dirname(out), { recursive: true });

const ogPng = png(card());
writeFileSync(out, ogPng);
const iconPng = png(icon());
const iconPath = join(dirname(out), "favicon.png");
writeFileSync(iconPath, iconPng);

console.log(`${out}: 1200x630, ${(ogPng.length / 1024).toFixed(1)} KB`);
console.log(`${iconPath}: 64x64, ${(iconPng.length / 1024).toFixed(1)} KB`);

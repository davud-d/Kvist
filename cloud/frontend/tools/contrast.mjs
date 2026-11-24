// WCAG AA over every token pair that carries text, in both themes. Exits non-zero below 4.5:1.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const TEXT = ["tx", "dim", "faint", "acc", "ok", "warn", "err"];
const SURFACES = ["ink", "panel", "panel-2"];

function block(css, selector) {
  const start = css.indexOf(selector);
  if (start < 0) throw new Error(`no ${selector} block in tokens.css`);
  const open = css.indexOf("{", start);
  let depth = 0;
  for (let i = open; i < css.length; i += 1) {
    if (css[i] === "{") depth += 1;
    if (css[i] === "}") depth -= 1;
    if (depth === 0) return css.slice(open + 1, i);
  }
  throw new Error(`unclosed ${selector}`);
}

function tokens(text) {
  const found = {};
  for (const match of text.matchAll(/--([\w-]+):\s*(#[0-9a-fA-F]{6})/g)) found[match[1]] = match[2];
  return found;
}

function luminance(hex) {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function ratio(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

export function checkContrast(path) {
  const css = readFileSync(path, "utf8");
  const dark = tokens(block(css, ":root {"));
  const light = { ...dark, ...tokens(block(css, ':root[data-theme="light"]')) };
  const failures = [];
  const rows = [];
  for (const [name, theme] of [["dark", dark], ["light", light]]) {
    const pairs = TEXT.flatMap((fg) => SURFACES.map((bg) => [fg, bg]));
    pairs.push(["ink", "paper"]);
    for (const [fg, bg] of pairs) {
      const value = ratio(theme[fg], theme[bg]);
      rows.push(`${name.padEnd(5)} --${fg} on --${bg}: ${value.toFixed(2)}`);
      if (value < 4.5) failures.push(`${name}: --${fg} on --${bg} is ${value.toFixed(2)}:1`);
    }
  }
  return { failures, rows };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const { failures, rows } = checkContrast(new URL("../src/tokens.css", import.meta.url));
  console.log(rows.join("\n"));
  if (failures.length) {
    console.error(`\ncontrast below 4.5:1:\n${failures.join("\n")}`);
    process.exit(1);
  }
}

#!/usr/bin/env node
/**
 * i18n-check.mjs — the two locales must have the same shape.
 *
 *   node tools/i18n-check.mjs
 *
 * A missing key is a hole in one of the two pages, so it fails the build
 * instead of printing a warning. Array lengths count too: an "items" list with
 * four entries in one locale and three in the other is the same kind of hole.
 */
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const load = (name) =>
  JSON.parse(readFileSync(resolve(here, `../src/i18n/${name}.json`), "utf8"));

/** Every leaf path in the document, arrays indexed by position. */
function paths(node, prefix = "") {
  if (Array.isArray(node)) {
    return node.flatMap((item, index) => paths(item, `${prefix}[${index}]`));
  }
  if (node && typeof node === "object") {
    return Object.entries(node).flatMap(([key, value]) =>
      paths(value, prefix ? `${prefix}.${key}` : key),
    );
  }
  return [prefix];
}

const en = new Set(paths(load("en")));
const ru = new Set(paths(load("ru")));

const missingInRu = [...en].filter((key) => !ru.has(key));
const missingInEn = [...ru].filter((key) => !en.has(key));

console.log(`en.json: ${en.size} keys`);
console.log(`ru.json: ${ru.size} keys`);

for (const key of missingInRu) console.log(`  missing in ru.json: ${key}`);
for (const key of missingInEn) console.log(`  missing in en.json: ${key}`);

if (missingInRu.length || missingInEn.length) {
  console.log(`\n${missingInRu.length + missingInEn.length} keys differ`);
  process.exit(1);
}
console.log("key sets match");

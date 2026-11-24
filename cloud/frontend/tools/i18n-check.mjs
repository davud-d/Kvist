// Both locales must have the same keys, and list values the same length.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

function shape(value, prefix = "") {
  if (Array.isArray(value)) return [`${prefix}[${value.length}]`];
  if (value && typeof value === "object") {
    return Object.entries(value).flatMap(([key, item]) => shape(item, prefix ? `${prefix}.${key}` : key));
  }
  return [prefix];
}

export function checkLocales(dir, prefix = "") {
  const en = JSON.parse(readFileSync(new URL(`${prefix}en.json`, dir), "utf8"));
  const ru = JSON.parse(readFileSync(new URL(`${prefix}ru.json`, dir), "utf8"));
  const a = new Set(shape(en));
  const b = new Set(shape(ru));
  return [...[...a].filter((key) => !b.has(key)).map((key) => `ru lacks ${key}`),
          ...[...b].filter((key) => !a.has(key)).map((key) => `en lacks ${key}`)];
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const dir = new URL("../src/i18n/", import.meta.url);
  const problems = [...checkLocales(dir), ...checkLocales(dir, "admin.")];
  if (problems.length) {
    console.error(problems.join("\n"));
    process.exit(1);
  }
  console.log("locales match");
}

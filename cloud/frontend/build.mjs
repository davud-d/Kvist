// Builds dist/: the app bundle, the HTML shell and the prerendered pricing pages, with the CSS
// inlined. Refuses to finish when a gate fails: locale parity, contrast, the first-screen budget.
//
//   node build.mjs          build
//   node build.mjs --watch  rebuild on change (dev)
import { build, transform } from "esbuild";
import { cpSync, existsSync, mkdirSync, readFileSync, rmSync, watch, writeFileSync } from "node:fs";
import { gzipSync } from "node:zlib";
import { checkContrast } from "./tools/contrast.mjs";
import { checkLocales } from "./tools/i18n-check.mjs";

const SRC = new URL("./src/", import.meta.url);
const DIST = new URL("./dist/", import.meta.url);
const PIXEL = new URL("../pixel/", import.meta.url);
const BUDGET_BYTES = 100 * 1024;
const FONTS = {
  latin: ["kvist-mono-latin-400.woff2", "kvist-mono-latin-700.woff2"],
  cyrillic: ["kvist-mono-cyrillic-400.woff2", "kvist-mono-cyrillic-700.woff2"],
};

const read = (name) => readFileSync(new URL(name, SRC), "utf8");
const esc = (text) => String(text).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

async function css() {
  const source = ["tokens.css", "base.css", "app.css"].map(read).join("\n");
  const { code } = await transform(source, { loader: "css", minify: true });
  return code.trim();
}

function shell(styles, lang) {
  const t = JSON.parse(read(`i18n/${lang}.json`));
  return `<!doctype html>
<html lang="${lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kvist Cloud</title>
<meta name="description" content="${esc(t.meta.description)}">
<meta name="robots" content="noindex">
<link rel="icon" href="data:,">
<style>${styles}</style>
<script type="module" src="/assets/app.js"></script>
<script id="boot" type="application/json">null</script>
</head>
<body>
<a class="skip" href="#main">${esc(t.common.skip)}</a>
<div id="app"><header class="top"><div class="wrap top-row"><a class="mark" href="/"><span class="dot" aria-hidden="true"></span>${esc(t.common.brand)}<span class="sub">${esc(t.common.cloud)}</span></a></div></header><main class="wrap" id="main"><span class="skeleton line" style="width:60%"></span><span class="skeleton line" style="width:40%"></span></main></div>
<noscript><p class="wrap">Kvist Cloud needs JavaScript for this page. / Для этой страницы нужен JavaScript.</p></noscript>
</body>
</html>
`;
}

function gz(text) {
  return gzipSync(Buffer.from(text)).length;
}

function fontBytes(scripts) {
  return scripts.flatMap((script) => FONTS[script]).reduce(
    (total, name) => total + readFileSync(new URL(`fonts/${name}`, SRC)).length, 0);
}

async function buildOnce() {
  const problems = [...checkLocales(new URL("i18n/", SRC)), ...checkContrast(new URL("tokens.css", SRC)).failures];
  if (problems.length) throw new Error(`gates failed:\n${problems.join("\n")}`);

  rmSync(DIST, { recursive: true, force: true });
  mkdirSync(new URL("assets/fonts/", DIST), { recursive: true });
  for (const name of [...FONTS.latin, ...FONTS.cyrillic, "OFL.txt"]) {
    cpSync(new URL(`fonts/${name}`, SRC), new URL(`assets/fonts/${name}`, DIST));
  }
  // The pixel window is plain files (no bundler), served as they are under /pixel.
  cpSync(PIXEL, new URL("pixel/", DIST), { recursive: true, filter: (from) => !/\/(tests|node_modules)(\/|$)/.test(from) });
  const styles = await css();
  await build({
    entryPoints: [new URL("js/app.js", SRC).pathname],
    bundle: true,
    minify: true,
    format: "esm",
    target: "es2020",
    outfile: new URL("assets/app.js", DIST).pathname,
    legalComments: "none",
  });
  const js = readFileSync(new URL("assets/app.js", DIST), "utf8");
  const report = [];
  for (const lang of ["en", "ru"]) {
    const html = shell(styles, lang);
    writeFileSync(new URL(`app.${lang}.html`, DIST), html);
    report.push([`app (${lang})`, gz(html) + gz(js) + fontBytes(lang === "ru" ? ["latin", "cyrillic"] : ["latin"])]);
  }
  const pricing = new URL("pricing.mjs", SRC);
  if (existsSync(pricing)) {
    const { renderPricing } = await import(`${pricing.href}?t=${Date.now()}`);
    const plans = JSON.parse(read("plans.json"));
    for (const lang of ["en", "ru"]) {
      const html = renderPricing(JSON.parse(read(`i18n/${lang}.json`)), plans, styles);
      writeFileSync(new URL(`pricing.${lang}.html`, DIST), html);
      report.push([`pricing (${lang})`, gz(html) + fontBytes(lang === "ru" ? ["latin", "cyrillic"] : ["latin"])]);
    }
  }
  const over = report.filter(([, bytes]) => bytes > BUDGET_BYTES);
  for (const [name, bytes] of report) {
    console.log(`${name.padEnd(16)} first screen ${(bytes / 1024).toFixed(1).padStart(6)} KB (budget 100 KB)`);
  }
  console.log(`app.js ${(gz(js) / 1024).toFixed(1)} KB gzip, css ${(gz(styles) / 1024).toFixed(1)} KB gzip`);
  if (over.length) throw new Error(`over budget: ${over.map(([name]) => name).join(", ")}`);
}

try {
  await buildOnce();
} catch (error) {
  console.error(String(error.message || error));
  process.exit(1);
}

if (process.argv.includes("--watch")) {
  let timer = null;
  watch(SRC, { recursive: true }, () => {
    clearTimeout(timer);
    timer = setTimeout(() => buildOnce().then(() => console.log("rebuilt"), (error) => console.error(error.message)), 150);
  });
  console.log("watching src/ …");
}

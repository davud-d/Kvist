#!/usr/bin/env node
/**
 * build.mjs — renders the two locales into dist/ and checks the budgets.
 *
 *   node build.mjs            # build once
 *   node build.mjs --serve    # build, then serve dist/ on :4173
 *
 * The whole generator is this file plus src/template.mjs. esbuild does the two
 * things worth a tool — minifying the CSS and bundling the modules — and
 * nothing else is installed.
 */
import { build as esbuild, transform } from "esbuild";
import { gzipSync } from "node:zlib";
import {
  cpSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { createServer } from "node:http";
import { dirname, extname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { render, renderGuides, SITE, BOOT } from "./src/template.mjs";
import { resolveRequest } from "./tools/static-path.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const dist = resolve(here, "dist");

/* Budgets from the brief, in bytes after gzip. A build that breaks one fails. */
const BUDGET = {
  js: 40 * 1024,
  css: 30 * 1024,
  fonts: 90 * 1024,
  firstScreen: 400 * 1024,
};

/* Each guide is one recording of one flow; the ids double as file names. */
const GUIDES = [
  "provider", "custom", "models", "changeapi",
  "commands", "role", "effort", "style", "toggles",
  "session", "changes", "headless",
];

/* The frame a guide is shown on before anybody presses Play, and the frame a
   reader without JavaScript gets. The last frame of a recording is its least
   interesting one — the flow is already over — so each guide names the moment
   that shows what it is about. Matching on text rather than a timestamp keeps
   these working after the guides are re-recorded. */
const GUIDE_POSTERS = {
  provider: "❯ OmniRoute — Local OpenAI-compatible",
  custom: "❯ + Create custom provider...",
  models: "❯ Enter a separate model id per role",
  changeapi: "New OmniRoute API key",
  commands: "/revert — Restore agent-changed files",
  role: "❯ DevOps — Infrastructure",
  effort: "❯ xhigh — Extended reasoning",
  style: "❯ Paranoid — An outage costs more",
  toggles: "Planning stage disabled",
  session: "❯ + Create new session...",
  changes: "Agent changes in this workspace",
  headless: "journal-op changes",
};
/* Every page the site publishes, for the sitemap. */
const PATHS = ["/", "/ru/", "/guides/", "/ru/guides/"];

const KB = (n) => `${(n / 1024).toFixed(1)} KB`;
const gz = (data) => gzipSync(Buffer.from(data), { level: 9 }).length;

/* -- gates ----------------------------------------------------------------- */

function gate(name, script) {
  try {
    execFileSync(process.execPath, [resolve(here, "tools", script)], {
      stdio: ["ignore", "pipe", "inherit"],
    });
    console.log(`  ${name}: pass`);
  } catch {
    console.error(`\nbuild stopped: ${name} failed — run node tools/${script}`);
    process.exit(1);
  }
}

/* -- build ----------------------------------------------------------------- */

async function main() {
  console.log("gates");
  gate("locale keys", "i18n-check.mjs");
  gate("token contrast", "contrast.mjs");
  gate("hero contrast", "hero-contrast.mjs");

  rmSync(dist, { recursive: true, force: true });
  mkdirSync(join(dist, "assets"), { recursive: true });

  const buildId = Date.now().toString(36);

  // CSS: three files, one inlined stylesheet, no request of its own.
  const rawCss = ["src/tokens.css", "src/base.css", "src/components.css"]
    .map((file) => readFileSync(resolve(here, file), "utf8"))
    .join("\n");
  const { code: css } = await transform(rawCss, {
    loader: "css",
    minify: true,
  });

  // JS: one module bundle.
  await esbuild({
    entryPoints: [resolve(here, "src/js/main.js")],
    bundle: true,
    format: "esm",
    target: ["chrome120", "firefox120", "safari17"],
    minify: true,
    outfile: join(dist, "assets/app.js"),
    legalComments: "none",
  });
  const js = readFileSync(join(dist, "assets/app.js"));

  // Assets.
  cpSync(resolve(here, "assets/fonts"), join(dist, "assets/fonts"), {
    recursive: true,
    filter: (src) => !src.endsWith("font-face.css"),
  });
  mkdirSync(join(dist, "assets/demo"), { recursive: true });
  for (const locale of ["en", "ru"]) {
    cpSync(
      resolve(here, `assets/demo/run-${locale}.json`),
      join(dist, `assets/demo/run-${locale}.json`),
    );
  }
  for (const guide of GUIDES) {
    cpSync(
      resolve(here, `assets/demo/guide-${guide}.json`),
      join(dist, `assets/demo/guide-${guide}.json`),
    );
  }
  execFileSync(process.execPath, [
    resolve(here, "tools/og.mjs"),
    join(dist, "assets/og.png"),
  ]);

  // Pages: the front page and the guides page, once per locale.
  const guideCasts = Object.fromEntries(
    GUIDES.map((guide) => {
      const cast = JSON.parse(
        readFileSync(resolve(here, `assets/demo/guide-${guide}.json`), "utf8"),
      );
      cast.final = posterFrame(cast, GUIDE_POSTERS[guide], guide);
      return [guide, cast];
    }),
  );

  const pages = [];
  const write = (name, target, html) => {
    const full = join(dist, target);
    mkdirSync(dirname(full), { recursive: true });
    writeFileSync(full, html);
    pages.push({ name, target, html });
  };

  for (const locale of ["en", "ru"]) {
    const t = JSON.parse(
      readFileSync(resolve(here, `src/i18n/${locale}.json`), "utf8"),
    );
    const cast = JSON.parse(
      readFileSync(resolve(here, `assets/demo/run-${locale}.json`), "utf8"),
    );
    const prefix = locale === "en" ? "" : "ru/";
    write(locale, `${prefix}index.html`, render({ t, css, cast, buildId }));
    write(
      `${locale} guides`,
      `${prefix}guides/index.html`,
      renderGuides({ t, css, casts: guideCasts, buildId }),
    );
  }

  writeFileSync(
    join(dist, "sitemap.xml"),
    `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:xhtml="http://www.w3.org/1999/xhtml">
${PATHS
  .map(
    (path) => {
      const en = path.startsWith("/ru/") ? path.replace("/ru/", "/") : path;
      const ru = path.startsWith("/ru/") ? path : `/ru${path}`;
      return `  <url>
    <loc>${SITE}${path}</loc>
    <xhtml:link rel="alternate" hreflang="en" href="${SITE}${en}"/>
    <xhtml:link rel="alternate" hreflang="ru" href="${SITE}${ru}"/>
    <xhtml:link rel="alternate" hreflang="x-default" href="${SITE}${en}"/>
  </url>`;
    },
  )
  .join("\n")}
</urlset>
`,
  );
  writeFileSync(
    join(dist, "robots.txt"),
    `User-agent: *\nAllow: /\n\nSitemap: ${SITE}/sitemap.xml\n`,
  );
  writeFileSync(join(dist, "security-headers.conf"), securityHeaders());

  report({ css, js, pages });
}

/**
 * The nginx snippet the deploy includes, written by the build because only the
 * build knows the hash. The theme decision is an inline script — it has to run
 * before the first paint — so a policy that allows it has to name its digest,
 * and a hand-copied digest goes stale the first time that script is edited.
 *
 * The page makes no request off its own origin: no CDN, no analytics, no
 * webfont host. That is what makes a policy this narrow possible, and the
 * policy is what keeps it true — an added third-party script fails visibly
 * here rather than quietly shipping.
 *
 * style-src keeps 'unsafe-inline' and cannot drop it: the stylesheet is inlined
 * into <head>, the replay carries style attributes, and the player writes
 * element.style as it paints. Hashes do not cover attributes, so the honest
 * thing is to allow it and say why.
 */
function securityHeaders() {
  const bootHash = createHash("sha256").update(BOOT, "utf8").digest("base64");
  const policy = [
    "default-src 'none'",
    `script-src 'self' 'sha256-${bootHash}'`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data:",
    "font-src 'self'",
    "connect-src 'self'",
    "base-uri 'none'",
    "form-action 'none'",
    "frame-ancestors 'none'",
  ].join("; ");

  return `# Written by build.mjs — do not edit; the script hash is computed from
# the inline theme script and changes whenever that script does.
#
#   include /srv/kvist-site/security-headers.conf;
#
# add_header does not inherit into a location block that adds headers of its
# own, so this is included in each one that does.
add_header Content-Security-Policy "${policy}" always;
add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
add_header X-Content-Type-Options "nosniff" always;
add_header Referrer-Policy "strict-origin-when-cross-origin" always;
add_header Permissions-Policy "geolocation=(), camera=(), microphone=(), interest-cohort=()" always;
`;
}

/** The first frame whose screen contains `marker`, as dictionary ids. */
function posterFrame(cast, marker, name) {
  const screen = new Array(cast.h).fill(0);
  const text = (id) => cast.rows[id].map((span) => span[0]).join("");
  for (const frame of cast.frames) {
    for (let i = 1; i < frame.length; i += 2) screen[frame[i]] = frame[i + 1];
    if (screen.some((id) => text(id).includes(marker))) return [...screen];
  }
  console.error(
    `\nbuild stopped: guide "${name}" has no frame containing ${JSON.stringify(marker)}`,
  );
  process.exit(1);
}

/* -- report and budgets ---------------------------------------------------- */

/** Bytes of the actual font files; the licence and notes ship but do not count. */
function fontBytes(path) {
  let total = 0;
  for (const entry of readdirSync(path, { withFileTypes: true })) {
    if (!entry.name.endsWith(".woff2")) continue;
    total += statSync(join(path, entry.name)).size;
  }
  return total;
}

function report({ css, js, pages }) {
  const cssGz = gz(css);
  const jsGz = gz(js);
  const fonts = fontBytes(join(dist, "assets/fonts"));
  const ogSize = statSync(join(dist, "assets/og.png")).size;

  console.log("\noutput");
  for (const page of pages) {
    console.log(
      `  ${page.name.padEnd(10)}dist/${page.target.padEnd(22)}` +
      `${KB(Buffer.byteLength(page.html)).padStart(9)} raw /${KB(gz(page.html)).padStart(9)} gzip`,
    );
  }

  /* First screen: the HTML response (CSS is inside it), the module, and the
     fonts the page preloads — latin on /, latin plus cyrillic on /ru/. The
     cast JSON loads when the demo is scrolled to, and og.png is never fetched
     by a visitor at all. */
  const html = Math.max(...pages.map((p) => gz(p.html)));
  const font = (name) => statSync(join(dist, `assets/fonts/${name}.woff2`)).size;
  const firstScreen =
    html + jsGz +
    font("kvist-mono-latin-400") + font("kvist-mono-latin-700") +
    font("kvist-mono-cyrillic-400") + font("kvist-mono-cyrillic-700");

  const rows = [
    ["css (inlined)", cssGz, BUDGET.css],
    ["js bundle", jsGz, BUDGET.js],
    ["fonts, all files", fonts, BUDGET.fonts],
    ["first screen", firstScreen, BUDGET.firstScreen],
  ];

  console.log("\nbudget (gzip, fonts already compressed)");
  let broke = false;
  for (const [name, value, limit] of rows) {
    const ok = value <= limit;
    if (!ok) broke = true;
    console.log(
      `  ${name.padEnd(18)}${KB(value).padStart(9)} / ${KB(limit).padStart(9)}  ${ok ? "ok" : "OVER"}`,
    );
  }
  console.log(`  ${"og.png".padEnd(18)}${KB(ogSize).padStart(9)}`);

  for (const locale of ["en", "ru"]) {
    const file = join(dist, `assets/demo/run-${locale}.json`);
    console.log(
      `  ${`cast ${locale} (deferred)`.padEnd(18)}${KB(statSync(file).size).padStart(9)}` +
      ` raw / ${KB(gz(readFileSync(file)))} gzip`,
    );
  }
  const guideTotal = GUIDES.reduce(
    (sum, guide) => sum + gz(readFileSync(join(dist, `assets/demo/guide-${guide}.json`))),
    0,
  );
  console.log(
    `  ${`${GUIDES.length} guides (deferred)`.padEnd(18)}${KB(guideTotal).padStart(9)} gzip, loaded on view`,
  );

  if (broke) {
    console.error("\nbuild stopped: a budget was exceeded");
    process.exit(1);
  }
  console.log("\nbuilt");
}

/* -- dev server ------------------------------------------------------------ */

const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".woff2": "font/woff2",
  ".png": "image/png",
  ".xml": "application/xml; charset=utf-8",
  ".txt": "text/plain; charset=utf-8",
};

function serve(port = 4173) {
  createServer((request, response) => {
    /* This one listens on every interface, so the containment rule is the only
       thing between dist/ and the network. It lives in tools/static-path.mjs. */
    const file = resolveRequest(dist, request.url);
    if (!file) {
      response.writeHead(403).end();
      return;
    }
    try {
      const body = readFileSync(file);
      response.writeHead(200, {
        "content-type": TYPES[extname(file)] ?? "application/octet-stream",
        "cache-control": "no-store",
      });
      response.end(body);
    } catch {
      response.writeHead(404, { "content-type": "text/plain" }).end("404");
    }
  }).listen(port, "0.0.0.0", () => {
    console.log(`\nserving dist/ on http://0.0.0.0:${port}/ and /ru/`);
  });
}

await main();
if (process.argv.includes("--serve")) serve();

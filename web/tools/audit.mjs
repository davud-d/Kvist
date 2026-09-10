#!/usr/bin/env node
/**
 * audit.mjs — the checks from the brief, run against the built site.
 *
 *   node build.mjs && node tools/audit.mjs
 *
 * It serves dist/ itself, then for every width and both locales asserts: no
 * horizontal scroll, the install command above the fold at 1366x768, no text
 * under 14px outside the replay, and both themes. It then repeats the pass
 * with JavaScript off and with reduced motion on.
 *
 * Needs the playwright package and a Chromium. It uses the system Chrome when
 * one is installed rather than downloading a second copy; if neither is there
 * the script says so and exits without failing a build that has nothing to do
 * with it.
 */
import { createServer } from "node:http";
import { existsSync, readFileSync, statSync } from "node:fs";
import { dirname, extname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const dist = resolve(here, "../dist");

const WIDTHS = [380, 620, 768, 1024, 1366, 1440];
const LOCALES = ["/", "/ru/"];
const GUIDE_PAGES = ["/guides/", "/ru/guides/"];
/* One replay per guide, read from the built page rather than hard-coded. */
const GUIDE_COUNT = JSON.parse(
  readFileSync(resolve(here, "../src/i18n/en.json"), "utf8"),
).guides.items.length;
const ALL = [...LOCALES, ...GUIDE_PAGES];
const PORT = 4271;

const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".woff2": "font/woff2",
  ".png": "image/png",
  ".xml": "application/xml; charset=utf-8",
  ".txt": "text/plain; charset=utf-8",
};

function serve() {
  const server = createServer((request, response) => {
    /* A protocol-relative request such as "//" makes new URL() throw, which
       would take the whole server down. Nothing served here is worth that. */
    let path;
    try {
      path = decodeURIComponent(new URL(request.url, "http://localhost").pathname);
    } catch {
      response.writeHead(400).end("bad request");
      return;
    }
    if (path.endsWith("/")) path += "index.html";
    const file = join(dist, path);
    try {
      const body = readFileSync(file);
      response.writeHead(200, {
        "content-type": TYPES[extname(file)] ?? "application/octet-stream",
        "cache-control": "no-store",
      });
      response.end(body);
    } catch {
      response.writeHead(404).end("404");
    }
  });
  return new Promise((done) => server.listen(PORT, () => done(server)));
}

let chromium;
try {
  ({ chromium } = await import("playwright"));
} catch {
  console.log("playwright is not installed — skipping the browser audit");
  process.exit(0);
}

const server = await serve();
const base = `http://127.0.0.1:${PORT}`;
const problems = [];
const note = (message) => problems.push(message);

/* Prefer a Chrome that is already on the machine over a private download. */
const SYSTEM_CHROME = [
  "/usr/bin/google-chrome-stable",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].find((path) => existsSync(path));

let browser;
try {
  browser = await chromium.launch(
    SYSTEM_CHROME ? { executablePath: SYSTEM_CHROME } : {},
  );
} catch (error) {
  console.log(`no usable browser (${error.message.split("\n")[0]}) — skipping`);
  server.close();
  process.exit(0);
}

/** Everything measured on one loaded page. */
async function inspect(page) {
  return page.evaluate(() => {
    const doc = document.documentElement;
    const small = [];
    for (const el of document.querySelectorAll("body *")) {
      if (!el.textContent.trim() || el.children.length) continue;
      if (el.closest(".replay-screen")) continue; // sized by the brief's §8
      const size = parseFloat(getComputedStyle(el).fontSize);
      if (size < 14) small.push(`${el.tagName.toLowerCase()}.${el.className} ${size}px`);
    }
    const command = document.getElementById("install-command");
    const rect = command ? command.getBoundingClientRect() : null;
    return {
      scrollWidth: doc.scrollWidth,
      clientWidth: doc.clientWidth,
      commandBottom: rect ? Math.round(rect.bottom) : 0,
      commandText: command ? command.textContent.trim() : "",
      small: [...new Set(small)],
      h1s: document.querySelectorAll("h1").length,
      lang: doc.lang,
      title: document.title,
      replays: document.querySelectorAll(".replay-screen").length,
      /* Every replay must be a full 32-row screen; the smallest one answers. */
      replayRows: Math.min(
        ...[...document.querySelectorAll(".replay-screen")].map(
          (el) => el.children.length,
        ),
      ),
      replayHasText: Math.min(
        ...[...document.querySelectorAll(".replay-screen")].map(
          (el) => el.textContent.trim().length,
        ),
      ),
    };
  });
}

for (const scheme of ["dark", "light"]) {
  for (const width of WIDTHS) {
    for (const path of ALL) {
      const context = await browser.newContext({
        viewport: { width, height: 768 },
        colorScheme: scheme,
        deviceScaleFactor: 1,
      });
      const page = await context.newPage();
      await page.goto(base + path, { waitUntil: "load" });
      await page.waitForTimeout(250);
      const r = await inspect(page);
      const at = `${scheme} ${width}px ${path}`;

      if (r.scrollWidth > r.clientWidth + 1) {
        note(`${at}: horizontal scroll (${r.scrollWidth} > ${r.clientWidth})`);
      }
      if (r.small.length) note(`${at}: text under 14px — ${r.small.join(", ")}`);
      if (r.h1s !== 1) note(`${at}: ${r.h1s} h1 elements`);
      if (LOCALES.includes(path)) {
        if (width === 1366 && r.commandBottom > 768) {
          note(`${at}: install command bottom at ${r.commandBottom}px, below the fold`);
        }
        if (!r.commandText.startsWith("git clone")) {
          note(`${at}: install command missing`);
        }
      }
      await context.close();
    }
  }
}

/* No JavaScript: the page, the command and the demo's final frame must all be
   in the first response. */
for (const path of ALL) {
  const context = await browser.newContext({
    viewport: { width: 1366, height: 768 },
    javaScriptEnabled: false,
  });
  const page = await context.newPage();
  await page.goto(base + path, { waitUntil: "load" });
  const r = await inspect(page);
  if (LOCALES.includes(path)) {
    if (!r.commandText.startsWith("git clone")) note(`no-js ${path}: no command`);
    const copyVisible = await page.locator(".copy").isVisible();
    if (copyVisible) note(`no-js ${path}: copy button shown without a clipboard`);
  }
  const wantReplays = GUIDE_PAGES.includes(path) ? GUIDE_COUNT : 1;
  if (r.replays !== wantReplays) {
    note(`no-js ${path}: ${r.replays} replays, expected ${wantReplays}`);
  }
  if (r.replayRows !== 32) note(`no-js ${path}: ${r.replayRows} replay rows, expected 32`);
  if (r.replayHasText < 200) note(`no-js ${path}: a replay frame looks empty`);
  if (r.scrollWidth > r.clientWidth + 1) note(`no-js ${path}: horizontal scroll`);
  await context.close();
}

/* Reduced motion: the animation draws one frame and stops, the replay shows
   its final frame, and the reveal groups are not left invisible. */
for (const path of ALL) {
  const context = await browser.newContext({
    viewport: { width: 1366, height: 768 },
    reducedMotion: "reduce",
  });
  const page = await context.newPage();
  await page.goto(base + path, { waitUntil: "load" });
  await page.waitForTimeout(600);
  const state = await page.evaluate(() => {
    const card = document.querySelector(".grid .card");
    return {
      cardOpacity: card ? getComputedStyle(card).opacity : "1",
      replayText: Math.min(
        ...[...document.querySelectorAll(".replay-screen")].map(
          (el) => el.textContent.trim().length,
        ),
      ),
    };
  });
  if (Number(state.cardOpacity) < 1) {
    note(`reduced-motion ${path}: cards stay at opacity ${state.cardOpacity}`);
  }
  if (state.replayText < 200) note(`reduced-motion ${path}: a replay is empty`);
  await context.close();
}

/* Keyboard: every control must be reachable and the replay must respond. */
{
  const context = await browser.newContext({ viewport: { width: 1366, height: 768 } });
  const page = await context.newPage();
  await page.goto(base + "/", { waitUntil: "load" });
  await page.waitForTimeout(400);
  const stops = [];
  for (let i = 0; i < 26; i += 1) {
    await page.keyboard.press("Tab");
    stops.push(
      await page.evaluate(() => {
        const el = document.activeElement;
        if (!el || el === document.body) return "body";
        return `${el.tagName.toLowerCase()}${el.className ? "." + el.className.split(" ")[0] : ""}`;
      }),
    );
  }
  const want = ["a.skip", "button.chip", "input.scrub", "code", "button.copy"];
  for (const target of want) {
    if (!stops.includes(target)) note(`keyboard: never focused ${target}`);
  }
  await context.close();
}

await browser.close();
server.close();

/* Weight, from the files on disk. */
const size = (p) => statSync(join(dist, p)).size;
console.log("\nweights on disk");
for (const file of [
  "index.html",
  "ru/index.html",
  "guides/index.html",
  "ru/guides/index.html",
  "assets/app.js",
  "assets/og.png",
  "assets/favicon.png",
  "assets/demo/run-en.json",
  "assets/demo/run-ru.json",
]) {
  console.log(`  ${file.padEnd(26)}${(size(file) / 1024).toFixed(1)} KB`);
}

console.log("");
if (problems.length === 0) {
  console.log("audit: all checks passed");
} else {
  console.log(`audit: ${problems.length} problems`);
  for (const problem of problems) console.log(`  - ${problem}`);
  process.exit(1);
}

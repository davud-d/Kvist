#!/usr/bin/env node
/**
 * perf.mjs — LCP, CLS and transferred bytes on a throttled connection.
 *
 *   node build.mjs && node tools/perf.mjs
 *
 * Serves dist/ over HTTP with gzip, throttles the browser to Chrome DevTools'
 * "Fast 3G" profile through CDP, and reports what the first screen actually
 * costs. Numbers in the report come from here, not from a guess.
 */
import { createServer } from "node:http";
import { existsSync, readFileSync } from "node:fs";
import { gzipSync } from "node:zlib";
import { dirname, extname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { resolveRequest } from "./static-path.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const dist = resolve(here, "../dist");
const PORT = 4272;

/* Chrome DevTools "Fast 3G": 1.6 Mbit down, 750 kbit up, 150 ms RTT. */
const THROTTLE = {
  offline: false,
  downloadThroughput: (1.6 * 1024 * 1024) / 8,
  uploadThroughput: (750 * 1024) / 8,
  latency: 150,
};

const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".woff2": "font/woff2",
  ".png": "image/png",
  ".xml": "application/xml; charset=utf-8",
  ".txt": "text/plain; charset=utf-8",
};
const COMPRESSIBLE = new Set([".html", ".js", ".json", ".xml", ".txt"]);

function serve() {
  const server = createServer((request, response) => {
    const file = resolveRequest(dist, request.url);
    if (!file) {
      response.writeHead(403).end();
      return;
    }
    try {
      let body = readFileSync(file);
      const type = extname(file);
      const headers = {
        "content-type": TYPES[type] ?? "application/octet-stream",
        "cache-control": "no-store",
      };
      if (COMPRESSIBLE.has(type) && (request.headers["accept-encoding"] ?? "").includes("gzip")) {
        body = gzipSync(body, { level: 9 });
        headers["content-encoding"] = "gzip";
      }
      headers["content-length"] = String(body.length);
      response.writeHead(200, headers);
      response.end(body);
    } catch {
      response.writeHead(404).end("404");
    }
  });
  /* Only this machine's browser reads it, so only this machine can reach it. */
  return new Promise((done) => server.listen(PORT, "127.0.0.1", () => done(server)));
}

let chromium;
try {
  ({ chromium } = await import("playwright"));
} catch {
  console.log("playwright is not installed — skipping the performance run");
  process.exit(0);
}

const SYSTEM_CHROME = [
  "/usr/bin/google-chrome-stable",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].find((path) => existsSync(path));

const server = await serve();
let browser;
try {
  browser = await chromium.launch(SYSTEM_CHROME ? { executablePath: SYSTEM_CHROME } : {});
} catch (error) {
  console.log(`no usable browser (${error.message.split("\n")[0]}) — skipping`);
  server.close();
  process.exit(0);
}

const rows = [];

for (const path of ["/", "/ru/", "/guides/", "/ru/guides/"]) {
  const context = await browser.newContext({
    viewport: { width: 1366, height: 768 },
    colorScheme: "dark",
  });
  const page = await context.newPage();
  const session = await context.newCDPSession(page);
  await session.send("Network.enable");
  await session.send("Network.emulateNetworkConditions", THROTTLE);

  let transferred = 0;
  const requests = [];
  page.on("response", async (response) => {
    const headers = response.headers();
    const size = Number(headers["content-length"] ?? 0);
    transferred += size;
    requests.push([new URL(response.url()).pathname, size]);
  });

  await page.goto(`http://127.0.0.1:${PORT}${path}`, { waitUntil: "load" });
  // Give LCP and any late layout shift a chance to be recorded.
  await page.waitForTimeout(3500);

  const metrics = await page.evaluate(
    () =>
      new Promise((done) => {
        let lcp = 0;
        let cls = 0;
        new PerformanceObserver((list) => {
          for (const entry of list.getEntries()) lcp = entry.startTime;
        }).observe({ type: "largest-contentful-paint", buffered: true });
        new PerformanceObserver((list) => {
          for (const entry of list.getEntries()) {
            if (!entry.hadRecentInput) cls += entry.value;
          }
        }).observe({ type: "layout-shift", buffered: true });
        const nav = performance.getEntriesByType("navigation")[0];
        const paint = performance.getEntriesByType("paint");
        setTimeout(
          () =>
            done({
              lcp,
              cls,
              fcp: paint.find((p) => p.name === "first-contentful-paint")?.startTime ?? 0,
              domContentLoaded: nav?.domContentLoadedEventEnd ?? 0,
              load: nav?.loadEventEnd ?? 0,
            }),
          250,
        );
      }),
  );

  const external = requests.filter(
    (r) => !r[0].startsWith("/") || r[0].includes("//"),
  );
  rows.push({ path, ...metrics, transferred, requests, external });
  await context.close();
}

await browser.close();
server.close();

const ms = (n) => `${Math.round(n)} ms`;
const kb = (n) => `${(n / 1024).toFixed(1)} KB`;

console.log('THROTTLE: Fast 3G (1.6 Mbit down, 150 ms RTT), 1366x768, cold cache');
console.log("-".repeat(72));
console.log(
  "PAGE".padEnd(8) + "FCP".padEnd(10) + "LCP".padEnd(10) +
  "CLS".padEnd(9) + "LOAD".padEnd(10) + "TRANSFERRED",
);
for (const row of rows) {
  console.log(
    row.path.padEnd(8) +
    ms(row.fcp).padEnd(10) +
    ms(row.lcp).padEnd(10) +
    row.cls.toFixed(3).padEnd(9) +
    ms(row.load).padEnd(10) +
    kb(row.transferred),
  );
}
console.log("-".repeat(72));
console.log("requests on / :");
for (const [path, size] of rows[0].requests) {
  console.log(`  ${path.padEnd(42)}${kb(size)}`);
}

const budget = { lcp: 1500, cls: 0.05 };
const bad = rows.filter((r) => r.lcp > budget.lcp || r.cls > budget.cls);
const offsite = rows.flatMap((r) => r.external);
if (offsite.length) console.log(`\nEXTERNAL REQUESTS: ${offsite.length}`);
else console.log("\nexternal requests: 0");

if (bad.length) {
  console.log(`\nover budget: ${bad.map((r) => r.path).join(", ")}`);
  process.exit(1);
}
console.log("within budget (LCP <= 1.5 s, CLS <= 0.05)");

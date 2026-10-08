// Screenshots of every prototype state at 1440x900 and 390x844, served under the client's own CSP.
// Run from the repo root: PLAYWRIGHT_MODULE=… CHROMIUM_EXECUTABLE=… node docs/web-onboarding/prototype/shoot.mjs
// Fails on a CSP violation, a page error or horizontal scroll. Shots go to KVIST_SHOTS (default: ./shots beside this file).
import { createServer } from 'node:http';
import { readFile, mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const here = path.dirname(fileURLToPath(import.meta.url)), repo = path.resolve(here, '../../..');
const out = process.env.KVIST_SHOTS || path.join(here, 'shots');
const CSP = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'";
const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.woff2': 'font/woff2' };
const server = createServer(async (req, res) => {
  const file = path.join(repo, decodeURIComponent(new URL(req.url, 'http://x').pathname));
  if (!file.startsWith(repo + path.sep)) { res.writeHead(403).end(); return; }
  try { const body = await readFile(file); res.writeHead(200, { 'Content-Type': types[path.extname(file)] || 'application/octet-stream', 'Content-Security-Policy': CSP }).end(body); }
  catch { res.writeHead(404).end(); }
}).listen(0, '127.0.0.1');
await new Promise(r => server.once('listening', r));
const base = `http://127.0.0.1:${server.address().port}/docs/web-onboarding/prototype/`;
const states = (await readFile(path.join(here, 'states.js'), 'utf8'));
const PROTO_STATES = new Function('window', `${states}; return window.PROTO_STATES;`)({});
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE, headless: true });
const problems = [];
try {
  for (const [w, h] of [[1440, 900], [390, 844]]) {
    await mkdir(path.join(out, String(w)), { recursive: true });
    for (const [, name, query] of PROTO_STATES) {
      const context = await browser.newContext({ viewport: { width: w, height: h } });
      await context.route('https://**', r => r.fulfill({ contentType: 'text/html', body: '<title>Registration (stub)</title>stub' }));
      const page = await context.newPage();
      page.on('pageerror', e => problems.push(`${w} ${name}: ${e.message}`));
      page.on('console', m => { if (/Content.Security.Policy|Refused/i.test(m.text())) problems.push(`${w} ${name}: ${m.text()}`); });
      await page.goto(`${base}app.html?${query}`);
      await page.waitForTimeout(1900);
      if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) problems.push(`${w} ${name}: horizontal scroll`);
      await page.screenshot({ path: path.join(out, String(w), `${name}.png`) });
      await context.close();
    }
  }
} finally { await browser.close(); server.close(); }
console.log(`${PROTO_STATES.length * 2} screenshots in ${out}`);
if (problems.length) { console.log(problems.join('\n')); process.exitCode = 1; }

// Keyboard walk of the prototype: the first run, a chip menu, the command menu and the palette, without a mouse.
// Run from the repo root: PLAYWRIGHT_MODULE=… CHROMIUM_EXECUTABLE=… node docs/web-onboarding/prototype/keyboard.mjs
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = []; page.on('pageerror', e => errors.push(e.message));
const active = () => page.evaluate(() => document.activeElement?.id || document.activeElement?.className);
try {
  await page.goto(pathToFileURL(new URL('./app.html', import.meta.url).pathname).href);
  assert.equal(await active(), 'anymodel-key');
  await page.keyboard.insertText('qa-valid-key'); await page.keyboard.press('Enter');
  await page.waitForTimeout(900);
  assert.equal(await active(), 'start');
  await page.keyboard.press('Enter'); await page.waitForTimeout(700);
  assert.equal(await active(), 'prompt');
  assert.equal(await page.locator('#anymodel-key').count(), 0, 'the key field leaves the DOM');
  console.log('PASS first run: paste, Enter checks the key, Enter starts; the key field is gone');

  for (let i = 0; i < 4; i++) await page.keyboard.press('Tab'); // paperclip, /, model, reasoning
  assert.equal(await active(), 'chip-effort');
  await page.keyboard.press('Enter');
  assert(await page.locator('#chip-menu').isVisible());
  assert.equal(await page.evaluate(() => document.activeElement.getAttribute('aria-checked')), 'true');
  await page.keyboard.press('ArrowDown'); await page.keyboard.press('Enter');
  await page.waitForTimeout(800);
  assert.equal(await page.locator('#chip-effort .chip-value').textContent(), 'high');
  assert.equal(await active(), 'chip-effort');
  await page.keyboard.press('Enter'); await page.keyboard.press('Escape');
  assert(await page.locator('#chip-menu').isHidden()); assert.equal(await active(), 'chip-effort');
  console.log('PASS chip menu: Enter opens on the checked item, arrows move, Enter applies, Escape returns focus');

  await page.locator('#prompt').focus(); await page.keyboard.type('/r');
  assert.deepEqual(await page.locator('#slash-menu .menu-label').allTextContents(), ['/reasoning', '/role']);
  await page.keyboard.press('ArrowDown'); await page.keyboard.press('Enter');
  assert.equal(await page.locator('#chip-role').getAttribute('aria-expanded'), 'true');
  await page.keyboard.press('Escape');
  await page.locator('#prompt').fill('see /tmp/notes');
  assert(await page.locator('#slash-menu').isHidden());
  console.log('PASS command menu: filter, arrows, Enter runs; a path inside the text opens nothing');

  await page.keyboard.press('Control+k'); assert(await page.locator('#palette').isVisible());
  await page.keyboard.type('model');
  assert.equal((await page.locator('#palette-results .palette-item span').allTextContents())[0], 'Change model');
  console.log('PASS palette finds Change model');

  const kept = await page.evaluate(() => JSON.stringify({ ...localStorage }) + JSON.stringify({ ...sessionStorage }) + location.href + document.documentElement.outerHTML);
  assert(!kept.includes('qa-valid-key'));
  assert.deepEqual(errors, []);
  console.log('PASS no key in storage, the URL or the DOM; no page errors');
} finally { await browser.close(); }

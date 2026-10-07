// The novice walkthrough repeated after the onboarding work (step 9): the same seven tasks as audit.mjs on a clean
// profile, every step screenshotted. Opening a native select and choosing counts as two actions.
// Run from the repo root: PLAYWRIGHT_MODULE=… CHROMIUM_EXECUTABLE=… node docs/web-onboarding/audit/audit-after.mjs <desktop|mobile>
// Screenshots and log.json go to $KVIST_SHOTS/<mode> (default /tmp/kvist-onboarding-audit-after).
import { spawn } from 'node:child_process';
import { mkdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const mode = process.argv[2] || 'desktop';
const viewport = mode === 'mobile' ? { width: 390, height: 844 } : { width: 1440, height: 900 };
const out = `${process.env.KVIST_SHOTS || '/tmp/kvist-onboarding-audit-after'}/${mode}/`;
await mkdir(out, { recursive: true });
const stand = spawn(process.env.KVIST_TEST_PYTHON || '.venv/bin/python', [fileURLToPath(new URL('./stand.py', import.meta.url))], { stdio: ['ignore', 'pipe', 'pipe'] });
let standErr = ''; stand.stderr.on('data', d => { standErr += d; });
const base = await new Promise((resolve, reject) => { stand.once('exit', c => reject(new Error(`stand exited ${c}: ${standErr}`))); stand.stdout.once('data', d => resolve(d.toString().trim())); });
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE, headless: true });
const context = await browser.newContext({ viewport });
await context.route('https://anymodel.org/**', r => r.fulfill({ contentType: 'text/html', body: '<title>AnyModel register (stub)</title>Registration stub' }));
const page = await context.newPage();
const errors = []; page.on('pageerror', e => errors.push(e.message));
const log = []; let task = null, n = 0;
const shot = async label => { await page.waitForTimeout(300); const file = `${String(log.length).padStart(2, '0')}-${task.id}-${label}.png`; await page.screenshot({ path: out + file }); return file; };
const begin = (id, title) => { task = { id, title, actions: 0, steps: [], notes: [] }; log.push(task); n = 0; };
async function act(label, fn, { counts = 1 } = {}) {
  let error = null;
  try { await fn(); } catch (e) { error = e.message.split('\n')[0]; }
  if (!error) task.actions += counts;
  task.steps.push({ label, counts, error, shot: await shot(`${++n}-${label.replace(/\W+/g, '-').slice(0, 40)}`) });
}
const note = text => task.notes.push(text);
const text = async sel => (await page.locator(sel).first().textContent().catch(() => null))?.trim();
const paste = async (locator, value) => { await locator.focus(); await page.keyboard.insertText(value); await locator.dispatchEvent('paste'); };
try {
  begin('t1', 'Launch from scratch');
  await act('open-url', () => page.goto(base));
  await act('first-run', () => page.getByRole('heading', { name: 'Connect a model' }).waitFor({ timeout: 15000 }), { counts: 0 });
  note(`working folder on screen: ${await text('.setup-path')}`);

  begin('t2', 'Connect a provider (AnyModel, key in hand)');
  await act('paste-key', () => paste(page.locator('#setup-key'), 'qa-valid-key'));
  await act('checked', () => page.getByText('The key works.', { exact: false }).waitFor({ timeout: 15000 }), { counts: 0 });
  note(`key note: ${await text('#setup-key-note')}; Start enabled: ${await page.locator('#setup-start').isEnabled()}`);
  if (await page.locator('#setup-start').isDisabled())
    await act('choose-model', () => page.locator('#setup-model').selectOption('kr/claude-sonnet-4.5'), { counts: 2 });
  await act('start', () => page.locator('#setup-start').click());
  await act('session', () => page.locator('.setup').waitFor({ state: 'detached', timeout: 15000 }), { counts: 0 });
  note(`model chip: ${await text('#model-label')}; focus: ${await page.evaluate(() => document.activeElement.id)}`);

  begin('t3', 'Choose a model');
  await act('model-chip', () => page.locator('#model-selector').click());
  await act('menu', () => page.getByRole('menuitemradio', { name: /^ds\/deepseek-v4-pro/ }).waitFor({ timeout: 15000 }), { counts: 0 });
  note(`menu head: ${await text('.chip-menu .menu-head')}`);
  await act('pick', () => page.getByRole('menuitemradio', { name: /^ds\/deepseek-v4-pro/ }).click());
  await act('saved', () => page.locator('#model-label').getByText('ds/deepseek-v4-pro', { exact: true }).waitFor({ timeout: 15000 }), { counts: 0 });

  begin('t4', 'Change reasoning level');
  note(`reasoning on screen before: ${await text('#effort-chip')}`);
  await act('effort-chip', () => page.locator('#effort-chip').click());
  await act('pick-high', () => page.getByRole('menuitemradio', { name: /^high/ }).click());
  await act('saved', () => page.locator('#effort-chip .chip-value').getByText('high', { exact: true }).waitFor({ timeout: 15000 }), { counts: 0 });

  begin('t5', 'Find the command list');
  await act('type-slash', async () => { await page.locator('#prompt').focus(); await page.keyboard.type('/'); await page.locator('#slash-menu').waitFor({ timeout: 5000 }); });
  note(`commands: ${(await page.locator('#slash-menu .menu-label').allTextContents()).join(' ')}`);
  await act('close', async () => { await page.keyboard.press('Escape'); await page.locator('#prompt').fill(''); }, { counts: 0 });

  begin('t6', 'Send the first task');
  note(`empty session: ${await text('#empty-title')}; starters: ${await page.locator('.starter').count()}`);
  await act('type-task', () => page.locator('#prompt').fill('Explain this project'));
  await act('send', () => page.keyboard.press('Control+Enter'));
  await act('answer', () => page.getByText('a tiny sample project').first().waitFor({ timeout: 60000 }), { counts: 0 });

  begin('t7', 'Change the model mid-session');
  await page.locator('#stop-agent').waitFor({ state: 'hidden', timeout: 30000 }).catch(() => {});
  await act('model-chip', () => page.locator('#model-selector').click());
  note(`menu head: ${await text('.chip-menu .menu-head')}`);
  await act('pick', () => page.getByRole('menuitemradio', { name: /^gpt-5\.6-luna/ }).click());
  const started = Date.now();
  await act('confirmed', () => page.locator('#model-label').getByText('gpt-5.6-luna', { exact: true }).waitFor({ timeout: 30000 }), { counts: 0 });
  await page.locator('#model-selector.pending').waitFor({ state: 'detached', timeout: 30000 }).catch(() => {});
  note(`label after ${Date.now() - started} ms: ${await text('#model-label')}; toast: ${(await page.locator('#toasts').innerText().catch(() => '')).replace(/\n/g, ' / ')}`);
} catch (e) { log.push({ fatal: e.message }); }
finally {
  await writeFile(out + 'log.json', JSON.stringify({ errors, log }, null, 1));
  await context.request.post(`${base}/api/admin/stop`, { headers: { Authorization: 'Bearer qa-only' } }).catch(() => stand.kill());
  await browser.close();
  setTimeout(() => stand.kill(), 1500).unref();
}
for (const t of log) console.log(t.fatal ? `FATAL ${t.fatal}` : `${t.id} ${t.title}: ${t.actions} actions; errors: ${t.steps.filter(s => s.error).map(s => `${s.label}: ${s.error}`).join('; ') || 'none'}\n  ${t.notes.join('\n  ')}`);
console.log('page errors:', errors);

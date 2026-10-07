// Novice walkthrough of the web client: seven tasks, every step screenshotted.
// Written against the client of 2707b56 (before 1c400e8 turned the model field into a list);
// tasks 2, 3 and 7 type into [name=fast], which the list hides on later commits.
// Run from the repo root: PLAYWRIGHT_MODULE=… CHROMIUM_EXECUTABLE=… node docs/web-onboarding/audit/audit.mjs <desktop|mobile>
// Screenshots and log.json go to $KVIST_SHOTS/<mode> (default /tmp/kvist-onboarding-audit).
import { spawn } from 'node:child_process';
import { mkdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const mode = process.argv[2] || 'desktop';
const viewport = mode === 'mobile' ? { width: 390, height: 844 } : { width: 1440, height: 900 };
const out = `${process.env.KVIST_SHOTS || '/tmp/kvist-onboarding-audit'}/${mode}/`;
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
// One action is one click, one filled field or one key press the person makes; waits do not count.
async function act(label, fn, { counts = true } = {}) {
  let error = null;
  try { await fn(); } catch (e) { error = e.message.split('\n')[0]; }
  if (counts && !error) task.actions++;
  task.steps.push({ label, counts, error, shot: await shot(`${++n}-${label.replace(/\W+/g, '-').slice(0, 40)}`) });
  return !error;
}
const note = text => task.notes.push(text);
const text = async sel => (await page.locator(sel).first().textContent().catch(() => null))?.trim();
try {
  begin('t1', 'Launch from scratch');
  await act('open-url', () => page.goto(base));
  await act('app-ready', () => page.locator('#app').waitFor({ timeout: 15000 }), { counts: false });
  note(`startup stage text: ${await text('#startup-stage')}`);
  note(`modal heading on arrival: ${await text('#modal h2, #modal [role=dialog] h2, #modal .modal-card h2')}`);

  begin('t2', 'Connect a provider (AnyModel, key in hand)');
  await act('modal-visible', () => page.locator('#provider-form').waitFor({ timeout: 10000 }), { counts: false });
  note(`provider preselected: ${await page.locator('[name=provider]').inputValue()}`);
  note(`model field value before: "${await page.locator('[name=fast]').inputValue()}"`);
  await act('paste-key', () => page.locator('[data-credential=anymodel_api_key]').fill('qa-valid-key'));
  await act('save-without-model', () => page.getByRole('button', { name: 'Save & continue' }).click());
  note(`after save without model, validity: ${await page.locator('[name=fast]').evaluate(n => n.validationMessage)}`);
  await act('load-models', () => page.getByRole('button', { name: 'Load available models' }).click());
  await act('models-loaded', () => page.locator('#configuration-status').filter({ hasText: /models available|No catalog|Could not/ }).waitFor({ timeout: 10000 }), { counts: false });
  note(`status: ${await text('#configuration-status')}`);
  note(`model control: ${await page.locator('[name=fast]').evaluate(n => `${n.tagName.toLowerCase()} list=${n.getAttribute('list')} options=${document.querySelectorAll('#provider-models option').length}`)}`);
  await act('open-model-field', () => page.locator('[name=fast]').click());
  await act('pick-model', () => page.locator('[name=fast]').fill('kr/claude-sonnet-4.5'));
  await act('save', () => page.getByRole('button', { name: 'Save & continue' }).click());
  await act('modal-closed', () => page.locator('#modal').waitFor({ state: 'hidden', timeout: 10000 }), { counts: false });
  note(`model label: ${await text('#model-label')}`);

  begin('t3', 'Choose a model');
  await act('click-model-selector', () => page.locator('#model-selector').click());
  await act('form', () => page.locator('#provider-form').waitFor(), { counts: false });
  note(`model field shows: "${await page.locator('[name=fast]').inputValue()}", options in datalist: ${await page.locator('#provider-models option').count()}`);
  await act('load-models', () => page.getByRole('button', { name: 'Load available models' }).click());
  note(`status: ${await page.locator('#configuration-status').textContent()}`);
  await act('clear-and-pick', () => page.locator('[name=fast]').fill('ds/deepseek-v4-pro'));
  await act('save-defaults', () => page.getByRole('button', { name: 'Save defaults', exact: true }).click());
  await act('closed', () => page.locator('#modal').waitFor({ state: 'hidden', timeout: 10000 }), { counts: false });
  note(`model label: ${await text('#model-label')}`);

  begin('t4', 'Change reasoning level');
  await act('click-model-selector', () => page.locator('#model-selector').click());
  await act('form', () => page.locator('#provider-form').waitFor(), { counts: false });
  note(`reasoning visible without Advanced: ${await page.locator('[name=effort]').isVisible()}`);
  await act('open-advanced', () => page.locator('#provider-form summary').click());
  await act('pick-high', () => page.locator('[name=effort]').selectOption('high'));
  await act('save-defaults', () => page.getByRole('button', { name: 'Save defaults', exact: true }).click());
  await act('closed', () => page.locator('#modal').waitFor({ state: 'hidden', timeout: 10000 }), { counts: false });
  note(`reasoning shown anywhere on main screen: ${await page.locator('main').evaluate(n => /high|reasoning|effort/i.test(n.innerText))}`);

  begin('t5', 'Find the command list');
  await act('type-slash', () => page.locator('#prompt').fill('/'));
  note(`menu after "/": ${await page.locator('[role=listbox], .slash-menu, .suggestions').count()} elements`);
  await act('clear', () => page.locator('#prompt').fill(''), { counts: false });
  if (mode === 'mobile') await act('open-drawer', () => page.locator('#toggle-sidebar').click());
  note(`Commands button visible: ${await page.locator('#commands-button').isVisible()}`);
  await act('click-commands', () => page.locator('#commands-button').click());
  await act('palette', () => page.locator('#palette').waitFor({ state: 'visible' }), { counts: false });
  note(`palette commands: ${(await page.locator('#palette-results .palette-item span').allTextContents()).join(' | ')}`);
  for (const q of ['model', 'reason', 'role', 'provider']) {
    await page.locator('#palette-input').fill(q);
    note(`palette "${q}": ${(await page.locator('#palette-results .palette-item span').allTextContents()).join(' | ') || 'nothing'}`);
  }
  await act('search-model', () => page.locator('#palette-input').fill('model'), { counts: false });
  await act('escape', () => page.keyboard.press('Escape'), { counts: false });
  await page.locator('#palette').waitFor({ state: 'hidden' }).catch(() => {});
  if (mode === 'mobile') {
    note(`drawer still open after palette: ${await page.locator('#app').evaluate(n => n.classList.contains('sidebar-open'))}`);
    await act('tap-outside-drawer', () => page.mouse.click(370, 420));
    await page.waitForTimeout(400);
  }

  begin('t6', 'Send the first task');
  note(`empty state: ${await text('#empty-state')}`);
  await act('type-task', () => page.locator('#prompt').fill('Explain this project'));
  await act('send', () => page.locator('#send-button').click());
  await act('answer', () => page.getByText('a tiny sample project').first().waitFor({ timeout: 45000 }), { counts: false });
  note(`session title: ${await text('#session-title')}`);

  begin('t7', 'Change the model mid-session');
  await page.evaluate(() => { window.__ev = []; const s = new EventSource('/api/v1/events?after=999999999'); s.onmessage = m => window.__ev.push(m.data.slice(0, 400)); });
  await page.waitForFunction(() => !document.querySelector('#stop-agent') || document.querySelector('#stop-agent').hidden, null, { timeout: 30000 }).catch(() => {});
  await act('click-model-selector', () => page.locator('#model-selector').click());
  await act('form', () => page.locator('#provider-form').waitFor(), { counts: false });
  note(`session buttons: ${(await page.locator('.modal-actions button:visible').allTextContents()).join(' | ')}`);
  await act('pick-model', () => page.locator('[name=fast]').fill('gpt-5.6-luna'));
  await act('use-in-session', () => page.getByRole('button', { name: 'Use model in this session' }).click());
  const started = Date.now();
  await act('confirmed', () => page.waitForFunction(() => document.querySelector('#model-label')?.textContent === 'gpt-5.6-luna', null, { timeout: 30000 }), { counts: false });
  note(`label changed after ${Date.now() - started} ms`);
  note(`events seen: ${(await page.evaluate(() => window.__ev)).filter(e => /command|hello|error/.test(e)).join('\n    ')}`);
  note(`model label: ${await text('#model-label')}`);
} catch (e) { log.push({ fatal: e.message }); }
finally {
  await writeFile(out + 'log.json', JSON.stringify({ errors, log }, null, 1));
  await context.request.post(`${base}/api/admin/stop`, { headers: { Authorization: 'Bearer qa-only' } }).catch(() => stand.kill());
  await browser.close();
  setTimeout(() => stand.kill(), 1500).unref();
}
for (const t of log) console.log(t.fatal ? `FATAL ${t.fatal}` : `${t.id} ${t.title}: ${t.actions} actions; errors: ${t.steps.filter(s => s.error).map(s => `${s.label}: ${s.error}`).join('; ') || 'none'}\n  ${t.notes.join('\n  ')}`);
console.log('page errors:', errors);

// Onboarding and session controls against the real controller and real core processes (core_server.py):
// HTTP, CSRF, SSE, the core's commands and its configuration store are real; only the model provider is a
// loopback fake. Run from the repo root: node frontends/web-local/tests/browser/onboarding.mjs
// It accepts the same Playwright/Chromium arguments and variables as the other scripts.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || process.argv[2] || 'playwright');
const output = process.env.KVIST_QA_OUTPUT || '/tmp/kvist-web-qa';
await mkdir(`${output}/screenshots`, { recursive: true });
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE || process.argv[3], headless: true });
const fixtures = [];

// One isolated controller per scenario: a fresh temporary HOME and workspace.
async function fixture(setup = 'blank') {
  const child = spawn(process.env.KVIST_TEST_PYTHON || '.venv/bin/python', [fileURLToPath(new URL('./core_server.py', import.meta.url))], { stdio: ['ignore', 'pipe', 'inherit'], env: { ...process.env, KVIST_FIXTURE_SETUP: setup } });
  fixtures.push(child);
  const [base, home] = await new Promise((resolve, reject) => {
    let text = '';
    child.once('exit', code => reject(new Error(`Fixture exited: ${code}`)));
    child.stdout.on('data', data => { text += data; const lines = text.split('\n'); if (lines.length > 2) resolve(lines.slice(0, 2)); });
  });
  return { base, home, child };
}
async function open(base, viewport = { width: 1440, height: 900 }) {
  const context = await browser.newContext({ viewport });
  await context.route('https://anymodel.org/**', route => route.fulfill({ contentType: 'text/html', body: '<title>Registration test</title>Registration page' }));
  const page = await context.newPage(), errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.goto(base);
  await page.locator('#app').waitFor();
  return { context, page, errors };
}
async function sendTask(page, text) {
  await page.locator('#prompt').fill(text);
  await page.keyboard.press('Control+Enter');
  await page.getByText('a sample project with one README file').first().waitFor({ timeout: 60000 });
  await page.locator('#stop-agent').waitFor({ state: 'hidden', timeout: 30000 });
}
const shot = (page, name) => page.screenshot({ path: `${output}/screenshots/onboarding-${name}.png` });

try {
  {
    const { base } = await fixture('configured');
    const { context, page, errors } = await open(base);
    await sendTask(page, 'Explain this project');
    await page.locator('#model-selector').click();
    await page.locator('#model-note').getByText('3 models available.').waitFor();
    await page.locator('#model-pick').selectOption('gpt-5.6-luna');
    await page.getByRole('button', { name: 'Use model in this session' }).click();
    await page.locator('#model-label').getByText('gpt-5.6-luna', { exact: true }).waitFor({ timeout: 30000 });
    await page.locator('.toast').getByText('Models updated.').waitFor();
    await shot(page, 'model-switched');
    assert.deepEqual(errors, []);
    await context.close();
    console.log('PASS mid-session model change: the core confirms it and the model label shows it without a reload');
  }
} finally {
  await browser.close();
  for (const child of fixtures) child.kill();
}

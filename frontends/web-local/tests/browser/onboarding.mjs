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
async function fixture(setup = 'blank', env = {}) {
  const child = spawn(process.env.KVIST_TEST_PYTHON || '.venv/bin/python', [fileURLToPath(new URL('./core_server.py', import.meta.url))], { stdio: ['ignore', 'pipe', 'inherit'], env: { ...process.env, KVIST_FIXTURE_SETUP: setup, ...env } });
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
// A paste: the text lands in the focused field and the field gets its paste event.
async function paste(page, locator, text) {
  await locator.focus();
  await page.keyboard.insertText(text);
  await locator.dispatchEvent('paste');
}

try {
  // The first run. One action is one click, one paste, one typed text or one key press.
  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
    const { base } = await fixture('blank', { KVIST_FIXTURE_DEFAULT_MODELS: 'missing/model,gpt-5.6-luna' });
    const { context, page, errors } = await open(base, viewport);
    await page.getByRole('heading', { name: 'Connect a model' }).waitFor();
    assert.match(await page.locator('.setup-path').textContent(), /my-project$/, 'the working folder is on screen before Start');
    assert.equal(await page.evaluate(() => document.activeElement.id), 'setup-key');
    await shot(page, `first-run-${viewport.width}`);
    await paste(page, page.locator('#setup-key'), 'qa-valid-key');                       // 1
    await page.getByText('The key works. 3 models available.').waitFor();
    assert.equal(await page.locator('#setup-model').inputValue(), 'gpt-5.6-luna', 'the first default the catalog offers');
    assert.equal(await page.evaluate(() => document.activeElement.id), 'setup-start');
    await shot(page, `first-run-ready-${viewport.width}`);
    await page.keyboard.press('Enter');                                                  // 2
    await page.locator('.setup').waitFor({ state: 'detached' });
    await page.keyboard.insertText('Explain this project');                              // 3
    await page.keyboard.press('Control+Enter');                                          // 4
    await page.getByText('a sample project with one README file').first().waitFor({ timeout: 60000 });
    assert.equal(await page.locator('#model-label').textContent(), 'gpt-5.6-luna');
    await shot(page, `first-task-${viewport.width}`);
    assert.deepEqual(errors, []);
    await context.close();
    console.log(`PASS ${viewport.width}px: key to first task in 4 actions with a default model: paste, Start, type, send`);
  }
  {
    const { base } = await fixture('blank');
    const { context, page, errors } = await open(base);
    await page.addInitScript(() => { window.open = () => null; });
    await page.reload(); await page.getByRole('heading', { name: 'Connect a model' }).waitFor();
    await page.getByRole('button', { name: 'Create an AnyModel account' }).click();
    await page.getByText('Your browser blocked the new tab.', { exact: false }).waitFor();
    assert.equal(await page.getByRole('link', { name: 'anymodel.org/app/register' }).getAttribute('href'), 'https://anymodel.org/app/register');
    await shot(page, 'first-run-tab-blocked');
    console.log('PASS a blocked registration tab gives the address as a link');

    const key = page.locator('#setup-key'), start = page.getByRole('button', { name: 'Start' });
    await paste(page, key, 'qa-offline');
    await page.getByText("Couldn't reach AnyModel. Check your connection, then try again.").waitFor();
    await key.fill(''); await paste(page, key, 'qa-wrong');
    await page.getByText('AnyModel did not accept this key. Copy it again from your AnyModel account.').waitFor();
    assert(await start.isDisabled());
    console.log('PASS an unreachable AnyModel and a refused key are told apart; nothing can start');

    await key.fill(''); await paste(page, key, 'qa-empty-catalog-key');
    await page.getByText('The key works. AnyModel returned no model list, so enter the model ID.').waitFor();
    assert(await page.locator('#setup-model-field').isHidden());
    assert.equal(await page.evaluate(() => document.activeElement.id), 'setup-id');
    await shot(page, 'first-run-empty-catalog');
    await page.keyboard.insertText('kr/claude-sonnet-4.5');
    assert(await start.isEnabled());
    console.log('PASS an empty model list gives a model ID field');

    await page.getByRole('button', { name: 'I have another provider' }).click();
    await page.locator('#setup-provider').selectOption('deepseek');
    assert((await page.locator('#setup-model option').allTextContents()).includes('DeepSeek V4 Pro'), 'built-in options before a key');
    await paste(page, page.locator('#setup-key'), 'qa-valid-key');
    await page.getByText('The key works. 3 models available.').waitFor();
    await page.locator('#setup-model').selectOption('ds-web/deepseek-v4-pro');
    await shot(page, 'first-run-other');
    await start.click();
    await page.locator('.setup').waitFor({ state: 'detached' });
    await sendTask(page, 'Explain this project');
    const info = await (await context.request.get(`${base}/api/v1/configuration?server=local`)).json();
    assert.equal(info.provider, 'deepseek'); assert.equal(info.setup_required, false);
    assert.deepEqual(errors, []);
    await context.close();
    console.log('PASS another provider: DeepSeek with its live list, saved and answering');
  }
  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
    const { base } = await fixture('configured');
    const { context, page, errors } = await open(base, viewport);
    const empty = page.locator('#empty-state');
    await empty.getByRole('heading', { name: 'What should Kvist do in my-project?' }).waitFor();
    assert.equal(await empty.locator('.starter').count(), 4);
    assert(await empty.getByText('for commands, such as changing the model.').isVisible());
    assert.equal(await page.locator('#prompt').getAttribute('placeholder'), 'Message Kvist, or type / for commands');
    await page.locator('#model-label').getByText('kr/claude-sonnet-4.5', { exact: true }).waitFor();
    await page.locator('#effort-chip .chip-value').getByText('medium', { exact: true }).waitFor();
    await shot(page, `empty-session-${viewport.width}`);
    await empty.getByRole('button', { name: 'Review my uncommitted changes' }).click();
    assert.equal(await page.locator('#prompt').inputValue(), 'Review my uncommitted changes');
    assert.equal(await page.evaluate(() => document.activeElement.id), 'prompt');
    assert(await page.locator('#more-tools').isVisible(), 'the palette has a visible button in the header');
    assert.equal((await page.locator('#more-tools span').isVisible()), viewport.width > 640, 'the word shows where there is room');
    await page.locator('#more-tools').click();
    await page.locator('#palette').waitFor();
    for (const label of ['Change model', 'Change reasoning', 'Change role', 'Providers and keys'])
      assert(await page.locator('#palette-results .palette-item span', { hasText: label }).first().isVisible(), label);
    await page.locator('#palette-input').fill('role'); await page.keyboard.press('Enter');
    await page.locator('#role-chip[aria-expanded="true"]').waitFor();
    await page.keyboard.press('Escape');
    assert.deepEqual(errors, []);
    await context.close();
    console.log(`PASS ${viewport.width}px: empty session with starters and the "/" hint; the header button opens the palette with model, reasoning and role`);
  }
  {
    const { base } = await fixture('configured');
    const { context, page, errors } = await open(base);
    await sendTask(page, 'Explain this project');
    const model = page.locator('#model-selector');
    assert.equal(await model.getAttribute('aria-label'), 'Model: kr/claude-sonnet-4.5');
    await model.click();
    await page.locator('.chip-menu .menu-head').getByText('AnyModel models. Applies to this session and new ones.').waitFor();
    await page.getByRole('menuitemradio', { name: /^gpt-5\.6-luna/ }).waitFor();
    assert.equal(await page.getByRole('menuitemradio', { name: /^kr\/claude-sonnet-4\.5/ }).getAttribute('aria-checked'), 'true');
    await shot(page, 'model-menu');
    await page.getByRole('menuitemradio', { name: /^gpt-5\.6-luna/ }).click();
    await page.locator('#model-label').getByText('gpt-5.6-luna', { exact: true }).waitFor({ timeout: 30000 });
    await page.locator('.toast').getByText('Models updated.').waitFor();
    await page.locator('#model-selector.pending').waitFor({ state: 'detached' });
    await shot(page, 'model-switched');
    console.log('PASS mid-session model change: the core confirms it and the model chip shows it without a reload');

    for (const [chip, value, toastText] of [['#effort-chip', 'high', /high/i], ['#role-chip', 'reader', /reader/i]]) {
      await page.locator(chip).click();
      assert(await page.locator('.chip-menu .menu-desc').first().isVisible(), 'every item has a one-line caption');
      await page.getByRole('menuitemradio', { name: new RegExp(`^${value}`) }).click();
      await page.locator(`${chip} .chip-value`).getByText(value, { exact: true }).waitFor();
      await page.locator('.toast').getByText(toastText).first().waitFor({ timeout: 30000 });
      await page.locator(`${chip}.pending`).waitFor({ state: 'detached' });
    }
    await shot(page, 'chips-changed');
    const info = await (await context.request.get(`${base}/api/v1/configuration?server=local`)).json();
    assert.equal(info.effort, 'high'); assert.equal(info.role, 'reader');
    console.log('PASS reasoning and role chips change the running session through the core and save the choice');

    // Keyboard: Enter opens on the checked item, arrows move, Escape closes and returns focus.
    await page.locator('#role-chip').focus(); await page.keyboard.press('Enter');
    assert.equal(await page.evaluate(() => document.activeElement.getAttribute('aria-checked')), 'true');
    await page.keyboard.press('ArrowDown'); await page.keyboard.press('Escape');
    assert(await page.locator('.chip-menu').isHidden());
    assert.equal(await page.evaluate(() => document.activeElement.id), 'role-chip');
    console.log('PASS chip menus work from the keyboard');
    assert.deepEqual(errors, []);
    await context.close();
  }
  {
    const { base } = await fixture('configured');
    const { context, page, errors } = await open(base);
    const sends = [];
    page.on('request', request => { if (request.url().includes('/api/v1/send')) sends.push(request.postData()); });
    const prompt = page.locator('#prompt'), menu = page.locator('#slash-menu');
    await prompt.click(); await page.keyboard.type('/');
    await menu.waitFor();
    assert.equal(await prompt.getAttribute('aria-expanded'), 'true');
    assert.deepEqual((await menu.locator('.menu-label').allTextContents()).slice(0, 2), ['/model', '/reasoning']);
    await shot(page, 'slash-menu');
    await page.keyboard.type('mo');
    assert.deepEqual(await menu.locator('.menu-label').allTextContents(), ['/model']);
    await page.keyboard.press('Enter');
    await page.locator('#model-selector[aria-expanded="true"]').waitFor(); // /model opens the model chip's menu
    assert.equal(await prompt.inputValue(), '');
    await page.keyboard.press('Escape'); assert(await page.locator('.chip-menu').isHidden());
    console.log('PASS "/" opens the command menu, typing filters it, Enter runs the command');

    await prompt.fill('/xyz'); await page.keyboard.press('Control+Enter');
    await menu.getByText('Unknown command /xyz. Nothing was sent. Type / to see the commands.').waitFor();
    assert.equal(await prompt.inputValue(), '/xyz');
    await shot(page, 'slash-unknown');
    await prompt.fill('/modelz please'); await page.keyboard.press('Control+Enter');
    await menu.getByText('Unknown command /modelz.', { exact: false }).waitFor();
    await page.keyboard.press('Escape');
    assert(await menu.isHidden());
    assert.equal(await prompt.inputValue(), '/modelz please');
    console.log('PASS an unknown command shows a hint, keeps the text and sends nothing');

    // A Russian layout gives Cyrillic letters on the command keys: "/ьщвуд" is "/model".
    await prompt.fill(''); await page.keyboard.insertText('/ьщ');
    assert.deepEqual(await menu.locator('.menu-label').allTextContents(), ['/model']);
    await prompt.fill('/ьщвуд'); await page.keyboard.press('Control+Enter');
    await page.locator('#model-selector[aria-expanded="true"]').waitFor();
    await page.keyboard.press('Escape'); assert(await page.locator('.chip-menu').isHidden());
    console.log('PASS a command typed on the Russian layout opens the menu and runs');

    await prompt.fill('/tmp/notes is broken');
    assert(await menu.isHidden());
    await page.locator('#slash-button').click();
    await page.locator('#palette').waitFor();
    await page.keyboard.press('Escape');
    await prompt.fill(''); await page.locator('#slash-button').click();
    assert.equal(await prompt.inputValue(), '/'); await menu.waitFor();
    console.log('PASS a path is no command; the "/" button opens the menu on an empty input and the palette otherwise');
    assert.deepEqual(sends, [], 'no command reached the agent');
    assert.deepEqual(errors, []);
    await context.close();
  }
} finally {
  await browser.close();
  for (const child of fixtures) child.kill();
}

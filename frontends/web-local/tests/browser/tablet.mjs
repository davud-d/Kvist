// The phone page on a tablet: shape by window size (phone, upright, lying down), the request moving between
// the conversation and the desk, the desk's tabs, answering from the desk and the hardware keyboard.
// Serves the client from disk itself and mocks the API in the page, so it needs no running Kvist:
//   node frontends/web-local/tests/browser/tablet.mjs   (PLAYWRIGHT_MODULE / CHROMIUM_EXECUTABLE as in README.md)
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { dirname, extname, join, normalize } from 'node:path';
import { fileURLToPath } from 'node:url';
import { fixture } from './fixtures.mjs';

const client = join(dirname(fileURLToPath(import.meta.url)), '../../kvist_web/client');
const playwrightModule = process.env.PLAYWRIGHT_MODULE || process.argv[2] || 'playwright';
const executablePath = process.env.CHROMIUM_EXECUTABLE || process.argv[3] || undefined;
const { chromium } = await import(playwrightModule);
const TYPES = { '.js': 'text/javascript', '.css': 'text/css', '.png': 'image/png', '.woff2': 'font/woff2', '.svg': 'image/svg+xml' };

const server = createServer(async (request, response) => {
    const path = new URL(request.url, 'http://x').pathname;
    try {
        if (path === '/m/') {
            const page = (await readFile(join(client, 'mobile/index.html'), 'utf8')).replace('{{KVIST_CSRF}}', 'test');
            response.writeHead(200, { 'Content-Type': 'text/html' }).end(page);
            return;
        }
        const file = normalize(join(client, path.replace(/^\/assets\//, '')));
        if (!path.startsWith('/assets/') || !file.startsWith(client)) throw new Error('not an asset');
        response.writeHead(200, { 'Content-Type': TYPES[extname(file)] || 'application/octet-stream' }).end(await readFile(file));
    } catch {
        response.writeHead(404).end();
    }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${server.address().port}`;

const fx = fixture();
const request = { type: 'input_request', request_id: 'approval-1', kind: 'confirm', title: 'Run the test suite?', body: 'Kvist needs permission.', subject: { kind: 'command', command: 'npm test -- --runInBand', reason: 'Verify the changes' } };
const CHANGES = 'diff --git a/a.ts b/a.ts\n--- a/a.ts\n+++ b/a.ts\n@@ -1,2 +1,2 @@\n-old\n+new\n same';
const sel = session => `#/local/${encodeURIComponent(fx.workspace)}/${session}`;

const browser = await chromium.launch(executablePath ? { executablePath } : {});
const failures = [];
async function scenario(name, body) {
    const context = await browser.newContext({ viewport: { width: 1180, height: 820 } });
    const page = await context.newPage();
    const errors = [];
    const posts = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/api/v1/**', route => {
        const url = new URL(route.request().url());
        const path = url.pathname.replace('/api/v1', '');
        const json = value => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ ok: true, ...value }) });
        if (route.request().method() === 'POST') posts.push([path, route.request().postData()]);
        if (path === '/bootstrap') return json({ navigation: fx.inbox(), event_stream: { cursor: 0, epoch: '' } });
        if (path === '/navigation') return json(fx.inbox());
        if (path === '/changes') return json({ report: CHANGES });
        if (path === '/events') return route.fulfill({ contentType: 'text/event-stream', body: ': open\n\n' });
        if (path === '/history') {
            const session = url.searchParams.get('session');
            const events = [...(fx.histories[session] || [])];
            if (session === 'waiting') events.push({ seq: events.length + 1, turn: 1, ts: 1780000100, event: request });
            return json({ events, has_more: false, closed: false });
        }
        return json({});
    });
    try {
        await body(page, posts);
        assert.deepEqual(errors, [], 'no script errors');
        console.log(`ok   ${name}`);
    } catch (error) {
        failures.push(name);
        console.log(`FAIL ${name}\n${error.stack}`);
    } finally {
        await context.close();
    }
}
async function open(page, hash = '') {
    await page.goto(`${base}/m/${hash}`);
    await page.waitForSelector('#boot', { state: 'hidden' });
}
const layout = page => page.evaluate(() => document.documentElement.dataset.layout);
const holder = page => page.evaluate(() => document.querySelector('#request').parentElement.className);
const resize = async (page, width, height) => { await page.setViewportSize({ width, height }); await page.waitForTimeout(150); };

await scenario('the shape follows the window: phone, upright tablet, tablet lying down, phone on its side', async page => {
    await open(page);
    assert.equal(await layout(page), 'wide');
    await resize(page, 820, 1180);
    assert.equal(await layout(page), 'medium');
    await resize(page, 390, 844);
    assert.equal(await layout(page), 'narrow');
    await resize(page, 844, 390);
    assert.equal(await layout(page), 'narrow', 'wide but short is still a phone');
});

await scenario('lying down: sessions, conversation and desk side by side; the open session is marked', async page => {
    await open(page, sel('review'));
    for (const selector of ['#home', '#chat', '#desk']) assert.equal(await page.locator(selector).isVisible(), true, selector);
    const boxes = await Promise.all(['#home', '#chat', '#desk'].map(selector => page.locator(selector).boundingBox()));
    assert.ok(boxes[0].x < boxes[1].x && boxes[1].x < boxes[2].x, 'left to right');
    assert.equal(await page.locator('#back').isHidden(), true);
    assert.equal(await page.locator('.session[aria-current="true"]').count(), 1);
});

await scenario('nothing open: a placeholder in the conversation, what waits for you in the desk', async page => {
    await open(page);
    assert.equal(await page.locator('#pick').isVisible(), true);
    assert.equal(await page.locator('#composer').isHidden(), true);
    assert.equal(await page.locator('.tab[aria-selected="true"]').getAttribute('data-alert') === null, false, 'the needs tab is the open one and shows the alert');
});

await scenario('activity: every call with its whole output, a call can be picked', async page => {
    await open(page, sel('review'));
    await page.locator('.tab', { hasText: 'ACTIVITY' }).click();
    assert.equal(await page.locator('.desk-call').count(), 3);
    assert.match(await page.locator('.desk-call').first().textContent(), /npm test/, 'newest first');
    assert.match(await page.locator('.desk-output').textContent(), /24 tests passed/, 'the newest call is shown');
    await page.locator('.desk-call').nth(2).click();
    assert.match(await page.locator('.desk-output').textContent(), /recover\(\)/, 'the picked call is shown');
    assert.equal(await page.locator('.desk-call.current').count(), 1);
});

await scenario('changes: the report is coloured', async page => {
    await open(page, sel('review'));
    await page.locator('.tab', { hasText: 'CHANGES' }).click();
    await page.waitForSelector('.desk-report .diff-row.add');
    assert.equal(await page.locator('.desk-report .diff-row.add').count(), 1);
    assert.equal(await page.locator('.desk-report .diff-row.del').count(), 1);
});

await scenario('a request: in the desk when wide, above the composer otherwise; answered from the desk', async (page, posts) => {
    await open(page, sel('waiting'));
    await page.waitForSelector('#desk .sheet:not([hidden])');
    assert.equal(await holder(page), 'desk-request');
    assert.equal(await page.locator('.tab[aria-selected="true"]').textContent().then(text => text.startsWith('NEEDS')), true, 'the tab opens by itself');
    await resize(page, 820, 1180);
    assert.equal(await holder(page), 'screen chat');
    assert.equal(await page.locator('#request').isVisible(), true);
    await resize(page, 390, 844);
    assert.equal(await page.locator('#home').isHidden(), true, 'a phone shows the conversation alone');
    assert.equal(await page.locator('#request').isVisible(), true);
    await resize(page, 1180, 820);
    assert.equal(await holder(page), 'desk-request');
    assert.equal(await page.locator('.tab[aria-selected="true"]').textContent().then(text => text.startsWith('NEEDS')), true, 'rotated back: the request is first');
    await page.locator('#desk .btn.primary', { hasText: 'Allow' }).click();
    await page.waitForTimeout(250);
    const answer = posts.find(([path]) => path === '/respond');
    assert.ok(answer, 'the answer was sent');
    assert.match(answer[1], /"request_id":"approval-1"/);
    assert.match(answer[1], /"approved":true/);
    assert.equal(await page.locator('#request').isHidden(), true);
});

await scenario('upright: the desk is a drawer', async page => {
    await resize(page, 820, 1180);
    await open(page, sel('review'));
    assert.equal(await page.locator('#desk').isHidden(), true);
    await page.locator('#desk-toggle').click();
    assert.equal(await page.locator('#desk').isVisible(), true);
    assert.equal(await page.locator('.tab', { hasText: 'NEEDS' }).isHidden(), true, 'no needs tab: the request stays in the conversation');
    await page.locator('.desk-close').click();
    assert.equal(await page.locator('#desk').isHidden(), true);
});

await scenario('a phone is unchanged: one screen, no desk, no keyboard shortcuts', async page => {
    await resize(page, 390, 844);
    await open(page);
    assert.equal(await page.locator('#chat').isHidden(), true);
    assert.equal(await page.locator('#desk').isHidden(), true);
    await page.keyboard.press('Control+k');
    assert.equal(await page.locator('#menu').isHidden(), true);
    await page.goto(`${base}/m/${sel('review')}`);
    await page.waitForSelector('#boot', { state: 'hidden' });
    assert.equal(await page.locator('#home').isHidden(), true);
    assert.equal(await page.locator('#back').isVisible(), true);
});

await scenario('hardware keyboard: Ctrl/Cmd+K opens the commands, Escape closes them, Ctrl+Enter sends', async (page, posts) => {
    await open(page, sel('review'));
    await page.keyboard.press('Control+k');
    assert.equal(await page.locator('#menu').isVisible(), true);
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#menu').isHidden(), true);
    await page.locator('#prompt').fill('run the tests');
    await page.locator('#prompt').press('Control+Enter');
    await page.waitForTimeout(250);
    assert.ok(posts.some(([path, body]) => path === '/send' && /run the tests/.test(body)));
});

await browser.close();
server.close();
if (failures.length) {
    console.log(`\n${failures.length} failed: ${failures.join('; ')}`);
    process.exit(1);
}

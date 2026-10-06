// Feed row states: feeds prepared event sequences through blocks.js and feed.js, imported in the page from
// the asset server (/assets/features/*.js), and takes a screenshot of each state at 1440x900 and 390x844.
// PLAYWRIGHT_MODULE=... CHROMIUM_EXECUTABLE=... KVIST_WEB_URL=http://127.0.0.1:PORT node .../feed-states.mjs
// KVIST_SHOTS sets the screenshot directory (default /tmp/kvist-shots/3b).
import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { fixture } from './fixtures.mjs';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || process.argv[2] || 'playwright');
const base = process.env.KVIST_WEB_URL || 'http://127.0.0.1:9672';
const shots = process.env.KVIST_SHOTS || '/tmp/kvist-shots/3b';
await mkdir(shots, { recursive: true });
const browser = await chromium.launch({ headless: true, ...((process.env.CHROMIUM_EXECUTABLE || process.argv[3]) ? { executablePath: process.env.CHROMIUM_EXECUTABLE || process.argv[3] } : {}) });
const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
const errors = [], checks = [];
page.on('pageerror', e => errors.push(e.message));
page.on('dialog', dialog => { errors.push(`dialog: ${dialog.message()}`); dialog.dismiss(); });
const f = fixture();
await page.addInitScript(() => {
    window.__sources = [];
    window.EventSource = class {
        constructor(url) { this.url = url; window.__sources.push(this); setTimeout(() => this.onopen?.({}), 0); }
        close() {}
        emit(data) { this.onmessage?.({ data: JSON.stringify(data) }); }
    };
    // Active timers, to prove the feed keeps one only while a thought streams.
    window.__timers = new Set();
    const set = window.setInterval, clear = window.clearInterval;
    window.setInterval = (...args) => { const id = set.apply(window, args); window.__timers.add(id); return id; };
    window.clearInterval = id => { window.__timers.delete(id); return clear.call(window, id); };
});
await page.route('**/api/v1/**', async route => {
    const url = new URL(route.request().url()), path = url.pathname.replace('/api/v1', '');
    const json = value => route.fulfill({ json: value });
    if (path === '/bootstrap') return json({ protocol_version: 1, navigation: f.inbox(), event_stream: { cursor: 0, epoch: 'e' } });
    if (path === '/navigation') return json(f.inbox());
    if (path === '/history') return json({ events: [], has_more: false });
    if (path === '/configuration') return json({ provider: 'p', setup_required: false, providers: [], custom: [] });
    return json({ ok: true });
});

// --- event builders (to_dict() of kernel/frontend_protocol.py) -------------------------------------
const T0 = 1780000000;
let seq = 0;
const rec = (event, ts = T0) => ({ seq: ++seq, ts, event });
const user = (text, ts) => rec({ type: 'user', text }, ts);
const step = (n, call_id, action_type, action_args, batch_id = '', ts) => rec({ type: 'step', step: n, plan_step: 'inspect', thought: '', user_message: '', action_type, action_args, reasoning_seconds: 0, action_description: '', call_id, batch_id }, ts);
const result = (n, call_id, action_type, extra = {}, ts) => rec({ type: 'action_result', step: n, action_type, ok: true, summary: '', output: '', exit_code: null, path: null, first_line: null, call_id, batch_id: '', ...extra }, ts);
const turnEnd = ts => rec({ type: 'turn_end' }, ts);
const LONG_PATH = `src/${'very-long-directory-name/'.repeat(8)}module.ts`;
const OUTPUT = Array.from({ length: 60 }, (_, i) => `line ${i + 1}: test output`).join('\n');

const SCENARIOS = {
    'tool-running': () => [user('Run the tests.'), step(1, 'c1', 'run_shell', { command: ['npm', 'test'] }, '', T0)],
    'tool-ok': () => [user('Run the tests.'), step(1, 'c1', 'run_shell', { command: ['npm', 'test'] }, '', T0), result(1, 'c1', 'run_shell', { exit_code: 0, summary: '24 tests passed', output: 'PASS session recovery\n24 tests passed' }, T0 + 12)],
    'tool-failed': () => [user('Run the tests.'), step(1, 'c1', 'run_shell', { command: ['npm', 'test'] }, '', T0), result(1, 'c1', 'run_shell', { ok: false, exit_code: 1, output: 'FAIL session recovery\n  expected 3, got 2' }, T0 + 65)],
    'tool-ok-output': () => [user('Run the tests.'), step(1, 'c1', 'run_shell', { command: ['npm', 'test'] }, '', T0), result(1, 'c1', 'run_shell', { exit_code: 0, output: OUTPUT }, T0 + 0.4)],
    'tool-long-target': () => [user('Open it.'), step(1, 'c1', 'read_file', { path: LONG_PATH }, '', T0), result(1, 'c1', 'read_file', { output: 'export {};' }, T0 + 1)],
    'batch-folded': () => [user('Look around.'), step(1, 'c1', 'read_file', { path: 'src/a.ts' }, 'b1', T0), step(2, 'c2', 'search_code', { query: 'recover' }, 'b1', T0), step(3, 'c3', 'run_shell', { command: ['git', 'status'] }, 'b1', T0),
        result(1, 'c1', 'read_file', { output: 'a' }, T0 + 1), result(2, 'c2', 'search_code', { output: 'b' }, T0 + 2), result(3, 'c3', 'run_shell', { exit_code: 0, output: 'clean' }, T0 + 3), turnEnd(T0 + 4)],
    'batch-open': () => [user('Look around.'), step(1, 'c1', 'read_file', { path: 'src/a.ts' }, 'b1', T0), step(2, 'c2', 'search_code', { query: 'recover' }, 'b1', T0), step(3, 'c3', 'run_shell', { command: ['git', 'status'] }, 'b1', T0),
        result(1, 'c1', 'read_file', { output: 'a' }, T0 + 1), result(2, 'c2', 'search_code', { ok: false, output: 'no rg' }, T0 + 2)],
    'reasoning-streaming': now => [user('Think about it.'), rec({ type: 'reasoning_start', stream_id: 's1' }, now - 5), rec({ type: 'reasoning_chunk', stream_id: 's1', text: 'The cursor is saved before the reconnect, so', append: true }, now - 4)],
    'reasoning-done': () => [user('Think about it.'), rec({ type: 'reasoning_start', stream_id: 's1' }), rec({ type: 'reasoning_chunk', stream_id: 's1', text: 'The cursor is saved before the reconnect,\nso the replay starts after it.' }), rec({ type: 'reasoning_commit', stream_id: 's1', duration_seconds: 12 }), rec({ type: 'message', markdown: 'Done thinking.' })],
    'plan': () => [user('Plan it.'), rec({ type: 'plan', markdown: '1. Save the cursor\n2. Replay missed events\n3. Verify with tests' })],
    'notices': () => [user('Go.'), rec({ type: 'journal_notice', omitted_turns: 3 }), rec({ type: 'sandbox_notice', note: 'Network is blocked in the sandbox.' }), rec({ type: 'error', message: 'The workspace is unavailable. Check that its directory is still mounted.' })],
    'user-long': () => [user(`Make session recovery reliable. ${'The connection drops while a turn is running and the cursor must survive. '.repeat(14)}\n\nSecond paragraph of the request.`)],
    'hostile-text': () => [user('Go.'), step(1, 'c1', 'run_shell', { command: ['echo', '<img src=x onerror=alert(1)>'] }, '', T0), result(1, 'c1', 'run_shell', { ok: false, exit_code: 2, output: '<img src=x onerror=alert(2)><script>alert(3)</script>' }, T0 + 1),
        rec({ type: 'reasoning_start', stream_id: 'h' }), rec({ type: 'reasoning_chunk', stream_id: 'h', text: '<img src=x onerror=alert(4)>' }), rec({ type: 'reasoning_commit', stream_id: 'h', duration_seconds: 2 }),
        rec({ type: 'error', message: '<img src=x onerror=alert(5)>' })],
};

// --- page side -------------------------------------------------------------------------------------
await page.goto(base);
await page.locator('#history-status').waitFor({ state: 'hidden' });
await page.evaluate(async () => {
    const blocks = await import('/assets/features/blocks.js');
    const { createFeed } = await import('/assets/features/feed.js');
    document.querySelector('#conversation').hidden = true;
    document.querySelector('#empty-state').hidden = true;
    const box = document.createElement('section');
    box.className = 'conversation';
    box.id = 'feed-under-test';
    document.querySelector('.conversation-wrap').append(box);
    window.__fs = { blocks, createFeed, box, feed: null, state: null };
});
const reset = () => page.evaluate(() => {
    const fs = window.__fs;
    fs.feed?.destroy();
    fs.box.replaceChildren();
    fs.feed = fs.createFeed(fs.box);
    fs.state = fs.blocks.initialState();
});
const push = records => page.evaluate(records => {
    const fs = window.__fs;
    for (const record of records) fs.state = fs.blocks.reduce(fs.state, record);
    fs.feed.render(fs.state);
}, records);
const show = async (name, now = Date.now() / 1000) => { seq = 0; await reset(); await push(SCENARIOS[name](now)); };
const timers = () => page.evaluate(() => window.__timers.size);
const rows = () => page.evaluate(() => [...document.querySelectorAll('#feed-under-test > *')].map(n => n.className));
// The computed color a design token paints, to compare with what a row really uses.
const token = name => page.evaluate(name => { const n = document.createElement('i'); n.style.color = `var(--${name})`; document.body.append(n); const c = getComputedStyle(n).color; n.remove(); return c; }, name);
const colorOf = (selector, pseudo) => page.locator(selector).first().evaluate((n, pseudo) => getComputedStyle(n, pseudo || null).color, pseudo);
const shot = async name => {
    await page.screenshot({ path: `${shots}/${name}-1440x900.png` });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: `${shots}/${name}-390x844.png` });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${name}: horizontal scroll at 390px`);
    await page.setViewportSize({ width: 1440, height: 900 });
};
const check = async (name, fn) => { await fn(); checks.push(name); console.log('PASS', name); };

try {
    await check('tool running: ring, name, target, no time', async () => {
        await show('tool-running');
        assert.deepEqual(await rows(), ['message user', 'tool-block']);
        const row = page.locator('.tool').first();
        assert(await row.evaluate(n => n.classList.contains('tool-running')));
        assert.equal(await page.locator('.tool-label').textContent(), 'Bash');
        assert.equal(await page.locator('.tool-target').textContent(), 'npm test');
        assert.equal(await page.locator('.tool-time').textContent(), '');
        const ring = await page.locator('.tool summary').first().evaluate(n => { const s = getComputedStyle(n, '::before'); return { animation: s.animationName, w: s.width, h: s.height, radius: s.borderTopLeftRadius, border: s.borderTopWidth }; });
        assert.deepEqual([ring.animation, ring.w, ring.h, ring.border], ['spin', '12px', '12px', '2px']);
        await shot('tool-running');
    });

    await check('tool ok: check, label --text at 500, target --text-3 and kept, time 12s, closed', async () => {
        await show('tool-ok');
        const summary = page.locator('.tool summary').first();
        assert.equal(await summary.evaluate(n => getComputedStyle(n, '::before').content), '"✓"');
        assert.equal(await page.locator('.tool-target').textContent(), 'npm test', 'the target stays after the result');
        assert.equal(await page.locator('.tool-time').textContent(), '12s');
        assert.equal(await page.locator('.tool-status').evaluate(n => getComputedStyle(n).display), 'none', 'exit 0 shows no code');
        assert.equal(await colorOf('.tool-label'), await token('text'));
        assert.equal(await colorOf('.tool-target'), await token('text-3'));
        const font = await page.locator('.tool-label').evaluate(n => { const s = getComputedStyle(n); return [s.fontFamily.split(',')[0], s.fontSize, s.fontWeight]; });
        assert.deepEqual(font, ['"JetBrains Mono"', '12.5px', '500']);
        assert.equal(await page.locator('.tool').first().evaluate(n => n.open), false);
        assert.equal(await summary.evaluate(n => n.getBoundingClientRect().height), 24);
        assert.equal(await page.locator('.tool pre').first().isVisible(), false, 'the output is hidden while closed');
        await shot('tool-ok');
    });

    await check('tool failed: cross, exit code, time 1m 05s, open by default, output scrolls', async () => {
        await show('tool-failed');
        const summary = page.locator('.tool summary').first();
        assert.equal(await summary.evaluate(n => getComputedStyle(n, '::before').content), '"✕"');
        assert.equal(await page.locator('.tool-status').textContent(), 'exit 1');
        assert.equal(await colorOf('.tool-status'), await token('err'));
        assert.equal(await page.locator('.tool-time').textContent(), '1m 05s');
        assert.equal(await page.locator('.tool').first().evaluate(n => n.open), true);
        assert.match(await page.locator('.tool pre').first().textContent(), /expected 3, got 2/);
        await shot('tool-failed');
    });

    await check('tool ok with output: opens on click, max-height 320px with scroll, sub-second time', async () => {
        await show('tool-ok-output');
        assert.equal(await page.locator('.tool-time').textContent(), '0.4s');
        await page.locator('.tool summary').first().click();
        const pre = page.locator('.tool pre').first();
        assert(await pre.isVisible());
        const box = await pre.evaluate(n => { const s = getComputedStyle(n); return { max: s.maxHeight, overflow: s.overflowY, font: s.fontFamily.split(',')[0], scrolls: n.scrollHeight > n.clientHeight, h: n.getBoundingClientRect().height }; });
        assert.deepEqual([box.max, box.overflow, box.font, box.scrolls], ['320px', 'auto', '"JetBrains Mono"', true]);
        assert(box.h <= 320);
        await shot('tool-ok-output-open');
    });

    await check('long path: the head of the directory is cut by width, the file and its parent stay whole, the full path is the title', async () => {
        await show('tool-long-target');
        const parts = async () => page.locator('.tool-target').evaluate(n => {
            const dir = n.querySelector('.tool-dir'), file = n.querySelector('.tool-file'), dirBox = dir.getBoundingClientRect(), fileBox = file.getBoundingClientRect(), box = n.getBoundingClientRect();
            return { file: file.textContent, fileWhole: file.scrollWidth <= Math.ceil(fileBox.width), dirCut: dir.scrollWidth > dir.clientWidth, overflow: getComputedStyle(dir).textOverflow, direction: getComputedStyle(dir).direction,
                dirText: dir.textContent, title: n.title, fits: fileBox.right <= box.right + 0.5 && dirBox.left >= box.left - 0.5, tail: dir.querySelector('bdi').textContent.endsWith('/') };
        });
        for (const width of [1440, 390]) {
            await page.setViewportSize({ width, height: 900 });
            const p = await parts();
            assert.equal(p.file, 'module.ts');
            assert(p.fileWhole && p.fits && p.tail, `${width}: ${JSON.stringify(p)}`);
            assert.equal(p.title, LONG_PATH);
            assert.equal(p.dirText, LONG_PATH.slice(0, LONG_PATH.lastIndexOf('/') + 1));
            assert.deepEqual([p.dirCut, p.overflow, p.direction], [true, 'ellipsis', 'rtl'], `${width}: the directory is cut`);
        }
        // the visible part of the directory is its tail: the last pixels of the directory text show the parent
        const visibleParent = await page.locator('.tool-dir').evaluate(n => { const r = document.createRange(); r.selectNodeContents(n.querySelector('bdi')); const b = r.getBoundingClientRect(), box = n.getBoundingClientRect(); return b.right <= box.right + 0.5 && b.left < box.left; });
        assert(visibleParent, 'the directory text overflows on the left, not on the right');
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
        await page.setViewportSize({ width: 1440, height: 900 });
        await shot('tool-long-target');
    });

    await check('long command: cut to 80 characters from the end with an ellipsis, the full line is the title', async () => {
        seq = 0;
        await reset();
        const command = `npm run build -- ${'--flag-with-a-long-name '.repeat(8)}done`;
        await push([user('x'), step(1, 'c1', 'run_shell', { command: ['bash', '-c', command] }, '', T0), result(1, 'c1', 'run_shell', { exit_code: 0 }, T0 + 1)]);
        const text = await page.locator('.tool-text').textContent();
        assert.equal(Array.from(text).length, 80);
        assert(text.endsWith('…') && text.startsWith('bash -c npm run build'));
        assert.equal(await page.locator('.tool-dir').evaluate(n => n.textContent), '');
        assert.equal(await page.locator('.tool-target').getAttribute('title'), `bash -c ${command}`.replace(/\s+/g, ' ').trim());
        await page.setViewportSize({ width: 390, height: 844 });
        assert(await page.locator('.tool-text').evaluate(n => n.scrollWidth > n.clientWidth && getComputedStyle(n).textOverflow === 'ellipsis'));
        await page.setViewportSize({ width: 1440, height: 900 });
        await shot('tool-long-command');
    });

    await check('batch of three: one "3 calls" row, folded when all ok and the turn ended', async () => {
        await show('batch-folded');
        assert.deepEqual(await rows(), ['message user', 'tool-block']);
        assert.equal(await page.locator('.tool-group > summary .tool-label').textContent(), '3 calls');
        assert.equal(await page.locator('.tool-group').evaluate(n => n.open), false);
        assert.equal(await page.locator('.tool-group > summary .tool-time').textContent(), '3s');
        assert.equal(await page.locator('.tool-group').evaluate(n => getComputedStyle(n.querySelector('summary'), '::before').content), '"✓"');
        await shot('batch-folded');
        await page.locator('.tool-group > summary').click();
        assert.equal(await page.locator('.tool-group > .tool-group-body > .tool').count(), 3);
        const indent = await page.evaluate(() => { const g = document.querySelector('.tool-group > summary').getBoundingClientRect(), c = document.querySelector('.tool-group-body > .tool > summary').getBoundingClientRect(); return c.x - g.x; });
        assert.equal(indent, 20, 'the calls are indented under the group row');
        await shot('batch-folded-opened');
    });

    await check('batch with a running or failed call is open; a running call does not make its finished rows spin', async () => {
        await show('batch-open');
        assert.equal(await page.locator('.tool-group').evaluate(n => n.open), true);
        assert(await page.locator('.tool-group').evaluate(n => n.classList.contains('tool-running')));
        const marks = await page.locator('.tool-group-body > .tool > summary').evaluateAll(list => list.map(n => getComputedStyle(n, '::before').animationName));
        assert.deepEqual(marks, ['none', 'none', 'spin']);
        await shot('batch-open');
        // all ok but the turn goes on: still open
        await push([result(3, 'c3', 'run_shell', { exit_code: 0, output: 'clean' }, T0 + 3)]);
        assert.equal(await page.locator('.tool-group').evaluate(n => n.classList.contains('tool-failed')), true, 'a failed call fails the group');
    });

    await check('reasoning streaming: "thinking Ns" in sans 13px --text-3, ticks once a second, one timer', async () => {
        const now = Date.now() / 1000;
        await reset();
        const baseline = await timers();
        await show('reasoning-streaming', now);
        const label = page.locator('.reasoning-label');
        const first = await label.textContent();
        assert.match(first, /^thinking [5-6]s$/);
        assert.equal(await colorOf('.reasoning summary'), await token('text-3'));
        assert.deepEqual(await page.locator('.reasoning summary').evaluate(n => { const s = getComputedStyle(n); return [s.fontFamily.includes('Mono'), s.fontSize]; }), [false, '13px']);
        assert.equal(await timers() - baseline, 1);
        await page.waitForTimeout(2300);
        const later = Number((await label.textContent()).match(/\d+/)[0]);
        assert(later >= Number(first.match(/\d+/)[0]) + 2, `the label ticked: ${first} -> ${await label.textContent()}`);
        // a second thought streaming does not add a timer
        await push([rec({ type: 'reasoning_start', stream_id: 's2' }, now)]);
        assert.equal(await timers() - baseline, 1);
        await shot('reasoning-streaming');
        await push([rec({ type: 'reasoning_commit', stream_id: 's1', duration_seconds: 7 }, now), rec({ type: 'reasoning_commit', stream_id: 's2', duration_seconds: 1 }, now)]);
        assert.equal(await timers() - baseline, 0, 'the timer is cleared when no thought streams');
        const settled = await label.first().textContent();
        await page.waitForTimeout(1300);
        assert.equal(await label.first().textContent(), settled, 'a finished label does not tick');
    });

    await check('reasoning finished: "thought for 12s", opens to the text, in order', async () => {
        await show('reasoning-done');
        assert.deepEqual(await rows(), ['message user', 'reasoning', 'message ']);
        assert.equal(await page.locator('.reasoning-label').textContent(), 'thought for 12s');
        assert.equal(await page.locator('.reasoning').evaluate(n => n.open), false);
        const chevron = () => page.locator('.reasoning .chevron').evaluate(n => { const s = getComputedStyle(n), b = n.getBoundingClientRect(); return { w: b.width, h: b.height, transform: s.transform, color: s.color, visibility: s.visibility, stroke: n.querySelector('path').getAttribute('stroke') }; });
        const closed = await chevron();
        assert.deepEqual([closed.w, closed.h, closed.transform, closed.stroke], [10, 10, 'none', 'currentColor']);
        assert.equal(closed.color, await token('text-3'));
        await page.locator('.reasoning summary').click();
        assert.match(await page.locator('.reasoning-body').textContent(), /replay starts after it\./);
        assert(await page.locator('.reasoning-body').isVisible());
        assert.equal((await chevron()).transform, 'matrix(0, 1, -1, 0, 0, 0)', 'the chevron turns a quarter when the row opens');
        const body = await page.locator('.reasoning-body').evaluate(n => { const s = getComputedStyle(n); return { color: s.color, mono: s.fontFamily.includes('Mono'), size: s.fontSize, left: n.getBoundingClientRect().x }; });
        assert.equal(body.color, await token('text-2'));
        assert.deepEqual([body.mono, body.size], [false, '13px']);
        // the summary starts at the left edge of the other blocks and the body sits under the text of the summary
        const edges = await page.evaluate(() => { const box = document.querySelector('#feed-under-test').getBoundingClientRect(), user = document.querySelector('.message.user').getBoundingClientRect(), chevron = document.querySelector('.reasoning .chevron').getBoundingClientRect(), label = document.querySelector('.reasoning-label').getBoundingClientRect(), body = document.querySelector('.reasoning-body').getBoundingClientRect(); return { user: user.x, chevron: chevron.x - 1, label: label.x, body: body.x + 20 }; });
        assert.equal(edges.chevron, edges.user, 'the summary starts at the left edge of the column');
        assert.equal(edges.body, edges.label, 'the body sits under the text of the summary');
        await shot('reasoning-done');
    });

    await check('reasoning with no text has no body and does not open', async () => {
        await reset();
        seq = 0;
        await push([user('x'), rec({ type: 'reasoning_start', stream_id: 'e' }), rec({ type: 'reasoning_commit', stream_id: 'e', duration_seconds: 3 })]);
        assert.equal(await page.locator('.reasoning-label').textContent(), 'thought for 3s');
        await page.locator('.reasoning summary').click({ force: true });
        assert.equal(await page.locator('.reasoning').evaluate(n => n.open), false);
        assert.equal(await page.locator('.reasoning-body').isVisible(), false);
        assert.equal(await page.locator('.reasoning summary').getAttribute('tabindex'), '-1');
    });

    await check('plan carries a "plan" label 11px --text-3, lowercase and not transformed', async () => {
        await show('plan');
        const label = page.locator('.message-label');
        assert.equal(await label.textContent(), 'plan');
        assert.equal(await colorOf('.message-label'), await token('text-3'));
        const s = await label.evaluate(n => { const c = getComputedStyle(n); return [c.fontSize, c.textTransform]; });
        assert.deepEqual(s, ['11px', 'none']);
        assert.match(await page.locator('.message-body').last().textContent(), /Replay missed events/);
        await shot('plan');
    });

    await check('notices: error --err with a cross, others --text-3, 12px mono; no connection rows', async () => {
        await show('notices');
        assert.deepEqual(await rows(), ['message user', 'notice', 'notice', 'notice notice-error']);
        const texts = await page.locator('.notice-text').allTextContents();
        assert.deepEqual(texts, ['3 earlier turns are not shown', 'Network is blocked in the sandbox.', 'The workspace is unavailable. Check that its directory is still mounted.']);
        assert.equal(await colorOf('.notice-error'), await token('err'));
        assert.equal(await colorOf('.notice:not(.notice-error)'), await token('text-3'));
        assert.equal(await page.locator('.notice-error').evaluate(n => getComputedStyle(n, '::before').content), '"✕"');
        assert.deepEqual(await page.locator('.notice').first().evaluate(n => { const s = getComputedStyle(n); return [s.fontSize, s.fontFamily.split(',')[0]]; }), ['12px', '"JetBrains Mono"']);
        const before = (await rows()).length;
        await push([rec({ type: 'connection.lost', message: 'down' }, T0), rec({ type: 'connection.restored', recovered_events: 2 }, T0), rec({ type: 'connection.state_changed', state: 'failed', error: 'x' }, T0)]);
        assert.equal((await rows()).length, before, 'connection.* are not in the feed');
        await shot('notices');
    });

    await check('long user message wraps inside the column and keeps its 2px --line-strong bar', async () => {
        await show('user-long');
        const body = page.locator('.message.user .message-body');
        const style = await body.evaluate(n => { const s = getComputedStyle(n); return [s.borderLeftWidth, s.borderLeftColor, s.paddingLeft]; });
        assert.deepEqual(style, ['2px', await token('line-strong'), '12px']);
        assert(await body.evaluate(n => n.scrollWidth <= n.clientWidth));
        await shot('user-long');
    });

    await check('event text never becomes markup', async () => {
        await show('hostile-text');
        assert.equal(await page.locator('#feed-under-test img, #feed-under-test script').count(), 0);
        assert.match(await page.locator('.tool pre').first().textContent(), /<script>alert\(3\)<\/script>/);
        assert.match(await page.locator('.reasoning-body').textContent(), /<img src=x/);
        assert.match(await page.locator('.notice-error').textContent(), /<img src=x/);
        assert.deepEqual(errors, []);
        await page.locator('.reasoning summary').click();
        await shot('hostile-text');
    });

    await check('keyboard: Enter and Space open and close a row, focus is visible, the row stays open on new events', async () => {
        await show('tool-ok-output');
        await page.locator('#feed-under-test .tool summary').first().focus();
        await page.keyboard.press('Shift+Tab');
        await page.keyboard.press('Tab');
        const focused = () => page.evaluate(() => { const n = document.activeElement; const s = getComputedStyle(n); return { tag: n.tagName, outline: s.outlineStyle, width: s.outlineWidth, color: s.outlineColor, inFeed: !!n.closest('#feed-under-test') }; });
        const at = await focused();
        assert.deepEqual([at.tag, at.inFeed, at.outline, at.width, at.color], ['SUMMARY', true, 'solid', '2px', await token('accent')]);
        const open = () => page.locator('#feed-under-test .tool').first().evaluate(n => n.open);
        await page.keyboard.press('Enter');
        assert.equal(await open(), true);
        await page.keyboard.press('Space');
        assert.equal(await open(), false);
        await page.keyboard.press('Space');
        assert.equal(await open(), true);
        await shot('keyboard-focus-open');

        // New events leave the opened row open and untouched; only the rows that changed or are new are written.
        await page.evaluate(() => {
            const box = document.querySelector('#feed-under-test');
            [...box.children].forEach((child, i) => { child.dataset.probe = `row${i}`; });
            window.__changed = new Set();
            const rowOf = node => { while (node && node.parentElement !== box) node = node.parentElement; return node; };
            new MutationObserver(list => list.forEach(m => window.__changed.add(rowOf(m.target)?.dataset?.probe ?? 'new'))).observe(box, { childList: true, subtree: true, characterData: true, attributes: true });
        });
        await push([step(2, 'c2', 'read_file', { path: 'src/b.ts' }, '', T0 + 2), rec({ type: 'message', markdown: 'Next.' }, T0 + 3)]);
        assert.equal(await open(), true, 'an event closed the row the person opened');
        assert.equal(await page.locator('#feed-under-test .tool').first().evaluate(n => n.dataset.probe ?? n.parentElement.dataset.probe), 'row1', 'the row was replaced');
        assert.deepEqual(await page.evaluate(() => [...window.__changed].sort()), ['new'], 'rows that did not change were written');

        // the batch grows into a group: the call the person opened keeps its state inside it
        await reset();
        seq = 0;
        await push([user('x'), step(1, 'c1', 'run_shell', { command: ['npm', 'test'] }, 'bx', T0), result(1, 'c1', 'run_shell', { exit_code: 0, output: OUTPUT, batch_id: 'bx' }, T0 + 1)]);
        await page.locator('#feed-under-test .tool summary').first().click();
        await push([step(2, 'c2', 'read_file', { path: 'src/b.ts' }, 'bx', T0 + 2)]);
        assert.equal(await page.locator('#feed-under-test .tool-group').count(), 1);
        assert.equal(await page.locator('#feed-under-test .tool-group-body > .tool').count(), 2);
        assert.equal(await page.locator('#feed-under-test .tool-group-body > .tool').first().evaluate(n => n.open), true);
    });

    await check('the person keeps the last word: a row they opened and closed does not reopen when the default changes', async () => {
        await show('tool-running');
        const row = page.locator('#feed-under-test .tool').first();
        await row.locator('summary').click();
        assert.equal(await row.evaluate(n => n.open), true);
        await row.locator('summary').click();
        assert.equal(await row.evaluate(n => n.open), false);
        // a failed call with output is open by default; the person's closing is the last word
        await push([result(1, 'c1', 'run_shell', { ok: false, exit_code: 1, output: 'FAIL' }, T0 + 2)]);
        assert(await row.evaluate(n => n.classList.contains('tool-failed')));
        assert.equal(await row.evaluate(n => n.open), false);
        // and the same call, never touched, opens by itself
        await reset();
        seq = 0;
        await push([user('x'), step(1, 'c1', 'run_shell', { command: ['npm', 'test'] }, '', T0), result(1, 'c1', 'run_shell', { ok: false, exit_code: 1, output: 'FAIL' }, T0 + 2)]);
        assert.equal(await page.locator('#feed-under-test .tool').first().evaluate(n => n.open), true);
        // a running group is open by default; once the person folds it, results and the end of the turn leave it folded
        await reset();
        seq = 0;
        await push(SCENARIOS['batch-open']().slice(0, 5));
        const group = page.locator('#feed-under-test .tool-group');
        assert.equal(await group.evaluate(n => n.open), true);
        await group.locator('> summary').click();
        assert.equal(await group.evaluate(n => n.open), false);
        await push([result(2, 'c2', 'search_code', { output: 'b' }, T0 + 2), result(3, 'c3', 'run_shell', { exit_code: 1, ok: false, output: 'x' }, T0 + 3), turnEnd(T0 + 4)]);
        assert.equal(await group.evaluate(n => n.open), false, 'an event reopened a group the person folded');
    });

    await check('reduced motion: the ring stays, the spin stops', async () => {
        await show('tool-running');
        await page.emulateMedia({ reducedMotion: 'reduce' });
        const ring = await page.locator('.tool summary').first().evaluate(n => { const s = getComputedStyle(n, '::before'); return { animation: s.animationName, w: s.width, border: s.borderTopWidth, style: s.borderTopStyle, content: s.content }; });
        assert.deepEqual([ring.animation, ring.w, ring.border, ring.style, ring.content], ['none', '12px', '2px', 'solid', '""']);
        await shot('tool-running-reduced-motion');
        await page.emulateMedia({ reducedMotion: 'no-preference' });
    });

    await check('column: 760px at 1440 with 24px side padding, 16px up to 800px, no horizontal scroll', async () => {
        await show('tool-ok');
        await push([rec({ type: 'message', markdown: 'An answer in the column.' }, T0 + 20)]);
        const measure = () => page.evaluate(() => {
            const box = document.querySelector('#feed-under-test'), row = box.querySelector('.message:last-child'), field = document.querySelector('#composer');
            const b = row.getBoundingClientRect(), c = field.getBoundingClientRect(), main = box.getBoundingClientRect(), p = getComputedStyle(box);
            return { row: [Math.round(b.x), Math.round(b.width)], field: [Math.round(c.x), Math.round(c.width)], padding: [p.paddingLeft, p.paddingRight], main: [Math.round(main.x), Math.round(main.width)], scroll: document.documentElement.scrollWidth <= innerWidth };
        });
        for (const [width, height] of [[1440, 900], [1280, 720]]) {
            await page.setViewportSize({ width, height });
            const m = await measure();
            assert.equal(m.row[1], 760, `${width}: text column ${JSON.stringify(m)}`);
            assert.equal(m.field[1], 760, `${width}: input ${JSON.stringify(m)}`);
            assert.equal(m.row[0], m.field[0], `${width}: the column and the input share a left edge`);
            assert.deepEqual(m.padding, ['24px', '24px']);
            assert(m.scroll, `${width}: horizontal scroll`);
        }
        await page.setViewportSize({ width: 790, height: 700 });
        const narrow = await measure();
        assert.deepEqual(narrow.padding, ['16px', '16px']);
        assert(narrow.row[1] <= 790 - 32 && narrow.scroll, JSON.stringify(narrow));
        assert.equal(narrow.row[0], narrow.field[0], `790: the text keeps the left edge of the input ${JSON.stringify(narrow)}`);
        await page.setViewportSize({ width: 390, height: 844 });
        const phone = await measure();
        assert.deepEqual(phone.padding, ['16px', '16px']);
        assert(phone.scroll && phone.row[0] + phone.row[1] <= 390 - 16 + 1, JSON.stringify(phone));
        assert.equal(phone.row[0], phone.field[0], `390: the text keeps the left edge of the input ${JSON.stringify(phone)}`);
        await page.setViewportSize({ width: 1440, height: 900 });
        await shot('column');
    });

    assert.deepEqual(errors, []);
    console.log(`PASS ${checks.length} feed state scenarios, screenshots in ${shots}`);
} finally {
    await browser.close();
}

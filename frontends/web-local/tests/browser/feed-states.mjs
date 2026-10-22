// Feed row states: feeds prepared event sequences through blocks.js and feed.js, imported in the page from
// the asset server (/assets/features/*.js), and takes a screenshot of each state at 1440x900 and 390x844.
// PLAYWRIGHT_MODULE=... CHROMIUM_EXECUTABLE=... KVIST_WEB_URL=http://127.0.0.1:PORT node .../feed-states.mjs
// KVIST_SHOTS sets the screenshot directory (default /tmp/kvist-shots/3e); the input states go to its input/
// directory unless KVIST_SHOTS_INPUT says otherwise.
import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { inflateSync } from 'node:zlib';
import { fixture } from './fixtures.mjs';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || process.argv[2] || 'playwright');
const base = process.env.KVIST_WEB_URL || 'http://127.0.0.1:9672';
const shots = process.env.KVIST_SHOTS || '/tmp/kvist-shots/3e';
await mkdir(shots, { recursive: true });
const browser = await chromium.launch({ headless: true, ...((process.env.CHROMIUM_EXECUTABLE || process.argv[3]) ? { executablePath: process.env.CHROMIUM_EXECUTABLE || process.argv[3] } : {}) });
const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
const errors = [], checks = [];
page.on('pageerror', e => errors.push(e.message));
page.on('dialog', dialog => { errors.push(`dialog: ${dialog.message()}`); dialog.dismiss(); });
const f = fixture();
// Hover, focus and press colors are transitions of --dur-fast. The scenarios assert end values, so a computed
// style read first finishes the transitions that are running (motion-states.mjs tests the motion itself).
const finishTransitionsOnRead = () => {
    const read = window.getComputedStyle;
    window.getComputedStyle = (...args) => { for (const a of document.getAnimations()) if (a instanceof CSSTransition) a.finish(); return read.apply(window, args); };
};
await page.addInitScript(finishTransitionsOnRead);
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
    if (path === '/configuration') return json({ provider: 'p', setup_required: false, providers: [], custom_providers: [] });
    return json({ ok: true });
});

// --- event builders (to_dict() of kernel/protocol/frontend_protocol.py) -------------------------------------
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
    // one call with output and one without; 1.9s, 2s and a failed 0.3s call for the time rule
    'rows-output': () => [user('Look.'), step(1, 'c1', 'read_file', { path: 'src/a.ts' }, '', T0), result(1, 'c1', 'read_file', { output: 'export {};' }, T0 + 5),
        step(2, 'c2', 'run_shell', { command: ['mkdir', 'out'] }, '', T0 + 5), result(2, 'c2', 'run_shell', { exit_code: 0 }, T0 + 5.3)],
    'times': () => [user('Time.'), step(1, 'c1', 'read_file', { path: 'src/a.ts' }, '', T0), result(1, 'c1', 'read_file', { output: 'a' }, T0 + 1.9),
        step(2, 'c2', 'read_file', { path: 'src/b.ts' }, '', T0 + 10), result(2, 'c2', 'read_file', { output: 'b' }, T0 + 12),
        step(3, 'c3', 'run_shell', { command: ['false'] }, '', T0 + 20), result(3, 'c3', 'run_shell', { ok: false, exit_code: 1 }, T0 + 20.3)],
    'group-fast': () => [user('Quick.'), step(1, 'c1', 'read_file', { path: 'src/a.ts' }, 'bq', T0), step(2, 'c2', 'search_code', { query: 'x' }, 'bq', T0),
        result(1, 'c1', 'read_file', { output: 'a' }, T0 + 0.4), result(2, 'c2', 'search_code', { output: 'b' }, T0 + 0.8), turnEnd(T0 + 1)],
    'group-failed': () => [user('Look around.'), step(1, 'c1', 'read_file', { path: 'src/a.ts' }, 'bf', T0), step(2, 'c2', 'read_file', { path: 'src/b.ts' }, 'bf', T0), step(3, 'c3', 'search_code', { query: 'recover' }, 'bf', T0), step(4, 'c4', 'run_shell', { command: ['git', 'status'] }, 'bf', T0),
        result(1, 'c1', 'read_file', { output: 'a' }, T0 + 0.2), result(2, 'c2', 'read_file', { output: 'b' }, T0 + 0.4), result(3, 'c3', 'search_code', { ok: false, output: 'no rg' }, T0 + 0.6), result(4, 'c4', 'run_shell', { exit_code: 0, output: 'clean' }, T0 + 1.5), turnEnd(T0 + 2)],
    'group-many-names': () => [user('Everything.'), ...[['read_file', { path: 'a.ts' }], ['list_dir', { path: 'src' }], ['search_code', { query: 'x' }], ['run_shell', { command: ['ls'] }], ['web_search', { query: 'y' }], ['fetch_url', { url: 'https://example.com' }],
        ['delegate_task', { task: 'z' }], ['write_file', { path: 'b.ts' }], ['find_files', { pattern: '*.ts' }], ['read_multiple_files', { paths: ['a.ts'] }], ['git_status', {}]]
        .flatMap(([type, args], i) => [step(i + 1, `m${i}`, type, args, 'bm', T0), result(i + 1, `m${i}`, type, { output: 'ok' }, T0 + 3)]), turnEnd(T0 + 4)],
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
// A screenshot taken right after a viewport change can catch the content of a freshly drawn row unpainted:
// messages are content-visibility: auto (tool rows were too, until the feed jank fix), and the browser decides
// again whether they are skipped one frame after the resize. Waiting for the loaded navigation and two frames
// makes every screenshot whole.
const frames = target => target.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
const settled = async target => {
    await target.locator('#navigation .nav-title').first().waitFor();
    await frames(target);
};
const shot = async name => {
    await settled(page);
    await page.screenshot({ path: `${shots}/${name}-1440x900.png` });
    await page.setViewportSize({ width: 390, height: 844 });
    await settled(page);
    await page.screenshot({ path: `${shots}/${name}-390x844.png` });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${name}: horizontal scroll at 390px`);
    await page.setViewportSize({ width: 1440, height: 900 });
};
const check = async (name, fn) => { await fn(); checks.push(name); console.log('PASS', name); };
// The sRGB value of a design token as the canvas rasterizes it, to find the token's color in a screenshot.
const rgbOf = name => page.evaluate(name => { const c = document.createElement('canvas'); c.width = c.height = 1; const g = c.getContext('2d'); g.fillStyle = getComputedStyle(document.documentElement).getPropertyValue(`--${name}`).trim(); g.fillRect(0, 0, 1, 1); return [...g.getImageData(0, 0, 1, 1).data].slice(0, 3); }, name);
// Decodes the 8-bit, non-interlaced PNG that page.screenshot returns into { at(x, y) -> [r, g, b] }.
function decodePng(buffer) {
    let offset = 8, width = 0, height = 0, channels = 0;
    const parts = [];
    while (offset < buffer.length) {
        const length = buffer.readUInt32BE(offset), type = buffer.toString('latin1', offset + 4, offset + 8), data = buffer.subarray(offset + 8, offset + 8 + length);
        if (type === 'IHDR') { width = data.readUInt32BE(0); height = data.readUInt32BE(4); channels = data[9] === 6 ? 4 : 3; assert.deepEqual([data[8], data[12]], [8, 0], 'PNG is 8-bit and not interlaced'); }
        if (type === 'IDAT') parts.push(data);
        offset += length + 12;
    }
    const raw = inflateSync(Buffer.concat(parts)), stride = width * channels, out = Buffer.alloc(stride * height);
    for (let y = 0; y < height; y++) {
        const filter = raw[y * (stride + 1)], line = raw.subarray(y * (stride + 1) + 1, (y + 1) * (stride + 1));
        for (let x = 0; x < stride; x++) {
            const a = x >= channels ? out[y * stride + x - channels] : 0, b = y ? out[(y - 1) * stride + x] : 0, c = x >= channels && y ? out[(y - 1) * stride + x - channels] : 0;
            const p = a + b - c, pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c);
            out[y * stride + x] = (line[x] + [0, a, b, (a + b) >> 1, pa <= pb && pa <= pc ? a : pb <= pc ? b : c][filter]) & 255;
        }
    }
    return { width, height, at: (x, y) => [...out.subarray(y * stride + x * channels, y * stride + x * channels + 3)] };
}

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

    await check('tool ok with output: opens on click, max-height 320px with scroll, no time under 2s', async () => {
        await show('tool-ok-output');
        assert.equal(await page.locator('.tool-time').textContent(), '', 'a 0.4s call shows no time');
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
        assert.equal(await page.locator('.tool-group .tool-group-body > .tool').count(), 3);
        const indent = await page.evaluate(() => { const g = document.querySelector('.tool-group > summary').getBoundingClientRect(), c = document.querySelector('.tool-group-body > .tool > summary').getBoundingClientRect(); return c.x - g.x; });
        assert.equal(indent, 16, 'the calls are indented 16px under the group row');
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

    await check('tool rows: a chevron, hover fill and pointer only when the call has output to open', async () => {
        await show('rows-output');
        const summaries = page.locator('#feed-under-test .tool > summary');
        const look = () => summaries.evaluateAll(list => list.map(n => ({ chevrons: n.querySelectorAll('.chevron').length, cursor: getComputedStyle(n).cursor, tab: n.tabIndex, fill: getComputedStyle(n).backgroundColor, label: n.querySelector('.tool-label').getBoundingClientRect().x, isStatic: n.parentElement.classList.contains('tool-static') })));
        const [withOutput, plain] = await look();
        assert.deepEqual([withOutput.chevrons, withOutput.cursor, withOutput.tab, withOutput.isStatic], [1, 'pointer', 0, false]);
        assert.deepEqual([plain.chevrons, plain.tab, plain.isStatic], [0, -1, true]);
        assert.notEqual(plain.cursor, 'pointer', 'a row without output is not clickable');
        assert.equal(plain.label, withOutput.label, 'the labels of both rows stand in one column');
        await page.mouse.move(700, 500);
        assert.equal((await look())[0].fill, 'rgba(0, 0, 0, 0)', 'no fill without the pointer');
        await summaries.nth(0).hover();
        assert.equal((await look())[0].fill, await token('bg-2'), 'a row with output is filled with --bg-2 under the pointer');
        await summaries.nth(1).hover();
        assert.equal((await look())[1].fill, 'rgba(0, 0, 0, 0)', 'a row without output is not filled under the pointer');
        await summaries.nth(1).click();
        assert.equal(await page.locator('#feed-under-test .tool').nth(1).evaluate(n => n.open), false, 'a row without output does not open');
        assert.equal(await page.locator('#feed-under-test .tool').nth(1).locator('.chevron').count(), 0);
        const turn = () => page.locator('#feed-under-test .tool .chevron').first().evaluate(n => getComputedStyle(n).transform);
        assert.equal(await turn(), 'none');
        await summaries.nth(0).click();
        assert.equal(await turn(), 'matrix(0, 1, -1, 0, 0, 0)', 'the chevron turns a quarter when the row opens');
        await page.mouse.move(700, 500);
        await shot('rows-output');
    });

    await check('tool time: shown from 2s and for a failed call, hidden for a faster success', async () => {
        await show('times');
        assert.deepEqual(await page.locator('#feed-under-test .tool-time').allTextContents(), ['', '2s', '0.3s'], '1.9s ok, 2s ok, 0.3s failed');
        assert.equal(await page.locator('#feed-under-test .tool-time').nth(2).isVisible(), true);
        assert.equal(await page.locator('#feed-under-test .tool-status').nth(2).textContent(), 'exit 1');
        await shot('times');
    });

    await check('group header: status, chevron, "N calls" in --text-2 at 400, the distinct names in --text-3', async () => {
        await show('batch-folded');
        const head = page.locator('.tool-group > summary');
        const at = await head.evaluate(n => { const x = s => n.querySelector(s).getBoundingClientRect().x, r = n.getBoundingClientRect(); return { mark: getComputedStyle(n, '::before').content, chevron: x('.chevron') - r.x, label: x('.tool-label') - r.x, target: x('.tool-target') - r.x, cursor: getComputedStyle(n).cursor }; });
        assert.equal(at.mark, '"✓"');
        assert(at.chevron >= 6 + 12 + 8 && at.chevron < at.label && at.label < at.target, `status, chevron, text, names in that order: ${JSON.stringify(at)}`);
        assert.equal(at.cursor, 'pointer');
        assert.equal(await page.locator('.tool-group > summary .tool-label').textContent(), '3 calls');
        assert.equal(await colorOf('.tool-group > summary .tool-label'), await token('text-2'));
        assert.equal(await page.locator('.tool-group > summary .tool-label').evaluate(n => getComputedStyle(n).fontWeight), '400');
        assert.equal(await page.locator('.tool-group > summary .tool-text').textContent(), 'Read, Search, Bash');
        assert.equal(await colorOf('.tool-group > summary .tool-text'), await token('text-3'));
        const turn = () => page.locator('.tool-group > summary > .chevron').evaluate(n => getComputedStyle(n).transform);
        assert.equal(await turn(), 'none');
        await head.click();
        assert.equal(await turn(), 'matrix(0, 1, -1, 0, 0, 0)');
        await head.hover();
        assert.equal(await head.evaluate(n => getComputedStyle(n).backgroundColor), await token('bg-2'));
        await page.mouse.move(700, 500);
        await shot('group-opened');
        await head.click();
        await page.mouse.move(700, 500);
        await shot('group-folded');
    });

    await check('group header: the same names are listed once, a long list is cut with an ellipsis, the full list is the title', async () => {
        await show('group-many-names');
        const names = 'Read, List, Search, Bash, Search the web, Fetch, Task, Edit';
        assert.equal(await page.locator('.tool-group > summary .tool-label').textContent(), '11 calls');
        assert.equal(await page.locator('.tool-group > summary .tool-text').textContent(), names);
        assert.equal(await page.locator('.tool-group > summary .tool-target').getAttribute('title'), names);
        await page.setViewportSize({ width: 390, height: 844 });
        assert(await page.locator('.tool-group > summary .tool-text').evaluate(n => n.scrollWidth > n.clientWidth && getComputedStyle(n).textOverflow === 'ellipsis'), 'the names are cut at 390px');
        await page.setViewportSize({ width: 1440, height: 900 });
        await shot('group-many-names');
    });

    await check('group with a failed call: "4 calls, 1 failed" with the count in --err, a cross, open, its time shown', async () => {
        await show('group-failed');
        const label = page.locator('.tool-group > summary .tool-label');
        assert.equal(await label.textContent(), '4 calls, 1 failed');
        assert.equal(await page.locator('.tool-failed-count').textContent(), '1 failed');
        assert.equal(await colorOf('.tool-failed-count'), await token('err'));
        assert.equal(await colorOf('.tool-group > summary .tool-label'), await token('text-2'), 'the rest of the text stays --text-2');
        assert.equal(await page.locator('.tool-group > summary .tool-text').textContent(), 'Read, Search, Bash', 'Read is listed once');
        assert.equal(await page.locator('.tool-group').evaluate(n => getComputedStyle(n.querySelector('summary'), '::before').content), '"✕"');
        assert.equal(await page.locator('.tool-group').evaluate(n => n.open), true, 'a failed group is open');
        assert.equal(await page.locator('.tool-group > summary .tool-time').textContent(), '1s', 'a failed group shows its time under 2s (1.5s, floored by formatDuration)');
        await show('group-fast');
        assert.equal(await page.locator('.tool-group > summary .tool-label').textContent(), '2 calls');
        assert.equal(await page.locator('.tool-failed-count').count(), 0, 'no failed part without a failed call');
        assert.equal(await page.locator('.tool-group > summary .tool-time').textContent(), '', 'an ok group under 2s shows no time');
        await show('group-failed');
        await shot('group-failed');
    });

    // The ring is 2px --accent inside the summary: it must be whole on all four sides, inside every ancestor
    // that clips (content-visibility on the tool row used to cut it to a line over the output block), and the
    // screenshot must show the accent color in the middle of each side.
    await check('keyboard focus ring: a full 2px --accent ring on a tool row, a group and reasoning, no horizontal scroll', async () => {
        const accent = await rgbOf('accent');
        const subjects = [
            ['tool row with output', () => show('tool-ok-output'), '#feed-under-test .tool > summary'],
            ['group', () => show('batch-folded'), '#feed-under-test .tool-group > summary'],
            ['reasoning', () => show('reasoning-done'), '#feed-under-test .reasoning > summary'],
        ];
        for (const [name, arrange, selector] of subjects) {
            await arrange();
            const summary = page.locator(selector).first();
            await summary.focus();
            await page.keyboard.press('Shift+Tab');
            await page.keyboard.press('Tab');
            assert(await summary.evaluate(n => n === document.activeElement && n.matches(':focus-visible')), `${name}: keyboard focus is on the summary`);
            await frames(page);
            const ring = await summary.evaluate(n => {
                const s = getComputedStyle(n), r = n.getBoundingClientRect(), grow = parseFloat(s.outlineOffset) + parseFloat(s.outlineWidth);
                const outer = { left: r.left - grow, top: r.top - grow, right: r.right + grow, bottom: r.bottom + grow };
                const clips = [];
                for (let a = n.parentElement; a; a = a.parentElement) {
                    const c = getComputedStyle(a);
                    if (c.overflowX === 'visible' && c.overflowY === 'visible' && !/paint|strict|content/.test(c.contain) && c.contentVisibility === 'visible') continue;
                    const b = a.getBoundingClientRect();
                    clips.push({ clip: a.className || a.tagName, inside: outer.left >= b.left - 0.01 && outer.right <= b.right + 0.01 && outer.top >= b.top - 0.01 && outer.bottom <= b.bottom + 0.01 });
                }
                return { style: s.outlineStyle, width: s.outlineWidth, color: s.outlineColor, radius: s.borderTopLeftRadius, outer, clips, viewport: outer.left >= 0 && outer.top >= 0 && outer.right <= innerWidth && outer.bottom <= innerHeight };
            });
            assert.deepEqual([ring.style, ring.width, ring.color, ring.radius], ['solid', '2px', await token('accent'), '4px'], `${name}: ${JSON.stringify(ring)}`);
            assert(ring.clips.length > 0 && ring.clips.every(c => c.inside) && ring.viewport, `${name}: the ring is inside what clips it ${JSON.stringify(ring.clips)}`);
            const box = await summary.boundingBox(), x = Math.round(box.x), y = Math.round(box.y), w = Math.round(box.width), h = Math.round(box.height);
            const png = decodePng(await page.screenshot({ clip: { x, y, width: w, height: h } }));
            const sides = { top: [w >> 1, 1], bottom: [w >> 1, h - 2], left: [1, h >> 1], right: [w - 2, h >> 1] };
            for (const [side, [px, py]] of Object.entries(sides)) {
                const found = png.at(px, py);
                assert(found.every((value, i) => Math.abs(value - accent[i]) <= 4), `${name}: the ${side} side of the ring is ${found}, not the accent ${accent}`);
            }
            for (const [width, height] of [[390, 844], [1280, 720], [1440, 900]]) {
                await page.setViewportSize({ width, height });
                assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${name}: horizontal scroll of the page at ${width}px`);
                assert(await page.locator('#feed-under-test').evaluate(n => n.scrollWidth <= n.clientWidth), `${name}: horizontal scroll of the feed at ${width}px`);
            }
            await shot(`focus-ring-${name.replaceAll(' ', '-')}`);
        }
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
        // a running call with no output has nothing to open; once its output streams in, it does
        await row.locator('summary').click();
        assert.equal(await row.evaluate(n => n.open), false);
        await push([rec({ type: 'action_output', call_id: 'c1', output: 'building…' }, T0 + 1)]);
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

    // --- input: the pinned panel and the answered rows, in the real app page ---------------------------
    // The panel is built by session.js, so these scenarios run on the app itself with the API mocked as in
    // redesign.mjs: a durable input_request reaches the page as a live envelope and then through the journal.
    const inputShots = process.env.KVIST_SHOTS_INPUT || `${shots}/input`;
    await mkdir(inputShots, { recursive: true });
    const SECRET = 'hunter2-SECRET-value';
    const app = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
    const appErrors = [], consoleLines = [], sent = [];
    app.on('pageerror', e => appErrors.push(e.message));
    app.on('console', message => consoleLines.push(message.text()));
    app.on('request', request => sent.push({ method: request.method(), path: new URL(request.url()).pathname, body: request.postData() ?? '', url: request.url() }));
    const fx = fixture();
    const reviewSession = () => fx.sessions.find(s => s.name === 'review');
    let cursor = 0;
    const answers = [];
    await app.addInitScript(finishTransitionsOnRead);
    await app.addInitScript(() => {
        window.__sources = [];
        window.EventSource = class {
            constructor(url) { this.url = url; window.__sources.push(this); setTimeout(() => this.onopen?.({}), 0); }
            close() {}
            emit(data) { this.onmessage?.({ data: JSON.stringify(data) }); }
        };
    });
    await app.route('**/api/v1/**', async route => {
        const request = route.request(), url = new URL(request.url()), path = url.pathname.replace('/api/v1', '');
        const json = value => route.fulfill({ json: value });
        if (path === '/bootstrap') return json({ protocol_version: 1, navigation: fx.inbox(), event_stream: { cursor, epoch: 'e' } });
        if (path === '/navigation') return json(fx.inbox());
        if (path === '/history') {
            const all = fx.histories[url.searchParams.get('session')] || [], after = Number(url.searchParams.get('after'));
            const events = all.filter(e => e.seq > after).slice(0, 1000);
            return json({ events, has_more: events.at(-1)?.seq < all.length });
        }
        // The navigation mock reports the session the way the server does: waiting while a request is open.
        if (path === '/respond') { answers.push(request.postDataJSON().response); Object.assign(reviewSession(), { execution: 'running', pending_input: null }); return json({ ok: true }); }
        if (path === '/configuration') return json({ provider: 'p', setup_required: false, providers: [], custom_providers: [] });
        return json({ ok: true });
    });
    await app.goto(base);
    await app.locator('#history-status').waitFor({ state: 'hidden' });
    await app.getByRole('button', { name: /^Improve session recovery,/ }).click();
    await app.locator('#history-status').waitFor({ state: 'hidden' });
    const journal = event => {
        const list = fx.histories.review;
        list.push({ seq: list.length + 1, turn: 2, ts: 1780000100, event });
        const session = reviewSession();
        session.last_event_id = list.length;
        if (event.type === 'input_request') Object.assign(session, { execution: 'waiting_for_user', pending_input: event });
        if (event.type === 'turn_end') Object.assign(session, { execution: 'finished', pending_input: null });
        return app.evaluate(envelope => window.__sources.at(-1).emit(envelope), { id: ++cursor, server_id: 'local', workspace: fx.workspace, session: 'review', event, durable: true });
    };
    const request = (id, kind, extra = {}) => ({ type: 'input_request', request_id: id, kind, title: 'Question', body: '', options: null, current_value: '', help_text: '', ...extra });
    const panel = app.locator('#pending-input');
    const ask = async event => { await journal(event); await panel.waitFor({ state: 'visible' }); };
    const answered = text => app.locator('#conversation .answer', { hasText: text });
    const edges = () => app.evaluate(() => { const p = document.querySelector('#pending-input').getBoundingClientRect(), c = document.querySelector('#composer').getBoundingClientRect(); return { panel: [Math.round(p.x), Math.round(p.width), Math.round(p.bottom)], composer: [Math.round(c.x), Math.round(c.width), Math.round(c.top)] }; });
    const inputShot = async name => {
        await app.mouse.move(2, 2); // a button under the pointer is in its hover color
        await settled(app);
        await app.screenshot({ path: `${inputShots}/${name}-1440x900.png` });
        await app.setViewportSize({ width: 390, height: 844 });
        await settled(app);
        await app.screenshot({ path: `${inputShots}/${name}-390x844.png` });
        assert(await app.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${name}: horizontal scroll at 390px`);
        await app.setViewportSize({ width: 1440, height: 900 });
    };
    const appToken = name => app.evaluate(name => { const n = document.createElement('i'); n.style.color = `var(--${name})`; document.body.append(n); const c = getComputedStyle(n).color; n.remove(); return c; }, name);
    const focusedId = () => app.evaluate(() => { const n = document.activeElement; return n.id || n.textContent.trim().slice(0, 12) || n.type || n.tagName; });
    const confirmEvent = (id, extra = {}) => request(id, 'confirm', { title: 'Run the test suite?', subject: { kind: 'command', command: 'npm test', reason: 'Verify the changes' }, remember_pattern: 'npm test', ...extra });

    await check('input pending, confirm: panel pinned above the field with its width and left edge', async () => {
        await ask(confirmEvent('c1'));
        for (const [width, height] of [[1440, 900], [1280, 720], [390, 844], [790, 700]]) {
            await app.setViewportSize({ width, height });
            const e = await edges();
            assert.deepEqual(e.panel.slice(0, 2), e.composer.slice(0, 2), `${width}: the panel has the left edge and width of the input ${JSON.stringify(e)}`);
            assert(e.panel[2] <= e.composer[2], `${width}: the panel is above the input`);
        }
        await app.setViewportSize({ width: 1440, height: 900 });
        assert.equal((await edges()).panel[1], 760);
        const box = await panel.evaluate(n => { const s = getComputedStyle(n); return [s.borderTopWidth, s.borderTopStyle, s.borderTopLeftRadius]; });
        assert.deepEqual(box, ['1px', 'solid', '6px']);
        assert.equal(await panel.locator('h3').textContent(), 'Run the test suite?');
        // The heading is the question; the panel carries no sentence the event did not bring.
        assert.equal(await panel.locator(':scope > p').count(), 0, 'a request without body or help text has no line under the heading');
        const pageText = await app.locator('body').innerText();
        assert(!pageText.includes('Kvist needs an answer.') && !pageText.includes('Review this request before continuing.'), 'a line the event did not bring is on the page');
        assert.deepEqual(await panel.getByRole('button').allTextContents(), ['Deny', 'Allow']);
        assert.equal(await panel.getByRole('checkbox').count(), 1);
        assert.equal(await app.locator('#conversation .answer').count(), 0, 'an unanswered request is not in the feed');
        assert.equal(await app.locator('#conversation').getByText('Run the test suite?').count(), 0);
        await inputShot('input-confirm-pending');
    });

    await check('input pending: the first control has the focus and Tab goes through the panel, then the composer', async () => {
        const where = () => app.evaluate(() => { const n = document.activeElement; return { name: n.textContent.trim().slice(0, 12) || n.type, panel: !!n.closest('#pending-input'), composer: !!n.closest('#composer') }; });
        const trail = [await where()];
        for (let i = 0; i < 3; i++) { await app.keyboard.press('Tab'); trail.push(await where()); }
        assert.deepEqual(trail.map(t => [t.name, t.panel ? 'panel' : t.composer ? 'composer' : 'elsewhere']),
            [['Deny', 'panel'], ['Allow', 'panel'], ['checkbox', 'panel'], [trail[3].name, 'composer']]);
    });

    await check('input pending, confirm: the command alone in its block, the reason under it as plain text', async () => {
        const look = await panel.evaluate(() => {
            const pre = document.querySelector('#pending-input pre'), reason = document.querySelector('#pending-input .pending-reason');
            const style = getComputedStyle(reason);
            return { block: pre.textContent, reason: reason.textContent, inBlock: pre.contains(reason), below: reason.getBoundingClientRect().top >= pre.getBoundingClientRect().bottom, mono: style.fontFamily.includes('Mono'), color: style.color };
        });
        assert.deepEqual(look, { block: 'npm test', reason: 'Verify the changes', inBlock: false, below: true, mono: false, color: await appToken('text-2') });
    });

    await check('input pending: the panel border is --line-strong while focus is inside and --accent otherwise, never two accents', async () => {
        const accent = await appToken('accent'), strong = await appToken('line-strong');
        const look = () => app.evaluate(() => {
            const box = document.querySelector('#pending-input'), node = document.activeElement, style = getComputedStyle(node);
            return { inside: box.contains(node), panel: getComputedStyle(box).borderTopColor, ring: [style.outlineStyle, style.outlineWidth, style.outlineColor] };
        });
        await app.locator('#prompt').focus();
        await app.keyboard.press('Shift+Tab'); // the last control of the panel, by keyboard, so the ring shows
        const inside = await look();
        assert.deepEqual(inside, { inside: true, panel: strong, ring: ['solid', '2px', accent] });
        await app.keyboard.press('Shift+Tab');
        assert.deepEqual(await look(), { inside: true, panel: strong, ring: ['solid', '2px', accent] }, 'the border stays while the focus moves between controls');
        await app.evaluate(() => document.activeElement.blur());
        assert.deepEqual((await look()).panel, accent, 'no focus inside: the panel border is the accent');
        assert.equal((await look()).inside, false);
    });

    await check('input pending, confirm: the remember flag is 14px, centered on its text, accent-colored, the size of the button labels', async () => {
        const look = await app.evaluate(() => {
            const box = document.querySelector('#pending-input input[type=checkbox]'), span = box.closest('label').querySelector('span'), range = document.createRange();
            range.selectNodeContents(span);
            const b = box.getBoundingClientRect(), t = range.getBoundingClientRect();
            return { size: [Math.round(b.width), Math.round(b.height)], offset: Math.abs(b.top + b.height / 2 - (t.top + t.height / 2)), accent: getComputedStyle(box).accentColor, label: getComputedStyle(span).fontSize, button: getComputedStyle(document.querySelector('#pending-input button')).fontSize };
        });
        assert.deepEqual(look.size, [14, 14]);
        assert(look.offset <= 1, `the centers of the flag and its text differ by ${look.offset}px`);
        assert.equal(look.accent, await appToken('accent'));
        assert.equal(look.label, look.button);
    });

    await check('input answered, confirm: Allow posts the answer, the panel goes, a check row says what was approved', async () => {
        await app.getByRole('checkbox').check();
        await app.getByRole('button', { name: 'Allow', exact: true }).click();
        await panel.waitFor({ state: 'hidden' });
        assert.equal(await panel.evaluate(n => n.children.length), 0, 'the controls are removed with the panel');
        assert.deepEqual(answers.at(-1), { request_id: 'c1', approved: true, remember: true });
        const row = answered('approved: run npm test');
        await row.waitFor();
        const look = await row.evaluate(n => { const s = getComputedStyle(n), m = getComputedStyle(n, '::before'); return { color: s.color, size: s.fontSize, mono: s.fontFamily.includes('Mono'), mark: m.content, markColor: m.color }; });
        assert.deepEqual([look.color, look.size, look.mono, look.mark, look.markColor], [await appToken('text-2'), '13px', false, '"✓"', await appToken('ok')]);
        await inputShot('input-confirm-answered');
    });

    await check('input answered, confirm: Deny reads "denied: ..." with a cross and no remember flag', async () => {
        await ask(confirmEvent('c2', { remember_pattern: null }));
        assert.equal(await panel.getByRole('checkbox').count(), 0);
        await app.getByRole('button', { name: 'Deny', exact: true }).click();
        await panel.waitFor({ state: 'hidden' });
        assert.deepEqual(answers.at(-1), { request_id: 'c2', approved: false });
        const row = answered('denied: run npm test');
        await row.waitFor();
        assert.equal(await row.evaluate(n => getComputedStyle(n, '::before').content), '"✕"');
        assert.equal(await row.evaluate(n => getComputedStyle(n, '::before').color), await appToken('err'));
    });

    await check('input choice: a radio list with the current value checked, Continue sends the choice', async () => {
        await ask(request('k1', 'choice', { title: 'Pick a model', options: ['fast', { value: 's', label: 'smart' }, 'slow'], current_value: 's' }));
        assert.equal(await panel.getByRole('radio').count(), 3);
        assert.equal(await panel.getByRole('radio', { name: 'smart' }).isChecked(), true);
        assert.equal(await panel.getByRole('radiogroup').count(), 1);
        assert.equal(await focusedId(), 'radio');
        // Continue is enabled with an option selected; the muted look in a screenshot is the hover color.
        const proceed = panel.getByRole('button', { name: 'Continue' }), fill = () => proceed.evaluate(n => getComputedStyle(n).backgroundColor);
        await app.mouse.move(2, 2);
        assert.equal(await proceed.isEnabled(), true);
        assert.equal(await fill(), await appToken('text'), 'Continue has its own fill without the pointer on it');
        await proceed.hover();
        assert.equal(await fill(), await appToken('text-2'), 'under the pointer it takes the hover fill');
        await inputShot('input-choice-pending');
        await panel.getByRole('radio', { name: 'slow' }).check();
        await panel.getByRole('button', { name: 'Continue' }).click();
        await panel.waitFor({ state: 'hidden' });
        assert.deepEqual(answers.at(-1), { request_id: 'k1', value: 'slow' });
        await answered('Pick a model: slow').waitFor();
        await ask(request('k2', 'choice', { title: 'Pick again', options: [{ value: 'a', label: 'Alpha' }, { value: 'b', label: 'Beta' }] }));
        assert.equal(await panel.getByRole('radio', { name: 'Alpha' }).isChecked(), true, 'the first option is checked when there is no current value');
        await panel.getByRole('radio', { name: 'Beta' }).check();
        await app.keyboard.press('Tab');
        await app.keyboard.press('Enter');
        await panel.waitFor({ state: 'hidden' });
        assert.deepEqual(answers.at(-1), { request_id: 'k2', value: 'b' });
        await answered('Pick again: Beta').waitFor();
        await inputShot('input-choice-answered');
    });

    await check('input text: a field, Enter sends, the field is cleared', async () => {
        await ask(request('t1', 'text', { title: 'Branch name', current_value: 'main' }));
        const field = panel.getByRole('textbox');
        assert.equal(await field.inputValue(), 'main');
        assert(await field.evaluate(n => n === document.activeElement));
        const look = await field.evaluate(n => { const s = getComputedStyle(n), p = getComputedStyle(n.closest('#pending-input')); return { field: s.borderTopColor, ring: [s.outlineStyle, s.outlineWidth, s.outlineColor], panel: p.borderTopColor }; });
        assert.deepEqual(look, { field: await appToken('line-strong'), ring: ['solid', '2px', await appToken('accent')], panel: await appToken('line-strong') }, 'one accent: the ring of the field, not its border and not the panel');
        await inputShot('input-text-pending');
        await field.fill('feature/pinned-panel');
        await app.keyboard.press('Enter');
        await panel.waitFor({ state: 'hidden' });
        assert.deepEqual(answers.at(-1), { request_id: 't1', value: 'feature/pinned-panel' });
        assert.equal(await panel.locator('input').count(), 0, 'the field is gone with its text');
        await answered('Branch name: feature/pinned-panel').waitFor();
        await inputShot('input-text-answered');
    });

    await check('input secret: a password field, Enter sends, the value is nowhere but in the answer', async () => {
        await ask(request('s1', 'secret', { title: 'API key' }));
        const field = panel.locator('input[type=password]');
        assert.equal(await field.count(), 1);
        assert(await field.evaluate(n => n === document.activeElement));
        await inputShot('input-secret-pending');
        await field.fill(SECRET);
        await app.keyboard.press('Enter');
        await panel.waitFor({ state: 'hidden' });
        assert.deepEqual(answers.at(-1), { request_id: 's1', value: SECRET });
        const secretRow = answered('API key: secret provided');
        await secretRow.waitFor();
        assert.equal((await secretRow.textContent()).trim(), 'API key: secret provided');
        await journal({ type: 'message', markdown: 'Key saved.' });
        await app.getByText('Key saved.').waitFor();
        await inputShot('input-secret-answered');

        // The value was in the POST of the answer and in nothing else.
        const where = sent.filter(r => r.body.includes(SECRET) || r.url.includes(SECRET));
        assert.deepEqual(where.map(r => `${r.method} ${r.path}`), ['POST /api/v1/respond']);
        assert(!consoleLines.some(line => line.includes(SECRET)), 'the value reached console.log');
        const found = await app.evaluate(async secret => {
            const html = document.documentElement.outerHTML, values = [...document.querySelectorAll('input, textarea')].map(n => n.value);
            const { state } = await import('/assets/state/store.js');
            const seen = new Set(), hits = [];
            const walk = (value, path) => {
                if (typeof value === 'string') { if (value.includes(secret)) hits.push(path); return; }
                if (!value || typeof value !== 'object' || seen.has(value)) return;
                seen.add(value);
                if (value instanceof Map) value.forEach((v, k) => { walk(k, `${path}.<key>`); walk(v, `${path}[${String(k)}]`); });
                else if (value instanceof Set) value.forEach(v => walk(v, `${path}.<set>`));
                else for (const [k, v] of Object.entries(value)) { walk(k, `${path}.<key>`); walk(v, `${path}.${k}`); }
            };
            walk(state, 'state');
            walk({ local: { ...localStorage }, session: { ...sessionStorage } }, 'storage');
            return { html: html.includes(secret), values: values.some(v => v.includes(secret)), hits };
        }, SECRET);
        assert.deepEqual(found, { html: false, values: false, hits: [] });
    });

    await check('input secret: a value the producer sent by mistake is not kept either', async () => {
        await ask(request('s2', 'secret', { title: 'Token', current_value: 'old-existing-secret' }));
        assert.equal(await panel.locator('input[type=password]').inputValue(), '');
        await app.getByRole('button', { name: 'Continue' }).click();
        await panel.waitFor({ state: 'hidden' });
        assert.deepEqual(answers.at(-1), { request_id: 's2', value: '' });
        assert.equal(await app.locator('#conversation').evaluate(n => n.innerHTML.includes('old-existing-secret')), false);
        assert.equal(await app.evaluate(async () => { const { state } = await import('/assets/state/store.js'); return JSON.stringify(state.events).includes('old-existing-secret'); }), false);
    });

    await check('input closed without an answer: "request closed" in --text-3, no panel left', async () => {
        await ask(request('x1', 'text', { title: 'Never answered' }));
        await journal({ type: 'turn_end' });
        await panel.waitFor({ state: 'hidden' });
        const row = app.locator('#conversation .answer-closed');
        await row.waitFor();
        assert.equal(await row.textContent(), 'request closed');
        assert.equal(await row.evaluate(n => getComputedStyle(n).color), await appToken('text-3'));
        assert.equal(await row.evaluate(n => getComputedStyle(n, '::before').content), '""');
        assert.equal(await app.locator('#conversation').getByText('Never answered').count(), 0);
        await inputShot('input-closed');
    });

    await check('input focus: a message being typed keeps the focus, an empty field gives it to the panel', async () => {
        const prompt = app.locator('#prompt');
        await prompt.focus();
        await app.keyboard.type('half a thought');
        await ask(confirmEvent('f1'));
        await app.waitForTimeout(120);
        assert.equal(await app.evaluate(() => document.activeElement.id), 'prompt', 'the panel took the focus from a message being typed');
        assert.equal(await prompt.inputValue(), 'half a thought');
        await app.keyboard.press('Shift+Tab');
        assert.equal(await focusedId(), 'checkbox', 'Shift+Tab from the composer reaches the last control of the panel');
        await app.getByRole('button', { name: 'Deny', exact: true }).click();
        await panel.waitFor({ state: 'hidden' });
        await prompt.fill('');
        await prompt.focus();
        await ask(confirmEvent('f2'));
        await app.waitForTimeout(120);
        assert.equal(await focusedId(), 'Deny', 'an empty composer does not keep the focus');
        await app.getByRole('button', { name: 'Allow', exact: true }).click();
        await panel.waitFor({ state: 'hidden' });
        await prompt.fill('');
    });

    await check('composer placeholder: "Draft your next message…" while the agent works, on every width', async () => {
        await ask(request('r1', 'text', { title: 'Name' }));
        await app.getByRole('button', { name: 'Continue' }).click();
        await panel.waitFor({ state: 'hidden' });
        await app.waitForTimeout(300); // past the navigation refresh, which reports the same state
        const placeholders = [];
        for (const [width, height] of [[1440, 900], [1280, 720], [390, 844]]) {
            await app.setViewportSize({ width, height });
            placeholders.push(await app.locator('#prompt').getAttribute('placeholder'));
        }
        await app.setViewportSize({ width: 1440, height: 900 });
        assert.deepEqual(placeholders, Array(3).fill('Draft your next message…'));
        assert.equal(await app.evaluate(() => document.querySelector('#agent-state').dataset.state), 'running');
    });

    // The navigation mock above reports the session as the server does (waiting_for_user with its
    // pending_input while a request is open), so the refresh a request triggers keeps the state.
    await check('input pending, every kind: Needs input with Stop and no Send, also after the navigation refresh', async () => {
        const shown = () => app.evaluate(() => { const node = document.querySelector('#agent-state'); return { state: node.dataset.state, label: node.textContent, stop: !document.querySelector('#stop-agent').hidden, send: !document.querySelector('#send-button').hidden }; });
        for (const [kind, extra] of [['confirm', {}], ['choice', { options: ['a', 'b'] }], ['text', {}], ['secret', {}]]) {
            await ask(request(`w-${kind}`, kind, extra));
            await app.waitForTimeout(300); // the refresh runs 120 ms after the event
            assert.deepEqual(await shown(), { state: 'waiting_for_user', label: 'Needs input', stop: true, send: false }, kind);
            await journal({ type: 'turn_end' });
            await panel.waitFor({ state: 'hidden' });
        }
        await app.waitForTimeout(300);
        assert.deepEqual(await shown(), { state: 'finished', label: '', stop: false, send: true });
        assert.equal(await app.locator('#prompt').getAttribute('placeholder'), 'Message Kvist, or type / for commands');
    });

    assert.deepEqual(appErrors, []);

    assert.deepEqual(errors, []);
    console.log(`PASS ${checks.length} feed state scenarios, screenshots in ${shots}`);
} finally {
    await browser.close();
}

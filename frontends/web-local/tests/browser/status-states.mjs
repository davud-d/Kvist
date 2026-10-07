// Status line, header and composer states: the connection shown only when it is not in order, "Needs input" as
// the one agent state in text, the context figure, the header's path and server, the open tab, the send button
// and its hint, and the composer without a second "+" or a server selector. Real asset server, API mocked in the
// page (the same approach as redesign.mjs and sidebar-states.mjs); a screenshot of each state at 1440x900 and 390x844.
// PLAYWRIGHT_MODULE=... CHROMIUM_EXECUTABLE=... KVIST_WEB_URL=http://127.0.0.1:PORT node .../status-states.mjs
// KVIST_SHOTS sets the screenshot directory (default /tmp/kvist-shots/5); KVIST_ONLY is a regular expression
// over scenario names.
import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { inflateSync } from 'node:zlib';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || process.argv[2] || 'playwright');
const base = process.env.KVIST_WEB_URL || 'http://127.0.0.1:9672';
const shots = process.env.KVIST_SHOTS || '/tmp/kvist-shots/5';
await mkdir(shots, { recursive: true });
const browser = await chromium.launch({ headless: true, ...((process.env.CHROMIUM_EXECUTABLE || process.argv[3]) ? { executablePath: process.env.CHROMIUM_EXECUTABLE || process.argv[3] } : {}) });

const session = (name, title, execution = 'idle') => ({ name, title, execution, unread: false, last_event_id: 0 });
const server = (id, name, workspaces, state = 'connected') => ({ server: { id, name, kind: id === 'local' ? 'local' : 'remote' }, connection: { state }, workspaces });
const workspace = (path, items) => ({ path, sessions: { session_items: items } });
// Titles avoid the words the status line must not show (Connected, Idle, Running, Finished).
const navigation = (execution = 'idle', state = 'connected') => ({ servers: [
    server('local', 'local', [workspace('/work/Kvist', [session('s1', 'First session', execution), session('s2', 'Second session')])], state),
    server('server-gz8q', 'server-gz8q', [workspace('/srv/project', [session('p1', 'Remote review')])]),
], inbox: [] });
const USAGE = { seq: 1, ts: 1, event: { type: 'usage', input_tokens: 10, output_tokens: 5, context_tokens: 54, context_limit: 100, model: 'm' } };

const frames = page => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
const ready = async page => {
    await page.locator('#app').waitFor({ state: 'visible' });
    await page.locator('#navigation .nav-group-toggle').first().waitFor();
    await page.waitForFunction(() => !document.querySelector('#history-status') || document.querySelector('#history-status').hidden);
    // The mono font is loaded on first use; a measurement taken before the swap sees other widths.
    await page.evaluate(() => document.fonts.load('12px "JetBrains Mono"'));
    await frames(page);
};
const live = [];
// Opens the app in a context of its own with the API mocked from `model.nav`. `hold` keeps the event stream from
// opening (the client stays "connecting"); `history` is the journal of every session.
async function open({ nav = navigation(), size = { width: 1440, height: 900 }, hold = false, history = [] } = {}) {
    const context = await browser.newContext({ viewport: size });
    const page = await context.newPage(), errors = [], warnings = [], requests = [], model = { nav, history };
    live.push(context);
    page.on('pageerror', e => errors.push(e.message));
    page.on('console', message => { if (message.type() === 'warning') warnings.push(message.text()); });
    await page.addInitScript(hold => {
        window.__sources = [];
        window.EventSource = class {
            constructor(url) { this.url = url; window.__sources.push(this); if (!hold) setTimeout(() => this.onopen?.({}), 0); }
            close() {}
        };
    }, hold);
    await page.route('**/api/v1/**', async route => {
        const request = route.request(), path = new URL(request.url()).pathname.replace('/api/v1', ''), json = value => route.fulfill({ json: value });
        const body = request.method() === 'POST' ? request.postDataJSON() : null;
        if (body) requests.push({ path, body });
        if (path === '/bootstrap') return json({ protocol_version: 1, navigation: model.nav, event_stream: { cursor: 0, epoch: 'e' } });
        if (path === '/navigation') return json(model.nav);
        if (path === '/history') return json({ events: model.history, has_more: false });
        if (path === '/configuration') return json({ provider: 'p', setup_required: false, providers: [], custom_providers: [] });
        return json({ ok: true });
    });
    await page.goto(base);
    await ready(page);
    return { page, errors, warnings, requests, model };
}
const only = process.env.KVIST_ONLY ? new RegExp(process.env.KVIST_ONLY) : null;
const checks = [];
const check = async (name, fn) => {
    if (only && !only.test(name)) return;
    try { await fn(); checks.push(name); console.log('PASS', name); }
    finally { await Promise.all(live.splice(0).map(context => context.close())); }
};
const shot = async (page, name) => {
    await page.mouse.move(700, 300);
    await ready(page);
    await page.screenshot({ path: `${shots}/${name}-1440x900.png` });
    await page.setViewportSize({ width: 390, height: 844 });
    await ready(page);
    await page.screenshot({ path: `${shots}/${name}-390x844.png` });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${name}: horizontal scroll at 390px`);
    await page.setViewportSize({ width: 1440, height: 900 });
    await ready(page);
};
// A navigation event is what the server sends when a connection or an execution changes: the client refreshes.
const emit = (page, type, extra = {}) => page.evaluate(([type, extra]) => window.__sources.at(-1).onmessage({ data: JSON.stringify({ id: Date.now(), event: { type, ...extra } }) }), [type, extra]);
const refresh = async (page, type = 'connection.state_changed') => { await emit(page, type); await page.waitForTimeout(400); };
const tokenColor = (page, name) => page.evaluate(name => { const probe = document.createElement('i'); probe.style.color = `var(--${name})`; document.body.append(probe); const color = getComputedStyle(probe).color; probe.remove(); return color; }, name);
const rgbOf = (page, name) => page.evaluate(name => { const c = document.createElement('canvas'); c.width = c.height = 1; const g = c.getContext('2d'); g.fillStyle = getComputedStyle(document.documentElement).getPropertyValue(`--${name}`).trim(); g.fillRect(0, 0, 1, 1); return [...g.getImageData(0, 0, 1, 1).data].slice(0, 3); }, name);
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
    return { at: (x, y) => [...out.subarray(y * stride + x * channels, y * stride + x * channels + 3)] };
}
const FORBIDDEN = ['Connecting…', 'Connected', 'Idle', 'Running', 'Finished'];
// The words of the removed texts must not be visible anywhere on the page (hidden elements have no text).
const noRemovedTexts = async (page, name, allow = []) => {
    const text = await page.evaluate(() => document.body.innerText);
    for (const word of FORBIDDEN.filter(word => !allow.includes(word)))
        assert(!text.includes(word), `${name}: "${word}" is shown`);
};
const statusOf = page => page.evaluate(() => {
    const get = id => { const n = document.querySelector(id); return { hidden: n.hidden, text: n.textContent, shown: n.getClientRects().length > 0, state: n.dataset.state }; };
    const line = document.querySelector('.status-line');
    return { connection: get('#connection-label'), agent: get('#agent-state'), context: get('#context-button'), line: getComputedStyle(line).display };
});
const pseudo = (page, selector, name = '::before') => page.locator(selector).evaluate((node, name) => {
    const s = getComputedStyle(node, name), own = getComputedStyle(node);
    return { content: s.content, width: s.width, height: s.height, radius: s.borderTopLeftRadius, edge: s.borderTopColor, topWidth: s.borderTopWidth, left: s.borderLeftWidth, leftColor: s.borderLeftColor, right: s.borderRightWidth, animation: s.animationName, color: own.color, font: own.fontFamily, size: own.fontSize };
}, name);
const rect = (page, selector) => page.locator(selector).evaluate(n => { const r = n.getBoundingClientRect(); return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width, height: r.height }; });
const contrast = ([r1, g1, b1], [r2, g2, b2]) => {
    const lin = v => { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
    const lum = (r, g, b) => 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
    const a = lum(r1, g1, b1), b = lum(r2, g2, b2);
    return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
};

try {
    // --- status line
    await check('all in order: no connection, no agent state, no empty context, no status line', async () => {
        const { page, warnings, errors } = await open();
        await page.waitForTimeout(1800); // past the 1.5 s a connection may spend connecting
        const status = await statusOf(page);
        assert.deepEqual([status.connection.hidden, status.connection.text, status.connection.shown], [true, '', false]);
        assert.deepEqual([status.agent.hidden, status.agent.text, status.agent.shown], [true, '', false]);
        assert.deepEqual([status.context.hidden, status.context.text, status.context.shown], [true, '', false]);
        assert.equal(status.line, 'none', 'an empty status line takes no room');
        await noRemovedTexts(page, 'in order');
        assert.deepEqual([errors, warnings], [[], []]);
        await shot(page, 'in-order');
    });

    await check('connecting: nothing for 1.5 s, then a ring and "Connecting…"', async () => {
        const { page } = await open({ hold: true });
        const early = await page.evaluate(() => ({ at: performance.now(), hidden: document.querySelector('#connection-label').hidden }));
        if (early.at < 1300) assert(early.hidden, `the connection is shown ${Math.round(early.at)} ms after the start`);
        await page.waitForFunction(() => !document.querySelector('#connection-label').hidden, null, { timeout: 4000 });
        const shown = await page.evaluate(() => performance.now());
        assert(shown >= 1500 && shown < 2600, `shown after ${Math.round(shown)} ms`);
        const label = await page.locator('#connection-label').textContent();
        assert.equal(label, 'Connecting…');
        const ring = await pseudo(page, '#connection-label');
        assert.deepEqual([ring.width, ring.height, ring.radius, ring.topWidth], ['10px', '10px', '50%', '2px']);
        assert.equal(ring.edge, await tokenColor(page, 'text-3'));
        assert.equal(ring.color, await tokenColor(page, 'text-3'));
        assert.notEqual(ring.animation, 'none');
        await noRemovedTexts(page, 'connecting', ['Connecting…']);
        await shot(page, 'connecting');
    });

    await check('a change of state starts the 1.5 s again: connecting shows late after it was in order', async () => {
        const { page, model } = await open();
        await page.waitForTimeout(300);
        model.nav = navigation('idle', 'connecting');
        await refresh(page);
        assert.equal(await page.locator('#connection-label').isHidden(), true, 'hidden 0.4 s after the change');
        await page.waitForTimeout(700);
        assert.equal(await page.locator('#connection-label').isHidden(), true, 'hidden 1.1 s after the change');
        await page.waitForFunction(() => !document.querySelector('#connection-label').hidden, null, { timeout: 2000 });
        assert.equal(await page.locator('#connection-label').textContent(), 'Connecting…');
        model.nav = navigation();
        await refresh(page);
        assert.equal(await page.locator('#connection-label').isHidden(), true, 'back in order: nothing');
    });

    await check('reconnecting: a ring and "Reconnecting…" at once, gone when the stream is back', async () => {
        const { page } = await open();
        await page.evaluate(() => window.__sources.at(-1).onerror({}));
        const status = await statusOf(page);
        assert.deepEqual([status.connection.hidden, status.connection.text, status.connection.state], [false, 'Reconnecting…', 'reconnecting']);
        const ring = await pseudo(page, '#connection-label');
        assert.deepEqual([ring.width, ring.height, ring.radius, ring.topWidth, ring.edge], ['10px', '10px', '50%', '2px', await tokenColor(page, 'text-3')]);
        await noRemovedTexts(page, 'reconnecting');
        await shot(page, 'reconnecting');
        await page.evaluate(() => window.__sources.at(-1).onopen({}));
        assert.equal(await page.locator('#connection-label').isHidden(), true);
        await page.waitForTimeout(300);
        assert.equal(await page.locator('#connection-label').isHidden(), true);
    });

    for (const [value, text] of [['offline', 'Offline'], ['failed', 'Failed']])
        await check(`${value}: a cross in --err with "${text}"`, async () => {
            const { page, model } = await open();
            model.nav = navigation('idle', value);
            await refresh(page);
            const status = await statusOf(page);
            assert.deepEqual([status.connection.hidden, status.connection.text, status.connection.state], [false, text, value]);
            const mark = await pseudo(page, '#connection-label');
            assert(mark.content.includes('✕'), `the mark is ${mark.content}`);
            assert.equal(mark.color, await tokenColor(page, 'err'));
            await noRemovedTexts(page, value);
            await shot(page, value);
        });

    await check('waiting for the person: "Needs input" in --warn with two 8px bars; no other agent state in text', async () => {
        const { page, model } = await open({ nav: navigation('waiting_for_user') });
        await page.waitForTimeout(500);
        const status = await statusOf(page);
        assert.deepEqual([status.agent.hidden, status.agent.text, status.agent.state], [false, 'Needs input', 'waiting_for_user']);
        const bars = await pseudo(page, '#agent-state');
        assert.deepEqual([bars.width, bars.height, bars.left, bars.right], ['8px', '8px', '2px', '2px']);
        assert.deepEqual([bars.leftColor, bars.color], [await tokenColor(page, 'warn'), await tokenColor(page, 'warn')]);
        await noRemovedTexts(page, 'waiting');
        await shot(page, 'needs-input');
        for (const value of ['running', 'starting', 'finished', 'failed', 'stopped', 'idle']) {
            model.nav.servers[0].workspaces[0].sessions.session_items[0].execution = value;
            await refresh(page, 'agent.state_changed');
            const agent = (await statusOf(page)).agent;
            assert.deepEqual([agent.hidden, agent.text, agent.state], [true, '', value], value);
            await noRemovedTexts(page, value);
        }
    });

    await check('an unknown execution is kept as it is, nothing is drawn for it, one console warning', async () => {
        const { page, warnings } = await open({ nav: navigation('paused') });
        await refresh(page, 'agent.state_changed');
        await refresh(page, 'agent.state_changed');
        const status = await statusOf(page);
        assert.deepEqual([status.agent.hidden, status.agent.text, status.agent.state], [true, '', 'paused']);
        assert.deepEqual(warnings.filter(text => text.includes('paused')), ['Unknown session execution: paused']);
    });

    await check('context: absent without data, "Context N%" in mono 12px --text-3 at the left, 12px between items', async () => {
        const { page } = await open({ history: [USAGE] });
        await page.waitForSelector('#context-button:not([hidden])');
        assert.equal(await page.locator('#context-button').textContent(), 'Context 54%');
        const style = await pseudo(page, '#context-button');
        assert.match(style.font, /JetBrains Mono/);
        assert.deepEqual([style.size, style.color], ['12px', await tokenColor(page, 'text-3')]);
        await page.evaluate(() => window.__sources.at(-1).onerror({}));
        const [line, context, connection] = [await rect(page, '.status-line'), await rect(page, '#context-button'), await rect(page, '#connection-label')];
        assert(Math.abs(context.left - line.left) < 0.5, 'the context figure is the first item');
        assert(Math.abs(connection.left - context.right - 12) < 0.5, `gap ${connection.left - context.right}`);
        assert(!(await page.locator('.status-line').innerText()).includes('·'));
        await shot(page, 'context');
    });

    // --- header
    await check('header: title, the path under it in mono 12px --text-3, the server of a remote session beside it', async () => {
        const { page } = await open();
        const local = { path: await page.locator('#session-path').textContent(), server: await page.locator('#session-server').isHidden() };
        assert.deepEqual(local, { path: '/work/Kvist', server: true });
        const title = await rect(page, '#session-title'), path = await rect(page, '#session-path');
        assert(path.top >= title.bottom - 1, 'the path is under the title');
        const style = await pseudo(page, '#session-path');
        assert.match(style.font, /JetBrains Mono/);
        assert.deepEqual([style.size, style.color], ['12px', await tokenColor(page, 'text-3')]);
        await shot(page, 'header-local');
        await page.locator('.nav-session', { hasText: 'Remote review' }).click();
        await page.waitForFunction(() => document.querySelector('#session-title').textContent === 'Remote review');
        const remote = { path: await page.locator('#session-path').textContent(), server: await page.locator('#session-server').textContent() };
        assert.deepEqual(remote, { path: '/srv/project', server: 'server-gz8q' });
        const [p, s] = [await rect(page, '#session-path'), await rect(page, '#session-server')];
        assert(s.left - p.right >= 12 - 0.5, `gap ${s.left - p.right}`);
        assert(!(await page.locator('.session-head').innerText()).includes('·'));
        await shot(page, 'header-remote');
    });

    await check('tabs: the open one has aria-selected and a 2px --accent underline, the others --text-2', async () => {
        const { page } = await open();
        const tabs = () => page.evaluate(() => [...document.querySelectorAll('.head-actions [data-view]')].map(n => { const s = getComputedStyle(n); return { name: n.textContent, selected: n.getAttribute('aria-selected'), line: s.borderBottomWidth, edge: s.borderBottomColor, color: s.color }; }));
        const [accent, text2, text] = [await tokenColor(page, 'accent'), await tokenColor(page, 'text-2'), await tokenColor(page, 'text')];
        let seen = await tabs();
        assert.deepEqual(seen.map(t => [t.name, t.selected]), [['Files', 'false'], ['Diff', 'false'], ['Terminal', 'false']]);
        assert(seen.every(t => t.color === text2 && t.edge !== accent), 'closed tabs: --text-2, no underline');
        await page.getByRole('button', { name: 'Diff', exact: true }).click();
        await page.locator('#workspace-view').waitFor({ state: 'visible' });
        seen = await tabs();
        assert.deepEqual(seen.map(t => [t.name, t.selected]), [['Files', 'false'], ['Diff', 'true'], ['Terminal', 'false']]);
        assert.deepEqual([seen[1].line, seen[1].edge, seen[1].color], ['2px', accent, text]);
        assert(seen.filter(t => t.name !== 'Diff').every(t => t.color === text2 && t.edge !== accent));
        await shot(page, 'tab-open');
        await page.keyboard.press('Escape');
        await page.locator('#workspace-view').waitFor({ state: 'hidden' });
        seen = await tabs();
        assert(seen.every(t => t.selected === 'false' && t.color === text2 && t.edge !== accent), 'closed again: nothing marked');
        await shot(page, 'tab-closed');
    });

    await check('tabs: keyboard focus is a 2px --accent ring inside the tab on all four sides', async () => {
        const { page } = await open();
        const accent = await rgbOf(page, 'accent'), accentColor = await tokenColor(page, 'accent');
        const tab = page.getByRole('button', { name: 'Diff', exact: true });
        await page.getByRole('button', { name: 'Files', exact: true }).focus();
        await page.keyboard.press('Shift+Tab');
        await page.keyboard.press('Tab');
        await page.keyboard.press('Tab');
        await page.mouse.move(700, 500);
        await frames(page);
        assert(await tab.evaluate(n => n === document.activeElement && n.matches(':focus-visible')), 'keyboard focus on Diff');
        const ring = await tab.evaluate(n => {
            const s = getComputedStyle(n), r = n.getBoundingClientRect(), grow = parseFloat(s.outlineOffset) + parseFloat(s.outlineWidth);
            const outer = { left: r.left - grow, top: r.top - grow, right: r.right + grow, bottom: r.bottom + grow }, clips = [];
            for (let a = n.parentElement; a; a = a.parentElement) {
                const c = getComputedStyle(a);
                if (c.overflowX === 'visible' && c.overflowY === 'visible') continue;
                const b = a.getBoundingClientRect();
                clips.push(outer.left >= b.left - 0.01 && outer.right <= b.right + 0.01 && outer.top >= b.top - 0.01 && outer.bottom <= b.bottom + 0.01);
            }
            return { style: s.outlineStyle, width: s.outlineWidth, color: s.outlineColor, offset: s.outlineOffset, clips };
        });
        assert.deepEqual([ring.style, ring.width, ring.color, ring.offset], ['solid', '2px', accentColor, '-2px']);
        assert(ring.clips.every(Boolean), 'the ring is inside what clips it');
        const box = await tab.boundingBox(), x = Math.round(box.x), y = Math.round(box.y), w = Math.round(box.width), h = Math.round(box.height);
        const png = decodePng(await page.screenshot({ clip: { x, y, width: w, height: h } }));
        for (const [side, [px, py]] of Object.entries({ top: [w >> 1, 1], bottom: [w >> 1, h - 2], left: [1, h >> 1], right: [w - 2, h >> 1] })) {
            const found = png.at(px, py);
            assert(found.every((value, i) => Math.abs(value - accent[i]) <= 4), `the ${side} side of the ring is ${found}, not ${accent}`);
        }
        await shot(page, 'tab-focus');
    });

    // --- composer
    await check('send button: disabled, aria-disabled and muted while there is nothing to send, enabled with text', async () => {
        const { page } = await open();
        const button = page.locator('#send-button');
        const state = () => button.evaluate(n => ({ disabled: n.disabled, aria: n.getAttribute('aria-disabled'), opacity: getComputedStyle(n).opacity, cursor: getComputedStyle(n).cursor }));
        const empty = await state();
        assert.deepEqual([empty.disabled, empty.aria], [true, 'true']);
        assert(Number(empty.opacity) < 1, 'muted');
        await button.hover({ force: true });
        const hover = await button.evaluate(n => getComputedStyle(n).backgroundColor);
        await page.mouse.move(700, 300);
        assert.equal(hover, await button.evaluate(n => getComputedStyle(n).backgroundColor), 'no hover change while disabled');
        await shot(page, 'composer-empty');
        await page.locator('#prompt').fill('Fix the build');
        const filled = await state();
        assert.deepEqual([filled.disabled, filled.aria, filled.opacity], [false, 'false', '1']);
        await shot(page, 'composer-text');
        await page.locator('#prompt').fill('   ');
        assert.equal((await state()).disabled, true, 'blank text is nothing to send');
        await page.locator('#prompt').fill('');
        assert.equal((await state()).disabled, true);
    });

    await check('send hint matches the key that sends: Ctrl+Enter sends, Enter only breaks the line', async () => {
        const { page, requests } = await open();
        const hint = await page.locator('.composer-hint').textContent();
        assert.equal(hint, 'Ctrl+Enter to send');
        assert(!hint.includes('↵') && !hint.includes('·'));
        assert.equal(await page.locator('#send-button').getAttribute('title'), 'Send, Ctrl+Enter');
        await page.locator('#prompt').fill('hello');
        await page.locator('#prompt').press('Enter');
        await page.waitForTimeout(400);
        assert.equal(requests.filter(r => r.path === '/send').length, 0, 'Enter does not send');
        assert.equal(await page.locator('#prompt').inputValue(), 'hello\n');
        await page.locator('#prompt').press('Control+Enter');
        await page.waitForFunction(() => document.querySelector('#prompt').value === '');
        assert.equal(requests.filter(r => r.path === '/send').length, 1, 'Ctrl+Enter sends');
        assert.equal(requests.find(r => r.path === '/send').body.text.trim(), 'hello');
    });

    await check('composer: no second "+" and no server selector in the DOM, the palette still opens on Ctrl+K and from the header', async () => {
        const { page } = await open();
        for (const id of ['#composer-tools', '#server-switcher', '#active-server-label'])
            assert.equal(await page.locator(id).count(), 0, `${id} has no node`);
        assert.equal(await page.locator('.composer-controls .icon-button[data-icon="plus"]').count(), 0, 'no "+" icon button in the composer');
        await page.keyboard.press('Control+k');
        await page.locator('#palette').waitFor({ state: 'visible' });
        await page.keyboard.press('Escape');
        await page.locator('#palette').waitFor({ state: 'hidden' });
        await page.locator('#more-tools').click();
        await page.locator('#palette').waitFor({ state: 'visible' });
        assert(!(await page.locator('#palette footer').innerText()).includes('·'));
        await shot(page, 'palette');
    });

    await check('attach button: a 28px paperclip in --text-2, --text on hover, a 2px inner --accent ring on focus, centred in the row', async () => {
        const { page } = await open();
        const button = page.locator('.image-attach-button');
        assert.equal(await button.count(), 1);
        assert.equal((await button.textContent()).trim(), '', 'no "+" glyph');
        assert.equal(await button.locator('svg').count(), 1, 'an icon');
        const box = await rect(page, '.image-attach-button'), send = await rect(page, '#send-button');
        assert.deepEqual([box.width, box.height], [28, 28]);
        assert(Math.abs((box.top + box.height / 2) - (send.top + send.height / 2)) <= 0.5, 'centred with the send button');
        assert.equal(await button.evaluate(n => getComputedStyle(n).color), await tokenColor(page, 'text-2'));
        await button.hover();
        assert.equal(await button.evaluate(n => getComputedStyle(n).color), await tokenColor(page, 'text'));
        await page.mouse.move(700, 300);
        await page.locator('#prompt').focus();
        await page.keyboard.press('Tab');
        assert(await button.evaluate(n => n === document.activeElement && n.matches(':focus-visible')), 'keyboard focus');
        const ring = await button.evaluate(n => { const s = getComputedStyle(n); return [s.outlineStyle, s.outlineWidth, s.outlineColor, s.outlineOffset]; });
        assert.deepEqual(ring, ['solid', '2px', await tokenColor(page, 'accent'), '-2px']);
        await shot(page, 'attach-focus');
    });

    await check('the hint and the status line start where the input starts and are as wide as it', async () => {
        const { page } = await open({ history: [USAGE] });
        await page.waitForSelector('#context-button:not([hidden])');
        for (const [width, height] of [[1440, 900], [1280, 720], [390, 844]]) {
            await page.setViewportSize({ width, height });
            await frames(page);
            const field = await rect(page, '#composer'), hint = await rect(page, '.composer-hint'), line = await rect(page, '.status-line');
            for (const [name, box] of [['hint', hint], ['status line', line]]) {
                assert(Math.abs(box.left - field.left) < 0.5, `${width}: the ${name} starts at ${box.left}, the input at ${field.left}`);
                assert(Math.abs(box.width - field.width) < 0.5, `${width}: the ${name} is ${box.width} wide, the input ${field.width}`);
            }
        }
    });

    await check('no dot separator in the bottom rows, the header, the palette or any title; no horizontal scroll at 1440, 1280, 390', async () => {
        const { page } = await open({ history: [USAGE], nav: navigation('waiting_for_user') });
        await page.evaluate(() => window.__sources.at(-1).onerror({}));
        await page.waitForSelector('#context-button:not([hidden])');
        for (const [width, height] of [[1440, 900], [1280, 720], [390, 844]]) {
            await page.setViewportSize({ width, height });
            await frames(page);
            assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `horizontal scroll at ${width}`);
            const text = await page.evaluate(() => [...document.querySelectorAll('.composer-area, .session-head')].map(n => n.innerText).join('\n'));
            assert(!text.includes('·'), `a dot in the bottom rows or the header at ${width}`);
        }
        assert.equal(await page.locator('[title*="·"], [aria-label*="·"], [placeholder*="·"]').count(), 0);
        await shot(page, 'worst-case');
    });
} finally {
    await browser.close();
}
console.log(`${checks.length} scenarios passed`);

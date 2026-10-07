// Sidebar states: workspace groups, session rows and their indicators, against the real asset server with the
// API mocked in the page (the same approach as redesign.mjs). Takes a screenshot of each state at 1440x900 and
// at 390x844 with the drawer open.
// PLAYWRIGHT_MODULE=... CHROMIUM_EXECUTABLE=... KVIST_WEB_URL=http://127.0.0.1:PORT node .../sidebar-states.mjs
// KVIST_SHOTS sets the screenshot directory (default /tmp/kvist-shots/4); KVIST_ONLY is a regular expression
// over scenario names.
import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { inflateSync } from 'node:zlib';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || process.argv[2] || 'playwright');
const base = process.env.KVIST_WEB_URL || 'http://127.0.0.1:9672';
const shots = process.env.KVIST_SHOTS || '/tmp/kvist-shots/4';
await mkdir(shots, { recursive: true });
const browser = await chromium.launch({ headless: true, ...((process.env.CHROMIUM_EXECUTABLE || process.argv[3]) ? { executablePath: process.env.CHROMIUM_EXECUTABLE || process.argv[3] } : {}) });

const LONG = 'Investigate why the nightly export job keeps timing out when the archive holds more than two thousand small attachments';
const session = (name, title, execution, unread = false) => ({ name, title, execution, unread, last_event_id: 0 });
const server = (id, name, workspaces) => ({ server: { id, name, kind: id === 'local' ? 'local' : 'remote' }, connection: { state: 'connected' }, workspaces });
const workspace = (path, items) => ({ path, sessions: { session_items: items } });
// Every execution value, unread alone, unread over running, waiting and failed over unread, a title that does not fit.
const states = () => [
    session('running', 'Run regression tests', 'running'),
    session('starting', 'Start the build', 'starting'),
    session('waiting', 'Update configuration', 'waiting_for_user'),
    session('failed', 'Inspect unavailable workspace', 'failed'),
    session('unread', 'Review API boundaries', 'finished', true),
    session('unread-running', 'Fix flaky reconnect test', 'running', true),
    session('waiting-unread', 'Approve the migration', 'waiting_for_user', true),
    session('failed-unread', 'Rebuild the index', 'failed', true),
    session('idle', 'Idle session', 'idle'),
    session('finished', 'Finished session', 'finished'),
    session('stopped', 'Stopped session', 'stopped'),
    session('unknown', 'Paused session', 'paused'),
    session('long', LONG, 'finished'),
];
const navigation = () => ({ servers: [
    server('local', 'local', [
        workspace('/work/Kvist', states()),
        workspace('/work/quiet', [session('q1', 'Quiet one', 'idle'), session('q2', 'Quiet two', 'finished')]),
        workspace('/work/mixed', [session('m1', 'Mixed unread', 'finished', true), session('m2', 'Mixed failed', 'failed'), session('m3', 'Mixed running', 'running')]),
        workspace('/work/fresh', [session('f1', 'Fresh result', 'finished', true)]),
    ]),
    server('server-gz8q', 'server-gz8q', [workspace('/srv/project', [session('p1', 'Remote review', 'finished'), session('p2', 'Needs your answer', 'waiting_for_user'), session('p3', 'Unread remote', 'finished', true)])]),
], inbox: [] });
const bulkNavigation = () => ({ servers: [server('local', 'local', [workspace('/work/Kvist', Array.from({ length: 300 }, (_, i) => session(`bulk-${i}`, `Background session ${i}`, ['idle', 'finished', 'running', 'failed', 'waiting_for_user'][i % 5], i % 7 === 0)))])], inbox: [] });
const GROUPS = [['local', '/work/Kvist', 'Kvist'], ['local', '/work/quiet', 'quiet'], ['local', '/work/mixed', 'mixed'], ['local', '/work/fresh', 'fresh'], ['server-gz8q', '/srv/project', 'project']];
const ROWS = 13 + 2 + 3 + 1 + 3;
// The key the client gives a group: keyOf({ serverId, workspace }) in state/store.js, a session-less selection.
const keyOf = (serverId, path) => `${serverId}\u0000${path}\u0000`;

const frames = page => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
const ready = async page => {
    await page.locator('#app').waitFor({ state: 'visible' });
    await page.locator('#navigation .nav-group-toggle').first().waitFor();
    await frames(page);
};
const live = [];
// Opens the app in a context of its own (storage included) with the API mocked from `model.nav`.
async function open({ nav = navigation(), size = { width: 1440, height: 900 }, context: contextOptions = {}, init } = {}) {
    const context = await browser.newContext({ viewport: size, ...contextOptions });
    const page = await context.newPage(), errors = [], warnings = [], requests = [], model = { nav };
    live.push(context);
    page.on('pageerror', e => errors.push(e.message));
    page.on('console', message => { if (message.type() === 'warning') warnings.push(message.text()); });
    if (init) await page.addInitScript(init);
    await page.addInitScript(() => {
        window.__sources = [];
        window.EventSource = class {
            constructor(url) { this.url = url; window.__sources.push(this); setTimeout(() => this.onopen?.({}), 0); }
            close() {}
            emit(data) { this.onmessage?.({ data: JSON.stringify(data) }); }
        };
    });
    await page.route('**/api/v1/**', async route => {
        const request = route.request(), path = new URL(request.url()).pathname.replace('/api/v1', ''), json = value => route.fulfill({ json: value });
        const body = request.method() === 'POST' ? request.postDataJSON() : null;
        if (body) requests.push({ path, body });
        if (path === '/bootstrap') return json({ protocol_version: 1, navigation: model.nav, event_stream: { cursor: 0, epoch: 'e' } });
        if (path === '/navigation') return json(model.nav);
        if (path === '/history') return json({ events: [], has_more: false });
        if (path === '/configuration') return json({ provider: 'p', setup_required: false, providers: [], custom_providers: [] });
        if (path === '/sessions') {
            const name = `created-${requests.length}`;
            model.nav.servers.find(s => s.server.id === body.server_id).workspaces.find(w => w.path === body.workspace).sessions.session_items.unshift(session(name, `Created ${name}`, 'idle'));
            return json({ active_session: name });
        }
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
    await page.mouse.move(1300, 450);
    await ready(page);
    await page.screenshot({ path: `${shots}/${name}-1440x900.png` });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator('#toggle-sidebar').click();
    await ready(page);
    await page.screenshot({ path: `${shots}/${name}-390x844.png` });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${name}: horizontal scroll at 390px`);
    await page.setViewportSize({ width: 1440, height: 900 });
    await ready(page);
};
const rowOf = (page, title) => page.locator('.nav-session').filter({ has: page.getByText(title, { exact: true }) });
const toggleOf = (page, name) => page.locator('.nav-group-toggle').filter({ has: page.locator('.nav-group-name', { hasText: new RegExp(`^${name}$`) }) });
const addOf = (page, name) => page.getByRole('button', { name: `New session in ${name}`, exact: true });
// A design token as computed color, to compare with what an element paints.
const tokenColor = (page, name) => page.evaluate(name => { const probe = document.createElement('i'); probe.style.color = `var(--${name})`; document.body.append(probe); const color = getComputedStyle(probe).color; probe.remove(); return color; }, name);
// The sRGB value of a token as the canvas rasterizes it, to find the token's color in a screenshot.
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
// What a screen reader and the eye get from the indicator slot of a row or a group header.
const slotOf = locator => locator.locator('.nav-ind').first().evaluate(slot => {
    const side = pseudo => { const s = getComputedStyle(slot, pseudo); return { w: s.width, h: s.height, radius: s.borderTopLeftRadius, bg: s.backgroundColor, border: s.borderTopWidth, edge: parseFloat(s.borderTopWidth) ? s.borderTopColor : '', transform: s.transform, animation: s.animationName }; };
    const r = slot.getBoundingClientRect();
    return { cls: slot.className, role: slot.getAttribute('role'), label: slot.getAttribute('aria-label'), w: r.width, h: r.height, before: side('::before'), after: side('::after') };
});
const kindOf = slot => (/ind-(\w+)/.exec(slot.cls) || [])[1] || '';

try {
    await check('markup: no workspace dropdown, no "Sessions" caption, no dot separators', async () => {
        const { page } = await open();
        assert.equal(await page.locator('#workspace-switcher, #workspace-label, .workspace-switcher, .section-label').count(), 0);
        assert.equal(await page.locator('#sidebar [aria-haspopup]').count(), 0, 'nothing in the sidebar opens a popup');
        const text = await page.locator('#sidebar').innerText();
        assert(!/^\s*Sessions\s*$/m.test(text), `the caption is gone: ${text}`);
        assert(!text.includes('·'), 'no dot separator in the sidebar text');
        assert.equal(await page.locator('#sidebar [title*="·"], #sidebar [aria-label*="·"]').count(), 0, 'no dot separator in a title or aria-label');
        assert.equal(await page.locator('#sidebar .status-dot').count(), 0, 'the old status dots are gone');
        assert.equal(await page.evaluate(() => [...document.styleSheets].flatMap(s => [...s.cssRules]).filter(r => /status-dot|workspace-switcher|section-label/.test(r.cssText)).length), 0, 'and so are their rules');
        assert.deepEqual(await page.evaluate(() => [...document.querySelectorAll('.navigation, .nav-group, .nav-session')].map(n => getComputedStyle(n).contentVisibility)).then(list => [...new Set(list)]), ['visible'], 'no content-visibility on the list');
    });

    await check('rows: 28px, 13px, 14px indicator slot, title cut with an ellipsis only when it does not fit, title attribute', async () => {
        const { page } = await open();
        const rows = await page.locator('.nav-session').evaluateAll(nodes => nodes.map(n => {
            const title = n.querySelector('.nav-title'), slot = n.querySelector('.nav-ind'), row = n.getBoundingClientRect(), t = title.getBoundingClientRect(), s = slot.getBoundingClientRect(), c = getComputedStyle(title);
            return { text: title.textContent, h: row.height, size: getComputedStyle(n).fontSize, slot: [s.width, s.height], slotRight: Math.round(row.right - s.right), cut: title.scrollWidth > title.clientWidth, ellipsis: [c.textOverflow, c.whiteSpace, c.overflow], clear: t.right <= s.left + 0.5, rowOverflow: n.scrollWidth > n.clientWidth, attr: n.title, label: n.getAttribute('aria-label') };
        }));
        assert.equal(rows.length, ROWS);
        for (const row of rows) {
            assert.deepEqual([row.h, row.size, row.slot], [28, '13px', [14, 14]], JSON.stringify(row));
            assert.deepEqual(row.ellipsis, ['ellipsis', 'nowrap', 'hidden'], row.text);
            assert(row.clear && !row.rowOverflow, `the title stays left of the slot: ${JSON.stringify(row)}`);
            assert.equal(row.cut, row.text === LONG, `${row.text}: scrollWidth over clientWidth only for the cut title`);
            assert(row.attr.startsWith(row.text) && row.attr === row.label && /, /.test(row.attr) && !row.attr.includes('·'), `title and aria-label use commas: ${row.attr}`);
        }
        assert(new Set(rows.map(r => r.slotRight)).size === 1, 'the slot sits at the same distance from the right edge in every row');
    });

    await check('indicators: one shape and one label per state, priority waiting > failed > running > unread, unknown execution is empty and warned once', async () => {
        const { page, warnings } = await open();
        const expected = { 'Run regression tests': 'running', 'Start the build': 'running', 'Update configuration': 'waiting', 'Inspect unavailable workspace': 'failed', 'Review API boundaries': 'unread', 'Fix flaky reconnect test': 'running', 'Approve the migration': 'waiting', 'Rebuild the index': 'failed', 'Idle session': '', 'Finished session': '', 'Stopped session': '', 'Paused session': '', [LONG]: '' };
        const labels = { waiting: 'waiting for your answer', failed: 'failed', running: 'running', unread: 'unread' };
        const shapes = {};
        for (const [title, kind] of Object.entries(expected)) {
            const slot = await slotOf(rowOf(page, title));
            assert.equal(kindOf(slot), kind, `${title}: ${JSON.stringify(slot)}`);
            assert.deepEqual([slot.w, slot.h], [14, 14], `${title}: the slot is 14px even when empty`);
            if (!kind) {
                assert.deepEqual([slot.role, slot.label, slot.before.w, slot.before.bg, slot.after.bg], [null, null, '0px', 'rgba(0, 0, 0, 0)', 'rgba(0, 0, 0, 0)'], `${title}: an empty slot draws nothing`);
                continue;
            }
            assert.deepEqual([slot.role, slot.label], ['img', labels[kind]], title);
            shapes[kind] = slot;
        }
        const [warn, err, accent] = await Promise.all(['warn', 'err', 'accent'].map(name => tokenColor(page, name)));
        const w = shapes.waiting, f = shapes.failed, r = shapes.running, u = shapes.unread;
        assert.deepEqual([w.before.w, w.before.h, w.before.bg, w.after.w, w.after.h, w.after.bg], ['2px', '8px', warn, '2px', '8px', warn], 'waiting: two vertical bars of 8px in --warn');
        assert(f.before.transform !== 'none' && f.after.transform !== 'none' && f.before.transform !== f.after.transform && f.before.bg === err && f.after.bg === err && f.before.h === '2px', `failed: a cross in --err ${JSON.stringify(f)}`);
        assert.deepEqual([r.before.w, r.before.h, r.before.radius, r.before.border, r.before.edge, r.before.animation], ['12px', '12px', '50%', '2px', accent, 'spin'], 'running: a 12px ring that spins, --accent');
        assert.deepEqual([u.before.w, u.before.h, u.before.radius, u.before.bg], ['6px', '6px', '50%', accent], 'unread: a 6px dot in --accent');
        assert.equal(new Set(Object.values(shapes).map(s => JSON.stringify([s.before, s.after]))).size, 4, 'four states, four different shapes');
        // One unknown value, drawn on every refresh and in the collapsed-group scan, is warned about once.
        await page.evaluate(() => document.dispatchEvent(new Event('kvist:navigation-refresh')));
        await page.waitForTimeout(400);
        await toggleOf(page, 'Kvist').click();
        await toggleOf(page, 'Kvist').click();
        assert.deepEqual(warnings.filter(text => text.includes('paused')), ['Unknown session execution: paused']);
    });

    await check('unread over running: the ring and a title of weight 600; other rows keep 400', async () => {
        const { page } = await open();
        const weight = async title => rowOf(page, title).locator('.nav-title').evaluate(n => getComputedStyle(n).fontWeight);
        assert.equal(await weight('Fix flaky reconnect test'), '600');
        assert.equal(kindOf(await slotOf(rowOf(page, 'Fix flaky reconnect test'))), 'running');
        for (const title of ['Run regression tests', 'Review API boundaries', 'Approve the migration', 'Rebuild the index', 'Idle session'])
            assert.equal(await weight(title), '400', title);
    });

    await check('active row: --bg-2 fill and a 2px --accent bar inside the row', async () => {
        const { page } = await open();
        const active = page.locator('.nav-session.active');
        assert.equal(await active.count(), 1);
        const [fill, bar, current] = await active.evaluate(n => { const bg = getComputedStyle(n).backgroundColor, b = getComputedStyle(n, '::before'), r = n.getBoundingClientRect(); return [bg, { w: b.width, bg: b.backgroundColor, left: b.left, top: parseFloat(b.top), bottom: parseFloat(b.bottom), h: r.height }, n.getAttribute('aria-current')]; });
        const probe = await page.evaluate(() => { const i = document.createElement('i'); i.style.background = 'var(--bg-2)'; document.body.append(i); const c = getComputedStyle(i).backgroundColor; i.remove(); return c; });
        assert.deepEqual([fill, bar.w, bar.bg, bar.left, current], [probe, '2px', await tokenColor(page, 'accent'), '0px', 'page']);
        assert(bar.top >= 0 && bar.bottom >= 0, 'the bar lies inside the row');
    });

    await check('group header: 24px, 11px --text-3 name and server in separate elements 6px apart, shared chevron, no dot', async () => {
        const { page } = await open();
        const text3 = await tokenColor(page, 'text-3');
        for (const [serverId, path, name] of GROUPS) {
            const info = await toggleOf(page, name).evaluate(n => {
                const head = n.parentElement.getBoundingClientRect(), nameNode = n.querySelector('.nav-group-name'), serverNode = n.querySelector('.nav-group-server'), chevron = n.querySelector(':scope > svg.chevron'), s = getComputedStyle(nameNode);
                const gap = serverNode ? serverNode.getBoundingClientRect().left - nameNode.getBoundingClientRect().right : null;
                return { h: head.height, size: s.fontSize, color: s.color, transform: s.textTransform, serverColor: serverNode && getComputedStyle(serverNode).color, server: serverNode?.textContent ?? null, gap, chevron: chevron && [chevron.getBoundingClientRect().width, chevron.getBoundingClientRect().height], expanded: n.getAttribute('aria-expanded'), title: n.title, text: n.textContent, tag: n.tagName };
            });
            assert.deepEqual([info.h, info.size, info.color, info.transform, info.chevron, info.expanded, info.tag, info.title], [24, '11px', text3, 'none', [10, 10], 'true', 'BUTTON', path], `${name}: ${JSON.stringify(info)}`);
            if (serverId === 'local') assert.equal(info.server, null, 'the local server is not named');
            else assert.deepEqual([info.server, info.serverColor, Math.round(info.gap)], ['server-gz8q', text3, 6], JSON.stringify(info));
            assert(!info.text.includes('·'), `${name}: no dot in the header`);
        }
        assert.equal(await page.locator('.nav-group-toggle > svg.chevron').evaluateAll(list => list.every(n => n.getAttribute('class') === 'chevron')), true);
    });

    await check('"+": New session in {name}, visible on hover and focus only, creates the session in its own workspace', async () => {
        const { page, requests } = await open();
        await page.mouse.move(1300, 450);
        const opacity = name => addOf(page, name).evaluate(n => getComputedStyle(n).opacity);
        for (const [, , name] of GROUPS) {
            assert.equal(await addOf(page, name).count(), 1, name);
            assert.deepEqual(await addOf(page, name).evaluate(n => { const icon = n.querySelector('svg'), r = n.getBoundingClientRect(); return [r.width, r.height, icon && icon.getBoundingClientRect().width, icon && icon.querySelector('path')?.getAttribute('d')]; }), [24, 24, 12, 'M12 5v14 M5 12h14'], `${name}: a 24px button with the 12px plus drawn`);
        }
        assert.equal(await opacity('project'), '0', 'hidden while nothing points at the header');
        await toggleOf(page, 'project').hover();
        assert.equal(await opacity('project'), '1', 'visible on hover');
        assert.equal(await opacity('mixed'), '0', 'only the hovered header shows it');
        await page.mouse.move(1300, 450);
        await toggleOf(page, 'mixed').focus();
        assert.equal(await opacity('mixed'), '1', 'visible while focus is inside the header');
        // The selected session is in /work/Kvist; the "+" of the remote group must still create in its own workspace.
        await toggleOf(page, 'project').hover();
        await addOf(page, 'project').click();
        await page.waitForFunction(() => document.querySelector('#session-title').textContent.startsWith('Created created-'));
        const created = requests.filter(r => r.path === '/sessions');
        assert.deepEqual(created.map(r => r.body), [{ server_id: 'server-gz8q', workspace: '/srv/project' }]);
        await toggleOf(page, 'quiet').hover();
        await addOf(page, 'quiet').click();
        await page.waitForFunction(() => document.querySelector('#session-path').textContent.includes('/work/quiet'));
        assert.deepEqual(requests.filter(r => r.path === '/sessions').at(-1).body, { server_id: 'local', workspace: '/work/quiet' });
    });

    await check('"+" is always visible without hover (hover: none)', async () => {
        const { page } = await open({ size: { width: 800, height: 900 }, context: { hasTouch: true, isMobile: true } });
        assert.equal(await page.evaluate(() => matchMedia('(hover: none)').matches), true, 'the context has no hover');
        await page.locator('#toggle-sidebar').click();
        await page.locator('#sidebar').waitFor({ state: 'visible' });
        const list = await page.locator('.nav-group-add').evaluateAll(nodes => nodes.map(n => getComputedStyle(n).opacity));
        assert.deepEqual(list, GROUPS.map(() => '1'));
    });

    await check('New session: no border, no fill, "+" icon at the left, no hotkey hint; creates in the selected session\'s workspace', async () => {
        const { page, requests } = await open();
        const button = page.locator('#new-session');
        const look = await button.evaluate(n => { const s = getComputedStyle(n), icon = n.querySelector('[data-icon="plus"]'); return { border: s.borderTopWidth, bg: s.backgroundColor, text: n.textContent.trim(), kbd: n.querySelectorAll('kbd').length, iconLeft: icon.getBoundingClientRect().left - n.getBoundingClientRect().left, iconSize: icon.firstElementChild.getBoundingClientRect().width, h: n.getBoundingClientRect().height }; });
        assert.deepEqual([look.border, look.bg, look.text, look.kbd, look.h], ['0px', 'rgba(0, 0, 0, 0)', 'New session', 0, 28], JSON.stringify(look));
        assert(look.iconLeft >= 0 && look.iconLeft <= 12, `the icon is at the left: ${look.iconLeft}`);
        await rowOf(page, 'Remote review').click();
        await page.waitForFunction(() => document.querySelector('#session-title').textContent === 'Remote review');
        await button.click();
        await page.waitForFunction(() => document.querySelector('#session-title').textContent.startsWith('Created created-'));
        assert.deepEqual(requests.filter(r => r.path === '/sessions').map(r => r.body), [{ server_id: 'server-gz8q', workspace: '/srv/project' }]);
        await rowOf(page, 'Quiet one').click();
        await page.waitForFunction(() => document.querySelector('#session-title').textContent === 'Quiet one');
        await button.click();
        await page.waitForFunction(() => document.querySelector('#session-path').textContent.includes('/work/quiet'));
        assert.deepEqual(requests.filter(r => r.path === '/sessions').at(-1).body, { server_id: 'local', workspace: '/work/quiet' });
    });

    await check('collapse: click, Enter and Space; focus stays on the header; the choice survives a reload', async () => {
        const { page } = await open();
        const expanded = name => toggleOf(page, name).getAttribute('aria-expanded');
        const stored = () => page.evaluate(() => JSON.parse(localStorage.getItem('kvist.nav.collapsed')));
        const rows = () => page.locator('.nav-session').count();
        assert.equal(await rows(), ROWS);
        await toggleOf(page, 'quiet').click();
        assert.deepEqual([await expanded('quiet'), await rows(), await stored()], ['false', ROWS - 2, [keyOf('local', '/work/quiet')]]);
        await toggleOf(page, 'quiet').focus();
        await page.keyboard.press('Enter');
        assert.deepEqual([await expanded('quiet'), await rows(), await stored()], ['true', ROWS, []]);
        assert.equal(await page.evaluate(() => document.activeElement.className + ':' + document.activeElement.querySelector('.nav-group-name').textContent), 'nav-group-toggle:quiet', 'focus stays on the header after Enter');
        await page.keyboard.press('Space');
        assert.deepEqual([await expanded('quiet'), await rows()], ['false', ROWS - 2]);
        assert.equal(await page.evaluate(() => document.activeElement.querySelector('.nav-group-name')?.textContent), 'quiet', 'focus stays on the header after Space');
        await toggleOf(page, 'project').click();
        assert.deepEqual((await stored()).sort(), [keyOf('local', '/work/quiet'), keyOf('server-gz8q', '/srv/project')].sort());
        await page.reload();
        await ready(page);
        assert.deepEqual([await expanded('quiet'), await expanded('project'), await expanded('Kvist'), await rows()], ['false', 'false', 'true', ROWS - 2 - 3]);
    });

    await check('collapse with storage that throws: the state lives in memory and nothing breaks', async () => {
        const { page, errors } = await open({ init: () => {
            Storage.prototype.getItem = () => { throw new DOMException('blocked', 'SecurityError'); };
            Storage.prototype.setItem = () => { throw new DOMException('blocked', 'SecurityError'); };
        } });
        await toggleOf(page, 'quiet').click();
        assert.equal(await toggleOf(page, 'quiet').getAttribute('aria-expanded'), 'false');
        await toggleOf(page, 'mixed').click();
        assert.deepEqual([await toggleOf(page, 'quiet').getAttribute('aria-expanded'), await toggleOf(page, 'mixed').getAttribute('aria-expanded')], ['false', 'false']);
        assert.deepEqual(errors, []);
    });

    await check('collapse with damaged storage: every group starts open', async () => {
        for (const value of ['not json', '{"a":1}', '5', 'null']) {
            const { page, errors } = await open({ init: `localStorage.setItem('kvist.nav.collapsed', ${JSON.stringify(value)})` });
            assert.deepEqual(await page.locator('.nav-group-toggle').evaluateAll(list => list.map(n => n.getAttribute('aria-expanded'))), GROUPS.map(() => 'true'), value);
            assert.deepEqual(errors, []);
        }
    });

    await check('collapsed group carries the most urgent indicator of its sessions, in the same shape', async () => {
        const { page } = await open();
        const none = () => page.locator('.nav-group-toggle .nav-ind').count();
        assert.equal(await none(), 0, 'an open group shows nothing on its header');
        // Kvist holds all four kinds (waiting wins), mixed holds unread, failed and running (failed wins), fresh only unread.
        const expected = { Kvist: 'waiting', project: 'waiting', mixed: 'failed', fresh: 'unread', quiet: '' };
        const labels = { waiting: 'waiting for your answer', failed: 'failed', unread: 'unread' };
        const sample = { waiting: 'Update configuration', failed: 'Inspect unavailable workspace', unread: 'Review API boundaries' };
        const shapeOf = slot => JSON.stringify([slot.before, slot.after]), shapes = {};
        for (const [kind, title] of Object.entries(sample)) shapes[kind] = shapeOf(await slotOf(rowOf(page, title)));
        for (const [name, kind] of Object.entries(expected)) {
            await toggleOf(page, name).click();
            assert.equal(await toggleOf(page, name).locator('.nav-ind').count(), kind ? 1 : 0, `${name}: ${kind || 'no indicator'}`);
            if (kind) {
                const slot = await slotOf(toggleOf(page, name));
                assert.deepEqual([kindOf(slot), slot.role, slot.label, slot.w, slot.h], [kind, 'img', labels[kind], 14, 14], name);
                assert.equal(shapeOf(slot), shapes[kind], `${name}: the same shape as in a row`);
            }
            await toggleOf(page, name).click();
        }
        assert.equal(await none(), 0, 'open again, the headers show nothing');
    });

    await check('collapsed group with waiting inside: the indicator is on the header and the rows are gone', async () => {
        const { page } = await open();
        await toggleOf(page, 'project').click();
        assert.equal(await page.locator('.nav-session').filter({ hasText: 'Needs your answer' }).count(), 0);
        const slot = await slotOf(toggleOf(page, 'project'));
        assert.deepEqual([kindOf(slot), slot.label], ['waiting', 'waiting for your answer']);
        assert.equal(await page.getByRole('img', { name: 'waiting for your answer' }).count() >= 2, true, 'also the ones in the Kvist group rows');
        for (const name of ['Kvist', 'quiet', 'mixed', 'fresh']) await toggleOf(page, name).click();
        await page.locator('.nav-group-toggle .nav-ind').first().waitFor();
        await shot(page, 'collapsed-groups');
    });

    await check('Tab order: search, New session, Inbox, groups in order, rows inside open groups', async () => {
        const { page } = await open();
        await toggleOf(page, 'mixed').click();
        await page.locator('#search-button').focus();
        const expected = ['new-session', 'inbox-button'];
        const titles = { Kvist: states().map(s => s.title), quiet: ['Quiet one', 'Quiet two'], mixed: null, fresh: ['Fresh result'], project: ['Remote review', 'Needs your answer', 'Unread remote'] };
        for (const [, , name] of GROUPS) expected.push(`toggle:${name}`, `add:New session in ${name}`, ...(titles[name] || []).map(t => `row:${t}`));
        expected.push('add-workspace');
        const seen = [];
        for (let i = 0; i < expected.length; i++) {
            await page.keyboard.press('Tab');
            seen.push(await page.evaluate(() => { const a = document.activeElement; return a.id || (a.matches('.nav-group-toggle') ? `toggle:${a.querySelector('.nav-group-name').textContent}` : a.matches('.nav-group-add') ? `add:${a.getAttribute('aria-label')}` : `row:${a.querySelector('.nav-title')?.textContent}`); }));
        }
        assert.deepEqual(seen, expected);
    });

    await check('keyboard focus ring: a full 2px --accent ring on a row, a group header and "+", on all four sides, inside every clipping ancestor', async () => {
        const { page } = await open();
        const accent = await rgbOf(page, 'accent'), accentColor = await tokenColor(page, 'accent');
        const subjects = [['session row', rowOf(page, 'Update configuration')], ['group header', toggleOf(page, 'Kvist')], ['"+" button', addOf(page, 'Kvist')]];
        for (const [name, subject] of subjects) {
            await subject.focus();
            await page.keyboard.press('Shift+Tab');
            await page.keyboard.press('Tab');
            await page.mouse.move(1300, 450);
            await frames(page);
            assert(await subject.evaluate(n => n === document.activeElement && n.matches(':focus-visible')), `${name}: keyboard focus`);
            const ring = await subject.evaluate(n => {
                const s = getComputedStyle(n), r = n.getBoundingClientRect(), grow = parseFloat(s.outlineOffset) + parseFloat(s.outlineWidth);
                const outer = { left: r.left - grow, top: r.top - grow, right: r.right + grow, bottom: r.bottom + grow }, clips = [];
                for (let a = n.parentElement; a; a = a.parentElement) {
                    const c = getComputedStyle(a);
                    if (c.overflowX === 'visible' && c.overflowY === 'visible' && !/paint|strict|content/.test(c.contain) && c.contentVisibility === 'visible') continue;
                    const b = a.getBoundingClientRect();
                    clips.push({ clip: a.id || a.className || a.tagName, inside: outer.left >= b.left - 0.01 && outer.right <= b.right + 0.01 && outer.top >= b.top - 0.01 && outer.bottom <= b.bottom + 0.01 });
                }
                return { style: s.outlineStyle, width: s.outlineWidth, color: s.outlineColor, offset: s.outlineOffset, clips };
            });
            assert.deepEqual([ring.style, ring.width, ring.color, ring.offset], ['solid', '2px', accentColor, '-2px'], `${name}: ${JSON.stringify(ring)}`);
            assert(ring.clips.length > 0 && ring.clips.every(c => c.inside), `${name}: the ring is inside what clips it ${JSON.stringify(ring.clips)}`);
            const box = await subject.boundingBox(), x = Math.round(box.x), y = Math.round(box.y), w = Math.round(box.width), h = Math.round(box.height);
            const png = decodePng(await page.screenshot({ clip: { x, y, width: w, height: h } }));
            const sides = { top: [w >> 1, 1], bottom: [w >> 1, h - 2], left: [1, h >> 1], right: [w - 2, h >> 1] };
            for (const [side, [px, py]] of Object.entries(sides)) {
                const found = png.at(px, py);
                assert(found.every((value, i) => Math.abs(value - accent[i]) <= 4), `${name}: the ${side} side of the ring is ${found}, not the accent ${accent}`);
            }
        }
        await subjects[0][1].focus();
        await shot(page, 'focus-ring-row');
    });

    await check('reduced motion: the ring stays, the spin stops', async () => {
        const { page } = await open();
        await page.emulateMedia({ reducedMotion: 'reduce' });
        const ring = await rowOf(page, 'Start the build').locator('.nav-ind').evaluate(n => { const s = getComputedStyle(n, '::before'); return [s.animationName, s.width, s.borderTopWidth, s.borderTopStyle]; });
        assert.deepEqual(ring, ['none', '12px', '2px', 'solid']);
    });

    await check('no horizontal scroll at 1440, 1280 and 390 (drawer open)', async () => {
        const { page } = await open();
        for (const [width, height] of [[1440, 900], [1280, 720], [390, 844]]) {
            await page.setViewportSize({ width, height });
            if (width === 390) { await page.locator('#toggle-sidebar').click(); await page.locator('#sidebar').waitFor({ state: 'visible' }); }
            await frames(page);
            assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `page at ${width}px`);
            assert(await page.locator('#navigation').evaluate(n => n.scrollWidth <= n.clientWidth), `navigation at ${width}px`);
            assert(await page.locator('#sidebar').evaluate(n => n.scrollWidth <= n.clientWidth), `sidebar at ${width}px`);
        }
    });

    await check('states screenshots, 1440x900 and 390x844', async () => {
        const { page } = await open();
        await shot(page, 'all-states');
        await page.mouse.move(1300, 450);
        await toggleOf(page, 'Kvist').hover();
        await ready(page);
        await page.screenshot({ path: `${shots}/group-hover-1440x900.png`, clip: { x: 0, y: 0, width: 248, height: 320 } });
    });

    await check('300 sessions: all rows drawn, the render time is measured', async () => {
        const { page } = await open({ nav: bulkNavigation() });
        assert.equal(await page.locator('.nav-session').count(), 300);
        const times = await page.evaluate(async () => {
            const { state } = await import('/assets/state/store.js'), { renderNavigation } = await import('/assets/features/navigation.js');
            const items = state.navigation.servers[0].workspaces[0].sessions.session_items, sync = [], frame = [];
            for (let i = 0; i < 10; i++) {
                items[0].title = `Changed ${i}`;
                const start = performance.now();
                renderNavigation(() => {}, () => {});
                document.querySelector('#navigation').lastElementChild.getBoundingClientRect();
                sync.push(performance.now() - start);
                await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                frame.push(performance.now() - start);
            }
            return { sync, frame };
        });
        const median = list => [...list].sort((a, b) => a - b)[Math.floor(list.length / 2)];
        console.log(`300 rows: render and layout median ${median(times.sync).toFixed(1)} ms (runs ${times.sync.map(t => t.toFixed(1)).join(', ')}); until the second frame median ${median(times.frame).toFixed(1)} ms`);
        assert(median(times.sync) <= 50, `the list renders in ${median(times.sync)} ms`);
        assert.equal(await page.locator('.nav-session').count(), 300);
        await shot(page, 'sessions-300');
    });

    await check('group spacing: 12px between the last row of a group and the next header, no margin above the first group', async () => {
        const { page } = await open();
        await toggleOf(page, 'quiet').click(); // a collapsed group is a header only: its gap counts too
        const groups = await page.evaluate(() => [...document.querySelectorAll('.nav-group')].map(group => ({ margin: getComputedStyle(group).marginTop, head: group.querySelector('.nav-group-head').getBoundingClientRect().top, last: group.lastElementChild.getBoundingClientRect().bottom })));
        assert.equal(groups.length, GROUPS.length);
        assert.equal(groups[0].margin, '0px', 'the first group has no top margin');
        for (let i = 1; i < groups.length; i++) {
            assert.equal(groups[i].margin, '12px', `group ${i}: top margin`);
            const gap = groups[i].head - groups[i - 1].last;
            assert(gap >= 12 - 0.01, `group ${i}: ${gap}px between the previous group's last line and its header`);
        }
        await shot(page, 'group-spacing');
    });

    await check('Inbox badge: hidden at 0, the count up to 99, "99+" above', async () => {
        const { page, model } = await open();
        const badge = page.locator('#inbox-count');
        assert.equal(await badge.isHidden(), true, 'hidden at 0');
        for (const [count, text] of [[3, '3'], [99, '99'], [100, '99+'], [150, '99+']]) {
            model.nav.inbox = Array.from({ length: count }, (_, i) => ({ title: `Inbox ${i}`, session: `i${i}`, server_id: 'local', workspace: '/work/Kvist', state: 'finished' }));
            await page.evaluate(() => window.__sources.at(-1).emit({ id: Date.now(), event: { type: 'turn_end' } }));
            await page.waitForFunction(text => document.querySelector('#inbox-count').textContent === text, text);
            assert.equal(await badge.isVisible(), true, `${count}: shown`);
            if (count === 150) await shot(page, 'inbox-150');
        }
        model.nav.inbox = [];
        await page.evaluate(() => window.__sources.at(-1).emit({ id: Date.now(), event: { type: 'turn_end' } }));
        await badge.waitFor({ state: 'hidden' });
    });

    await check('running and starting ring: a 2px --accent arc over a 25% track, the arc at least 3:1 against --bg-1', async () => {
        const { page } = await open();
        const [accent, bg1] = [await rgbOf(page, 'accent'), await rgbOf(page, 'bg-1')];
        const lin = v => { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
        const lum = ([r, g, b]) => 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
        const ratio = (Math.max(lum(accent), lum(bg1)) + 0.05) / (Math.min(lum(accent), lum(bg1)) + 0.05);
        console.log(`ring arc contrast against --bg-1: ${ratio.toFixed(2)}:1`);
        assert(ratio >= 3, `contrast ${ratio.toFixed(2)}:1`);
        for (const title of ['Run regression tests', 'Start the build']) {
            const ring = await rowOf(page, title).locator('.nav-ind').evaluate(n => { const s = getComputedStyle(n, '::before'); return { width: s.borderTopWidth, arc: s.borderTopColor, track: s.borderBottomColor, trackWidth: s.borderBottomWidth }; });
            assert.deepEqual([ring.width, ring.trackWidth, ring.arc], ['2px', '2px', await tokenColor(page, 'accent')], title);
            const alpha = /(?:\/|,)\s*([\d.]+)\)$/.exec(ring.track);
            assert(alpha && Math.abs(Number(alpha[1]) - 0.25) < 0.01, `${title}: the track is ${ring.track}`);
        }
    });

    await check('no runtime errors on any page', async () => {
        const { page, errors } = await open();
        await toggleOf(page, 'Kvist').click();
        assert.deepEqual(errors, []);
    });
    console.log(`PASS ${checks.length} sidebar scenarios`);
}
finally {
    await browser.close();
}

// The app in a browser context of its own with the API mocked in the page: the harness of motion-states.mjs
// and motion-metrics.mjs. The asset server is real (KVIST_WEB_URL); /api/v1 is answered here from a model that
// the scenario can change while the page runs (a session closing, a journal growing, a delay on every request).
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || process.argv[2] || 'playwright');
export const base = process.env.KVIST_WEB_URL || 'http://127.0.0.1:9672';
export const WORKSPACE = '/work/Kvist';
export const browser = await chromium.launch({ headless: true, ...((process.env.CHROMIUM_EXECUTABLE || process.argv[3]) ? { executablePath: process.env.CHROMIUM_EXECUTABLE || process.argv[3] } : {}) });
export const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
export const frames = (page, count = 2) => page.evaluate(count => new Promise(resolve => { const step = () => (count-- <= 0 ? resolve() : requestAnimationFrame(step)); step(); }), count);

export const NOTICE = 'conversation over · Enter — new session';
// Journal records: seq, turn, ts and the event, as /api/v1/history returns them.
export const records = (events, { turn = 1, from = 0 } = {}) => events.map((event, i) => ({ seq: from + i + 1, turn, ts: 1780000000 + from + i, event }));
export const session = (name, title, extra = {}) => ({ name, title, execution: 'idle', unread: false, last_event_id: 0, ...extra });
const navigationOf = model => {
    for (const item of model.sessions) {
        item.last_event_id = (model.histories[item.name] || []).length;
        if (model.closed.has(item.name)) Object.assign(item, { closed: true, closed_notice: NOTICE });
    }
    return { servers: [{ server: { id: 'local', name: 'local', kind: 'local' }, connection: { state: 'connected' }, workspaces: [{ path: WORKSPACE, sessions: { session_items: model.sessions } }] }], inbox: [] };
};

// A journal of `count` tool calls of different heights: batches of one to four calls, outputs of 0 to 30 lines,
// every seventh call failed (a failed call with output opens by default).
export function toolJournal(count = 300) {
    const events = [{ type: 'user', text: 'Go through the whole workspace.' }];
    let id = 0, batch = 0;
    while (id < count) {
        const size = 1 + (batch % 4), batchId = `batch-${batch++}`;
        for (let k = 0; k < size && id < count; k++, id++) {
            const lines = (id * 7) % 31, failed = id % 7 === 3, shell = id % 3 === 0;
            const output = Array.from({ length: lines }, (_, i) => `line ${i} of call ${id}: ${'x'.repeat(10 + ((i * 13 + id) % 60))}`).join('\n');
            const args = shell ? { command: `npm test -- ${id}` } : { path: `src/module-${id}/index.ts` };
            const action_type = shell ? 'run_shell' : 'read_file';
            events.push({ type: 'step', step: id + 1, call_id: `c${id}`, batch_id: batchId, action_type, action_args: args });
            events.push({ type: 'action_result', step: id + 1, call_id: `c${id}`, batch_id: batchId, action_type, ok: !failed, exit_code: failed ? 1 : 0, output, summary: `${lines} lines` });
        }
    }
    events.push({ type: 'final', message: 'Done.' }, { type: 'turn_end' });
    return events;
}

export async function open({ sessions = [session('s1', 'First session'), session('s2', 'Second session')], histories = {}, closed = [], size = { width: 1440, height: 900 }, delay = 0, onSend, context = null, reducedMotion = null, colorScheme = null } = {}) {
    const own = !context;
    context ??= await browser.newContext({ viewport: size, ...(reducedMotion && { reducedMotion }), ...(colorScheme && { colorScheme }) });
    const model = { sessions, histories: { ...histories }, closed: new Set(closed), delay, onSend, created: 0, requests: [], pages: [] };
    model.navigation = () => navigationOf(model);
    await context.addInitScript(() => {
        window.__sources = [];
        window.EventSource = class {
            constructor(url) { this.url = url; window.__sources.push(this); setTimeout(() => this.onopen?.({}), 0); }
            close() {}
        };
        // Every element.animate call of the app, with the properties its keyframes touch.
        window.__animated = [];
        const animate = Element.prototype.animate;
        Element.prototype.animate = function (keyframes, options) {
            const frames = Array.isArray(keyframes) ? keyframes : [keyframes];
            window.__animated.push({ id: this.id, className: String(this.className), from: frames[0]?.transform ?? '', properties: [...new Set(frames.flatMap(f => Object.keys(f)).filter(key => !['offset', 'easing', 'composite'].includes(key)))], duration: options?.duration });
            return animate.call(this, keyframes, options);
        };
    });
    await context.route('**/api/v1/**', async route => {
        const request = route.request(), url = new URL(request.url()), path = url.pathname.replace('/api/v1', ''), json = value => route.fulfill({ json: value });
        const body = request.method() === 'POST' ? request.postDataJSON() : null;
        model.requests.push({ path, body, query: Object.fromEntries(url.searchParams) });
        if (model.delay && path !== '/bootstrap') await sleep(model.delay);
        if (path === '/bootstrap') return json({ protocol_version: 1, navigation: navigationOf(model), event_stream: { cursor: 0, epoch: 'e' } });
        if (path === '/navigation') return json(navigationOf(model));
        if (path === '/history') {
            const name = url.searchParams.get('session'), after = Number(url.searchParams.get('after') || 0);
            return json({ session: name, events: (model.histories[name] || []).filter(r => r.seq > after), has_more: false, ...(model.closed.has(name) && { closed: true, closed_notice: NOTICE }) });
        }
        if (path === '/configuration') return json({ provider: 'p', setup_required: false, providers: [], custom_providers: [] });
        if (path === '/sessions') {
            const name = `new-${++model.created}`;
            model.sessions.unshift(session(name, name));
            model.histories[name] = [];
            return json({ active_session: name });
        }
        if (path === '/send') {
            if (model.onSend) await model.onSend(body, model);
            return json({ ok: true });
        }
        return json({ ok: true });
    });
    const page = await context.newPage();
    model.pages.push(page);
    page.errors = [];
    page.on('pageerror', error => page.errors.push(error.message));
    await page.goto(base);
    await ready(page);
    return { page, context, own, model };
}
export async function ready(page) {
    await page.locator('#app').waitFor({ state: 'visible' });
    await page.waitForFunction(() => document.querySelector('#history-status')?.hidden);
    await page.evaluate(() => document.fonts.load('12px "JetBrains Mono"'));
    await frames(page);
}
// A second page of the same context (another tab): the same mocked API and model.
export async function openTab(first) {
    const page = await first.context.newPage();
    first.model.pages.push(page);
    page.errors = [];
    page.on('pageerror', error => page.errors.push(error.message));
    await page.goto(base);
    await ready(page);
    return page;
}
// A live envelope of the broker for one session; `durable` marks an event the journal also has.
export const emit = (page, event, { durable = false, session = 's1' } = {}) =>
    page.evaluate(([event, durable, session]) => window.__sources.at(-1).onmessage({ data: JSON.stringify({ id: Date.now() + Math.random(), server_id: 'local', workspace: '/work/Kvist', session, durable, ts: Date.now() / 1000, event }) }), [event, durable, session]);
// Appends journal records to a session and announces the last one the way the broker does.
export async function journal(model, page, name, events, { announce = true } = {}) {
    const all = model.histories[name] ||= [];
    const added = records(events, { turn: 2, from: all.length });
    all.push(...added);
    if (announce) await emit(page, added.at(-1).event, { durable: true, session: name });
    return added;
}
export const median = values => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)];
export async function closeAll() { await browser.close(); }

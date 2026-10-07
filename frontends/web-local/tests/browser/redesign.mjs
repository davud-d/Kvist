// Run against a real Kvist Web asset server. All mutation/state fixtures below
// are intercepted in this browser context, so no credentials or remote host needed.
// PLAYWRIGHT_MODULE=/path/to/playwright/index.mjs CHROMIUM_EXECUTABLE=/path/to/chrome node .../redesign.mjs
import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { fixture } from './fixtures.mjs';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || process.argv[2] || 'playwright');
const base = process.env.KVIST_WEB_URL || 'http://127.0.0.1:9672';
const output = process.env.KVIST_QA_OUTPUT || '/tmp/kvist-web-qa';
await mkdir(output + '/screenshots', { recursive: true });
const browser = await chromium.launch({ headless: true, ...((process.env.CHROMIUM_EXECUTABLE || process.argv[3]) ? { executablePath: process.env.CHROMIUM_EXECUTABLE || process.argv[3] } : {}) });
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage(), errors = [], checks = [], requests = [], trace = [], started = Date.now();
page.on('pageerror', e => errors.push(e.message));
const f = fixture();
let cursor = 0, failSend = false, trust = false, expired = false, historyDelay = 0, createDelay = 0, connectFail = false, connectLogDelay = 0;
await page.addInitScript(() => {
    window.__sources = [];
    window.EventSource = class {
        constructor(url) { this.url = url; window.__sources.push(this); setTimeout(() => this.onopen?.({}), 0); }
        close() { this.closed = true; }
        emit(data) { this.onmessage?.({ data: JSON.stringify(data) }); }
    };
});
await page.route('**/api/v1/**', async (route) => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname.replace('/api/v1', ''), body = request.method() === 'POST' ? request.postDataJSON() : null;
    trace.push({ ms: Date.now() - started, method: request.method(), path });
    if (body)
        requests.push({ path, body });
    const json = value => route.fulfill({ json: value });
    if (path === '/ssh-aliases') return json({aliases:['server-gz8q', 'build-box']});
    if (path === '/configuration') return json({provider:'test-provider',setup_required:false,providers:[{id:'test-provider',label:'Test provider',defaults:{fast:'test-model',smart:'test-smart'},credential_fields:[],options:['test-model','test-smart']}],custom_providers:[],effort:'high',effort_levels:['low','high'],model_roles:['fast','smart'],environment_overrides:[],registration_url:'https://anymodel.org/app/register'});
    if (path === '/command') return json({ok:true});
    if (path === '/bootstrap')
        return expired ? route.fulfill({ status: 401, json: { error: 'Expired' } }) : json({ protocol_version: 1, navigation: f.inbox(), event_stream: { cursor, epoch: 'test-epoch' } });
    if (path === '/navigation')
        return json(f.inbox());
    if (path === '/history') {
        if (historyDelay)
            await new Promise(r => setTimeout(r, historyDelay));
        const all = f.histories[url.searchParams.get('session')] || [], after = Number(url.searchParams.get('after')), events = all.filter(e => e.seq > after).slice(0, 1000);
        return json({ events, has_more: events.at(-1)?.seq < all.length });
    }
    if (path === '/changes')
        return json({ report: '--- a/src/recovery.ts\n+++ b/src/recovery.ts\n@@ -1,2 +1,2 @@\n-old cursor\n+confirmed cursor\n' + Array.from({ length: 5000 }, (_, i) => `+line ${i}`).join('\n') });
    if (path === '/sessions') {
        if (createDelay)
            await new Promise(r => setTimeout(r, createDelay));
        const name = 'new-' + Date.now();
        f.histories[name] = [];
        f.sessions.unshift({ name, title: 'New session', execution: 'idle', unread: false, last_event_id: 0 });
        return json({ active_session: name });
    }
    if (path === '/send') {
        if (failSend)
            return route.fulfill({ status: 500, json: { ok: false, error: 'Test connection unavailable' } });
        const session = f.sessions.find(s => s.name === body.session);
        if (session)
            session.execution = 'running';
        return json({ ok: true });
    }
    if (path === '/stop') {
        const session = f.sessions.find(s => s.name === body.session);
        if (session)
            session.execution = 'stopped';
        return json({ ok: true, state: 'running' });
    }
    if (path === '/read') {
        const session = f.sessions.find(s => s.name === body.session);
        if (session) {
            session.last_read_event_id = body.seq;
            session.unread = session.last_event_id > body.seq;
        }
        return json({ ok: true });
    }
    if (path === '/respond') {
        f.sessions.find(s => s.name === 'waiting').execution = 'running';
        return json({ ok: true });
    }
    if (path === '/servers') {
        f.navigation.servers.push({ server: body, connection: { state: 'offline' }, workspaces: [] });
        return json({ server: body });
    }
    if (path === '/servers/connect') {
        if (connectFail) return json({state:'failed',error:'SSH authentication failed: configured key rejected'});
        if (trust && !body.fingerprint)
            return json({ state: 'trust_required', host: 'new-host', key_type: 'ED25519', fingerprint: 'SHA256:test-fingerprint' });
        const logs = [];
        if (connectLogDelay) {
            const first = {entry_id:'live-first',stream:'status',text:'Running custom setup…'};
            logs.push(first);
            const sendLog = entry => page.evaluate(envelope => window.__sources.at(-1).emit(envelope), {id:++cursor,server_id:body.server_id,workspace:'',session:'',event:{type:'connection.log',attempt_id:body.attempt_id,...entry}});
            await sendLog(first);
            await new Promise(r=>setTimeout(r,connectLogDelay));
            const last = {entry_id:'live-last',stream:'stdout',text:'Setup finished. <script>ignored()</script>\n'};
            logs.push(last);
            await sendLog(last);
        }
        const server = f.navigation.servers.find(s => s.server.id === body.server_id);
        server.connection.state = 'connected';
        return json({ state: 'connected', logs, attempt_id:body.attempt_id });
    }
    if (path === '/workspaces') {
        f.navigation.servers.find(s => s.server.id === body.server_id).workspaces.push({ path: body.path, sessions: { session_items: [] } });
        return json({ workspace: body.path });
    }
    if (path === '/config-sync')
        return json({ general: true, credentials: [{ id: 'test_api_key', label: 'Test API key', present: true }] });
    throw new Error(`Unhandled test route ${path}`);
});
// KVIST_ONLY is a regular expression over scenario names; it runs a subset, e.g. in a repeat loop.
const only = process.env.KVIST_ONLY ? new RegExp(process.env.KVIST_ONLY) : null;
const check = async (name, fn) => {
    if (only && !only.test(name))
        return;
    try { await fn(); checks.push(name); console.log('PASS', name); }
    finally { await page.setViewportSize({ width: 1440, height: 900 }); }
};
const shot = async (name) => { await page.waitForTimeout(180); await page.screenshot({ path: `${output}/screenshots/${name}.png` }); };
// The server selector is not rendered (SHOW_SERVERS is false); the server dialog opens from the command palette.
const openServers = async () => { await page.keyboard.press('Control+k'); await page.locator('#palette-input').fill('Switch server'); await page.keyboard.press('Enter'); await page.locator('#modal .modal-card').waitFor(); };
// Opens the connect dialog and waits until its alias response is handled: the dialog re-renders
// #server-fields when /ssh-aliases returns, which adds #ssh-alias-picker and replaces every input.
const openConnectDialog = async () => {
    await openServers();
    const aliases = page.waitForResponse(response => new URL(response.url()).pathname.endsWith('/api/v1/ssh-aliases'));
    await page.getByRole('button', {name:'＋ Connect server',exact:true}).click();
    await aliases;
    await page.locator('#server-fields #ssh-alias-picker').waitFor();
};
const select = async (title) => page.getByRole('button', { name: new RegExp('^' + title + ',') }).click();
const emit = async (event, session = 'review', durable = false) => { await page.evaluate(envelope => window.__sources.at(-1).emit(envelope), { id: ++cursor, server_id: 'local', workspace: f.workspace, session, event, durable }); };
const durable = async (event, session = 'review') => { const list = f.histories[session]; list.push({ seq: list.length + 1, turn: 2, ts: 1780000100, event }); const item = f.sessions.find(s => s.name === session); if (item) {
    item.last_event_id = list.length;
    item.unread = true;
} await emit(event, session, true); };
try {
    await page.goto(base);
    await page.locator('#history-status').waitFor({ state: 'hidden' });
    await page.locator('#conversation .message').first().waitFor();
    await check('Page identity, meaningful screen and no inspector', async () => { assert.equal(await page.title(), 'Kvist'); assert(await page.locator('#composer').isVisible()); assert.equal(await page.locator('.inspector').count(), 0); assert(await page.locator('#workspace-view').isHidden()); });
    await shot('conversation');
    await check('Context survives report calls; usage totals', async () => { assert.equal(await page.locator('#context-button').textContent(), 'Context 54%'); await page.locator('#context-button').click(); assert.match(await page.locator('#inspector-content').innerText(), /69,120/); await page.getByRole('tab', { name: 'Usage', exact: true }).click(); assert.match(await page.locator('#inspector-content').innerText(), /1,300/); await shot('usage'); await page.keyboard.press('Escape'); assert(await page.locator('#context-button').evaluate(n => n === document.activeElement)); });
    await check('Session switching preserves drafts', async () => { await page.locator('#prompt').fill('draft in review'); await select('Review API boundaries'); await page.locator('#history-status').waitFor({ state: 'hidden' }); assert.match(await page.locator('#conversation').textContent(), /single versioned/); await select('Improve session recovery'); assert.equal(await page.locator('#prompt').inputValue(), 'draft in review'); });
    await check('Unread requires visible dwell, modal blocks receipt', async () => { f.sessions.find(s => s.name === 'unread').unread = true; await select('Review API boundaries'); await openServers(); const before = requests.filter(r => r.path === '/read' && r.body.session === 'unread').length; await page.waitForTimeout(1600); assert.equal(requests.filter(r => r.path === '/read' && r.body.session === 'unread').length, before); await page.keyboard.press('Escape'); await page.waitForTimeout(1700); assert(requests.some(r => r.path === '/read' && r.body.session === 'unread' && r.body.seq === 3)); });
    await check('Runtime selector and remote session', async () => { await openServers(); assert(await page.getByRole('button', { name: /gpu-box Offline/ }).isVisible()); await shot('server-selector'); await page.getByRole('button', { name: /server-gz8q Connected/ }).click(); await page.locator('#history-status').waitFor({ state: 'hidden' }); assert.equal(await page.locator('#session-server').textContent(), 'server-gz8q'); assert.match(await page.locator('#conversation').textContent(), /Remote session history/); await shot('remote'); await openServers(); await page.getByRole('button', { name: 'Local Connected', exact: true }).click(); });
    await check('Model control uses core catalog and exposes active-session changes', async () => { await emit({ type: 'hello', models: { fast: 'test-model', smart: 'test-smart' }, provider: 'test-provider', effort: 'high' }); await page.locator('#model-selector').click(); await page.locator('#provider-form').waitFor(); assert.equal(await page.locator('[name=fast]').inputValue(), 'test-model'); assert.equal(await page.locator('[name=smart]').inputValue(), 'test-smart'); assert(await page.getByRole('button',{name:'Use model in this session'}).isVisible()); await shot('model'); await page.locator('#model-pick').selectOption(''); await page.locator('[name=fast]').fill('test-next'); await page.getByRole('button',{name:'Use model in this session'}).click(); await page.locator('#modal').waitFor({state:'hidden'}); assert(requests.some(r=>r.path==='/command' && r.body.name==='models' && r.body.args[0]==='test-next')); await emit({type:'command_result',name:'models',ok:true,message:'Models updated.',effects:{models:{fast:'test-next',smart:'test-smart'}}}); assert.equal(await page.locator('#model-label').textContent(),'test-next'); });
    await check('All tools open, keyboard tabs, Esc and focus return', async () => {
        for (const tab of ['files', 'diff', 'terminal', 'activity', 'context', 'usage']) {
            await page.keyboard.press('Control+k');
            await page.locator('#palette-input').fill('Open ' + tab);
            await page.keyboard.press('Enter');
            await page.getByRole('tab', { name: new RegExp('^' + tab + '$', 'i') }).waitFor();
            await page.waitForTimeout(80);
            assert(await page.locator('#workspace-view').isVisible());
            await shot(tab);
            if (tab === 'files')
                assert.match(await page.locator('#inspector-content').innerText(), /src\/recovery/);
            if (tab === 'diff') {
                assert.equal(await page.locator('.diff-line').count(), 1000);
                await page.getByRole('button', { name: 'Load more lines' }).click();
                assert.equal(await page.locator('.diff-line').count(), 2000);
            }
            if (tab === 'terminal')
                assert.match(await page.locator('#inspector-content').innerText(), /24 tests passed/);
            await page.keyboard.press('Escape');
            // The view fades out for --dur-fast and is hidden when that animation has finished.
            await page.locator('#workspace-view').waitFor({ state: 'hidden' });
        }
    });
    await check('Command palette search and workspace selection', async () => { await page.keyboard.press('Control+k'); await shot('command-palette'); await page.keyboard.press('Escape'); await page.keyboard.press('Control+p'); await page.locator('#palette-input').fill('no-such-session'); assert.match(await page.locator('#palette-results').innerText(), /No matching/); await page.locator('#palette-input').fill('Review API'); await page.keyboard.press('Enter'); assert.equal(await page.locator('#session-title').textContent(), 'Review API boundaries'); });
    await check('Waiting approval / error UI', async () => { await select('Update configuration'); await page.getByRole('button', { name: 'Allow', exact: true }).waitFor(); assert.equal(await page.locator('#agent-state').textContent(), 'Needs input'); await shot('waiting'); await page.getByRole('button', { name: 'Allow', exact: true }).click(); assert(requests.some(r => r.path === '/respond' && r.body.response.request_id === 'approval-1')); await select('Inspect unavailable workspace'); const notice = page.locator('#conversation .notice-error'); await notice.waitFor(); assert.match(await notice.textContent(), /The workspace is unavailable/); assert.equal(await page.locator('#conversation .message.error').count(), 0); await shot('error'); });
    await check('New session, composer, failed send keeps the text in its block, Retry, send and stop', async () => { await page.locator('#new-session').click(); await page.waitForFunction(() => document.querySelector('#session-title').textContent === 'New session'); await shot('empty'); failSend = true; await page.locator('#prompt').fill('A test task'); await page.keyboard.press('Control+Enter'); await page.waitForTimeout(80); assert.equal(await page.locator('#prompt').inputValue(), '', 'the text is in its block, not back in the input'); assert.match(await page.locator('.message.user').last().innerText(), /A test task[\s\S]*Not sent[\s\S]*Retry/); failSend = false; await page.locator('.message-failed button').click(); await page.locator('#stop-agent').waitFor(); assert.equal(await page.locator('.message.user').count(), 1, 'a retry that succeeds leaves one message'); assert.equal(await page.locator('#prompt').inputValue(), ''); await page.locator('#stop-agent').click(); await page.waitForTimeout(220); assert.deepEqual(await page.locator('#agent-state').evaluate(n => [n.dataset.state, n.textContent, n.hidden]), ['stopped', '', true]); });
    await check('Streaming coalesces and durable commit does not duplicate', async () => { await select('Improve session recovery'); await durable({ type: 'stream_start', stream_id: 'live-2' }); await page.waitForTimeout(60); const before = await page.locator('.message').count(); await page.evaluate(() => { window.__mutations = 0; window.__observer = new MutationObserver(r => window.__mutations += r.length); window.__observer.observe(document.querySelector('#conversation'), { childList: true, subtree: true }); }); await page.evaluate(({ cursor, workspace }) => { for (let i = 0; i < 2000; i++)
        window.__sources.at(-1).emit({ id: cursor + i, server_id: 'local', workspace, session: 'review', event: { type: 'stream_chunk', stream_id: 'live-2', text: 'Streaming ' + i }, durable: false }); }, { cursor, workspace: f.workspace }); await page.waitForTimeout(80); assert.match(await page.locator('#conversation').textContent(), /Streaming 1999/); assert.equal(await page.locator('.message').count(), before); assert(await page.evaluate(() => window.__mutations) < 20); await shot('running'); await durable({ type: 'stream_commit', stream_id: 'live-2', text: 'Streaming **finished**.' }); await page.waitForTimeout(80); assert.equal(await page.locator('.message').count(), before); await page.evaluate(() => window.__observer.disconnect()); });
    await check('Background completion gets unread and recovers on selection', async () => { await durable({ type: 'stream_commit', stream_id: 'background-stream', text: 'Background tests finished.' }, 'running'); f.sessions.find(s => s.name === 'running').execution = 'finished'; await durable({ type: 'turn_end' }, 'running'); await page.waitForTimeout(250); assert(await page.getByRole('button', { name: /Run regression tests.*Unread/ }).locator('.ind-unread').isVisible()); await shot('background-unread'); await select('Run regression tests'); await page.locator('#history-status').waitFor({ state: 'hidden' }); assert.match(await page.locator('#conversation').textContent(), /Background tests finished/); await page.waitForTimeout(1650); assert(!f.sessions.find(s => s.name === 'running').unread); });
    await check('Reconnect keeps transcript and recovers actual missed events', async () => { await select('Improve session recovery'); const text = await page.locator('#conversation').textContent(); await page.evaluate(() => window.__sources.at(-1).onerror({})); assert.equal(await page.locator('#connection-label').textContent(), 'Reconnecting…'); assert.equal(await page.locator('#conversation').textContent(), text); await shot('reconnecting'); f.histories.review.push({ seq: f.histories.review.length + 1, turn: 2, event: { type: 'message', markdown: 'Recovered result' } }); await page.evaluate(() => window.__sources.at(-1).onopen({})); await page.getByText('Recovered result', { exact: true }).waitFor(); assert.match(await page.locator('#toasts').innerText(), /recovered 1 events/); });
    await check('Remote stream reset does not replace controller epoch', async () => { await emit({ type: 'stream.reset', remote: true, epoch: 'remote-epoch' }); assert.match(await page.evaluate(() => window.__sources.at(-1).url), /epoch=test-epoch/); await emit({ type: 'stream.reset', epoch: 'next-epoch' }); await page.waitForTimeout(50); assert.match(await page.evaluate(() => window.__sources.at(-1).url), /epoch=next-epoch/); });
    await check('Connection dialog methods, trust flow and workspace destination', async () => { await openServers(); await page.getByRole('button', { name: '＋ Connect server', exact: true }).click(); await shot('connect-ssh-config'); await page.getByRole('tab', { name: 'Manual', exact: true }).click(); await page.getByLabel('Host', { exact: true }).fill('new-host'); await page.getByLabel('Username', { exact: true }).fill('developer'); await page.getByLabel('Password', { exact: true }).fill('test-only'); await shot('connect-manual'); trust = true; await page.getByRole('button', { name: 'Connect', exact: true }).click(); await page.getByRole('button', { name: 'Trust and connect' }).waitFor(); await shot('host-trust'); await page.getByRole('button', { name: 'Trust and connect' }).click(); await page.getByRole('button',{name:'Open workspace',exact:true}).click(); await page.getByLabel('Absolute directory path').waitFor(); assert(requests.some(r => r.path === '/servers/connect' && r.body.fingerprint === 'SHA256:test-fingerprint')); await page.keyboard.press('Escape'); await openServers(); await page.getByRole('button', { name: '＋ Connect server', exact: true }).click(); await page.getByRole('tab', { name: 'SSH key', exact: true }).click(); assert(await page.getByLabel('Private key path').isVisible()); await shot('connect-key'); await page.keyboard.press('Escape'); });
    await check('Long conversation and scroll-aware unread', async () => { await select('Long conversation'); await page.locator('#history-status').waitFor({ state: 'hidden' }); assert.equal(await page.locator('#conversation .message').count(), 2200); await page.locator('#conversation').evaluate(n => n.scrollTop = 0); await page.waitForTimeout(70); assert(await page.locator('#jump-latest').isVisible()); await shot('long-conversation'); await page.locator('#jump-latest').click(); });
    await select('Improve session recovery');
    await check('Responsive viewports and zoom-equivalent CSS viewports', async () => {
        for (const [width, height] of [[1280, 720], [1440, 900], [1920, 1080], [2560, 1440]])
            for (const zoom of [1, 1.1, 1.25]) {
                // Browser page zoom changes the available CSS viewport. DPR is independent.
                await page.setViewportSize({ width: Math.round(width / zoom), height: Math.round(height / zoom) });
                await page.waitForTimeout(60);
                const bounds = await page.locator('#composer').boundingBox(), viewport = page.viewportSize();
                assert(bounds.x >= 0 && bounds.y >= 0 && bounds.x + bounds.width <= viewport.width + 1 && bounds.y + bounds.height <= viewport.height);
                assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
                await shot(`viewport-${width}x${height}-${Math.round(zoom * 100)}`);
            }
        await page.setViewportSize({ width: 390, height: 844 });
        // The drawer slides off-canvas with a transform, so "hidden" means its right edge reached x <= 0.
        const sidebarRight = () => page.evaluate(() => document.querySelector('#sidebar').getBoundingClientRect().right);
        await page.waitForFunction(() => document.querySelector('#sidebar').getBoundingClientRect().right <= 0);
        await page.locator('#toggle-sidebar').click();
        assert(await page.locator('#sidebar').isVisible());
        await shot('mobile-sidebar');
        await page.keyboard.press('Escape');
        await openServers();
        await shot('mobile-selector');
        await page.keyboard.press('Escape');
        await shot('mobile');
        await page.setViewportSize({ width: 1440, height: 900 });
        assert((await sidebarRight()) > 0);
        await page.locator('#toggle-sidebar').click();
        await shot('collapsed-sidebar');
        await page.locator('#toggle-sidebar').click();
    });
    await check('Late histories cannot overwrite the selected session', async () => {
        historyDelay = 180;
        await select('Review API boundaries');
        await select('Improve session recovery');
        await page.waitForTimeout(500);
        historyDelay = 0;
        assert.equal(await page.locator('#session-title').textContent(), 'Improve session recovery');
        assert.match(await page.locator('#conversation').textContent(), /Session recovery is now consistent/);
        assert.doesNotMatch(await page.locator('#conversation').textContent(), /single versioned API adapter/);
    });
    await check('Live stream reservation is reconciled in journal order', async () => {
        const list = f.histories.review;
        list.push({ seq: list.length + 1, turn: 3, event: { type: 'user', text: 'New turn arrives before its history' } });
        historyDelay = 100;
        await durable({ type: 'stream_start', stream_id: 'ordered-stream' });
        await emit({ type: 'stream_chunk', stream_id: 'ordered-stream', text: 'Ordered reply' });
        await page.waitForTimeout(220);
        historyDelay = 0;
        const messages = await page.locator('.message').allTextContents();
        assert.match(messages.at(-2), /New turn arrives/);
        assert.match(messages.at(-1), /Ordered reply/);
        await durable({ type: 'stream_commit', stream_id: 'ordered-stream', text: 'Ordered reply' });
    });
    await check('Stream chunks append deltas, replace on append=false, and commit sets the final text', async () => {
        const lastMessage = async () => (await page.locator('.message').allTextContents()).at(-1);
        await durable({ type: 'stream_start', stream_id: 'delta-stream' });
        await emit({ type: 'stream_chunk', stream_id: 'delta-stream', text: 'Hello ', append: true });
        await emit({ type: 'stream_chunk', stream_id: 'delta-stream', text: 'world', append: true });
        await page.waitForTimeout(120);
        assert.match(await lastMessage(), /Hello world$/);
        await emit({ type: 'stream_chunk', stream_id: 'delta-stream', text: 'Rewritten', append: false });
        await page.waitForTimeout(120);
        const rewritten = await lastMessage();
        assert.match(rewritten, /Rewritten$/);
        assert.doesNotMatch(rewritten, /Hello/);
        await durable({ type: 'stream_commit', stream_id: 'delta-stream', text: 'Final text' });
        await page.waitForTimeout(120);
        assert.match(await lastMessage(), /Final text$/);
    });
    await check('Untrusted Markdown stays text and safe links', async () => {
        await durable({ type: 'message', markdown: '<img src=x onerror=alert(1)> **safe** [unsafe](javascript:alert(1))' });
        await page.waitForTimeout(100);
        assert.equal(await page.locator('#conversation img[src="x"]').count(), 0);
        assert.equal(await page.locator('#conversation a[href^="javascript:"]').count(), 0);
    });
    await check('Expired browser credentials renew without reload or lost draft', async () => {
        const before = await page.locator('#conversation').textContent();
        await page.locator('#prompt').fill('Keep this draft');
        const count = await page.evaluate(() => window.__sources.length);
        expired = true;
        await page.evaluate(() => window.__sources.at(-1).onerror({}));
        await page.waitForFunction(n => window.__sources.length > n, count);
        expired = false;
        assert.equal(await page.locator('#prompt').inputValue(), 'Keep this draft');
        assert.equal(await page.locator('#conversation').textContent(), before);
    });
    await check('Many sessions and large terminal snapshots stay usable', async () => {
        for (let i = 0; i < 1200; i++)
            f.sessions.push({ name: 'bulk-' + i, title: 'Background session ' + i, execution: 'idle', unread: false });
        await emit({ type: 'agent.state_changed', state: 'running' });
        await page.waitForTimeout(400);
        assert((await page.locator('.nav-session').count()) >= 1200);
        await emit({ type: 'step', step: 90, call_id: 'large-log', action_type: 'run_shell', action_args: { command: 'test command' } });
        await emit({ type: 'action_output', step: 90, call_id: 'large-log', output: Array.from({ length: 6000 }, (_, i) => 'log line ' + i).join('\n') });
        await page.getByRole('button', { name: 'Terminal', exact: true }).click();
        assert.equal(await page.locator('.diff-line').count(), 1000);
        await shot('large-terminal');
        await page.keyboard.press('Escape');
        await page.keyboard.press('Control+p');
        await page.locator('#palette-input').fill('Background session 1199');
        assert.match(await page.locator('#palette-results').innerText(), /1199/);
        await page.keyboard.press('Escape');
    });
    await check('Fast New session then Send allocates once and sends once', async () => {
        await select('Review API boundaries');
        createDelay = 300;
        const creates = requests.filter(r => r.path === '/sessions').length, sends = requests.filter(r => r.path === '/send').length;
        await page.locator('#new-session').click();
        await page.locator('#prompt').fill('Send while creating');
        await page.keyboard.press('Control+Enter');
        await page.keyboard.press('Control+Enter');
        await page.waitForTimeout(600);
        createDelay = 0;
        assert.equal(requests.filter(r => r.path === '/sessions').length, creates + 1);
        assert.equal(requests.filter(r => r.path === '/send').length, sends + 1);
        assert.equal(requests.filter(r => r.path === '/send').at(-1).body.text, 'Send while creating');
    });
    await check('Failed SSH connection is actionable and retry keeps identity', async () => {
        await openConnectDialog();
        await page.getByLabel('SSH alias', {exact:true}).fill('rejected-key-host');
        connectFail = true;
        await page.getByRole('button', {name:'Connect',exact:true}).click();
        await page.getByText('SSH authentication failed. Check your credentials and try again.', {exact:true}).waitFor();
        await shot('connection-error');
        const firstId = requests.filter(r => r.path === '/servers').at(-1).body.id;
        connectFail = false; trust = false;
        await page.getByRole('button', {name:'Connect',exact:true}).click();
        await page.getByRole('button',{name:'Open workspace',exact:true}).click();
        await page.getByLabel('Absolute directory path').waitFor();
        assert.equal(requests.filter(r => r.path === '/servers').at(-1).body.id, firstId);
        await page.keyboard.press('Escape');
    });
    await check('Connection logs stream before completion and stay until acknowledged', async () => {
        await page.setViewportSize({width:1440,height:900});
        await openConnectDialog();
        await page.getByLabel('SSH alias',{exact:true}).fill('setup-logs-host');
        connectLogDelay = 1600;
        await page.getByRole('button',{name:'Connect',exact:true}).click();
        await page.locator('.connection-log').getByText('Running custom setup…',{exact:true}).waitFor();
        assert.equal(await page.getByRole('button',{name:'Open workspace',exact:true}).count(),0);
        const attempt = requests.filter(r=>r.path==='/servers/connect').at(-1).body;
        await page.evaluate(envelope=>window.__sources.at(-1).emit(envelope),{id:++cursor,server_id:'unrelated-server',workspace:'',session:'',event:{type:'connection.log',attempt_id:attempt.attempt_id,entry_id:'other',stream:'stdout',text:'Foreign connection'}});
        assert(!await page.locator('.connection-log').innerText().then(text=>text.includes('Foreign connection')));
        await shot('connect-live-log');
        await page.getByRole('button',{name:'Open workspace',exact:true}).waitFor();
        connectLogDelay = 0;
        assert.equal(await page.locator('.connection-log script').count(),0);
        assert.match(await page.locator('.connection-log').innerText(), /<script>ignored/);
        assert.equal(await page.locator('.connection-log-line').count(),2);
        for (const [width,height] of [[1280,720],[1440,900],[1920,1080],[2560,1440],[640,720]]) {
            await page.setViewportSize({width,height});
            await page.getByRole('button',{name:'Open workspace',exact:true}).scrollIntoViewIfNeeded();
            assert(await page.locator('.modal-card').evaluate(n=>{const r=n.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&r.top>=0&&r.bottom<=innerHeight}));
            await shot(`connection-log-${width}x${height}`);
        }
        await page.getByRole('button',{name:'Open workspace',exact:true}).click();
        await page.getByLabel('Absolute directory path').waitFor();
        await page.keyboard.press('Escape');
        await page.setViewportSize({width:1440,height:900});
    });
    await check('No browser runtime errors', async () => assert.deepEqual(errors, []));
    await writeFile(output + '/results.json', JSON.stringify({ checks, errors }, null, 2));
    console.log(`PASS ${checks.length} browser scenarios`);
}
finally {
    await writeFile(output + '/trace.json', JSON.stringify({ trace, errors }, null, 2));
    await browser.close();
}

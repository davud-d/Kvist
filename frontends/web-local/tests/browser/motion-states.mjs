// The closed session banner and the motion system. Real asset server, API mocked in the page (app-mock.mjs);
// every scenario has a context of its own. Screenshots at 1440x900 and 390x844 go to KVIST_SHOTS (default
// /tmp/kvist-shots/5c); KVIST_ONLY is a regular expression over scenario names.
// PLAYWRIGHT_MODULE=... CHROMIUM_EXECUTABLE=... KVIST_WEB_URL=http://127.0.0.1:PORT node .../motion-states.mjs
//
// The feed container is #conversation (the spec calls it the feed). The client has no menus beside the
// overlays below, so "menus" are the narrow-window sidebar drawer here.
import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { browser, open, openTab, emit, journal, records, session, toolJournal, frames, sleep, NOTICE } from './app-mock.mjs';

const shots = process.env.KVIST_SHOTS || '/tmp/kvist-shots/5c';
await mkdir(shots, { recursive: true });
const only = process.env.KVIST_ONLY ? new RegExp(process.env.KVIST_ONLY) : null;
const live = [], checks = [];
const app = async options => { const result = await open(options); live.push(result.context); return result; };
const check = async (name, fn) => {
    if (only && !only.test(name)) return;
    try { await fn(); checks.push(name); console.log('PASS', name); }
    finally { await Promise.all(live.splice(0).map(context => context.close())); }
};

// --- journals
const HELLO = [{ type: 'user', text: 'Say hello.' }, { type: 'final', message: 'Hello, how can I help?' }, { type: 'turn_end' }];
const usage = { type: 'usage', input_tokens: 10, output_tokens: 5, context_tokens: 54, context_limit: 100, model: 'm' };
const CLOSING = [
    { type: 'user', text: 'You are useless.' },
    { type: 'session_ended', session_id: 's1', warned_msg_id: 1, closed_msg_id: 2, reason: 'abuse', closed_notice: NOTICE },
    { type: 'final', message: 'This conversation is closed.', outcome: 'closed', closed: true },
    { type: 'turn_end' },
];
const REQUEST = { type: 'input_request', request_id: 'r1', kind: 'confirm', title: 'Run the tests?', body: 'Kvist needs permission.', subject: { kind: 'command', command: 'npm test', reason: 'Verify' }, remember_pattern: 'npm test' };

// --- helpers
const rect = (page, selector) => page.locator(selector).evaluate(n => { const r = n.getBoundingClientRect(); return [r.left, r.top, r.width, r.height].map(v => Math.round(v * 1000) / 1000); });
const created = model => model.requests.filter(r => r.path === '/sessions').length;
// The animations element.animate made on a node (not the CSS ones): what they change and for how long.
const webAnimations = (page, selector, subtree = false) => page.evaluate(([selector, subtree]) => {
    const node = document.querySelector(selector);
    return node.getAnimations({ subtree }).filter(a => !(a instanceof CSSAnimation) && !(a instanceof CSSTransition) && (subtree ? a.effect.target !== node : true))
        .map(a => ({ target: a.effect.target.id || a.effect.target.className, state: a.playState, duration: a.effect.getTiming().duration, properties: [...new Set(a.effect.getKeyframes().flatMap(k => Object.keys(k)).filter(p => !['offset', 'easing', 'composite', 'computedOffset'].includes(p)))].sort() }));
}, [selector, subtree]);
// The calls the app made since the page loaded, by node id or class.
const started = (page, id) => page.evaluate(id => window.__animated.filter(a => a.id === id), id);
const startedOn = (page, pattern) => page.evaluate(source => window.__animated.filter(a => new RegExp(source).test(a.className)), pattern.source);
const rows = page => page.evaluate(() => [...document.querySelectorAll('#conversation > *')].map(n => n.className.trim()));
const focusId = page => page.evaluate(() => document.activeElement.id || document.activeElement.className || document.activeElement.tagName);
const selectSession = (page, name) => page.evaluate(name => [...document.querySelectorAll('.nav-session')].find(b => b.dataset.key.endsWith(`\u0000${name}`)).click(), name);
const FEED_READY = () => document.querySelector('#history-status').hidden && document.querySelector('#conversation').children.length > 0;
// Pauses every animation of the page at `time` ms and takes a screenshot (an animation that ended is left alone).
async function shotAt(page, name, time) {
    await page.evaluate(time => { for (const a of document.getAnimations()) if (!(a instanceof CSSAnimation) && !(a instanceof CSSTransition) && a.playState !== 'finished') { a.pause(); a.currentTime = time; } }, time);
    await page.screenshot({ path: `${shots}/${name}.png` });
    await page.evaluate(() => { for (const a of document.getAnimations()) if (a.playState === 'paused') a.play(); });
}
const both = async (page, name, fn) => {
    await fn(page, `${name}-1440x900`);
    await page.setViewportSize({ width: 390, height: 844 });
    await frames(page, 3);
    await fn(page, `${name}-390x844`);
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${name}: horizontal scroll at 390px`);
    await page.setViewportSize({ width: 1440, height: 900 });
    await frames(page, 3);
};
const closeSession = async (model, page, name = 's1') => {
    model.closed.add(name);
    await journal(model, page, name, CLOSING.map(e => (e.session_id ? { ...e, session_id: name } : e)));
    await page.waitForSelector('.closed-banner');
    await frames(page, 2);
};
const gone = ['#prompt', '#send-button', '.composer-hint', '.image-attach-button', '#model-selector', '#composer'];

try {
    // ----- closed session banner
    await check('banner, live: replaces the input at the same place and size, notice and notice row are not shown', async () => {
        const { page, model } = await app({ histories: { s1: records([usage, ...HELLO]) } });
        await page.locator('#prompt').fill('a draft nobody sent');
        const slot = await rect(page, '#composer-slot'), statusBefore = await rect(page, '.status-line'), field = (await rect(page, '#composer'))[3];
        await closeSession(model, page);
        assert.deepEqual(await rect(page, '#composer-slot'), slot, 'the slot is the input area it was, to the pixel');
        assert.deepEqual(await rect(page, '.status-line'), statusBefore, 'the status line has not moved');
        const banner = await rect(page, '.closed-banner');
        assert.deepEqual([banner[0], banner[1], banner[2]], [slot[0], slot[1], slot[2]], 'same left edge, top and width as the input');
        assert(banner[3] >= field, `at least as tall as the input field (${banner[3]} against ${field})`);
        for (const selector of gone) assert.equal(await page.locator(selector).count(), 0, `${selector} is not in the DOM`);
        assert.equal((await page.locator('.closed-banner .closed-text').textContent()).trim(), 'This conversation has ended');
        assert.equal((await page.locator('.closed-banner button').textContent()).trim(), 'New session');
        const text = await page.evaluate(() => document.body.innerText);
        assert(!text.includes('conversation over') && !text.includes('Enter — new session'), 'the notice of the core is not shown');
        assert(!(await rows(page)).some(c => c.includes('notice')), 'no notice row for session_ended');
        assert.match(await page.locator('#context-button').textContent(), /Context 54%/, 'the status line still works');
        assert.equal(await focusId(page), 'closed-new', 'the focus is on the button');
        assert.deepEqual(page.errors, []);
    });

    await check('banner survives a reload, opening a closed session, switching away and back, and a second tab', async () => {
        // both sessions report their context, so the status line takes the same room in each
        const first = await app({ histories: { s1: records([usage, ...HELLO]), s2: records([usage, ...HELLO]) } });
        const { page, model } = first;
        const openSlot = await rect(page, '#composer-slot');
        await closeSession(model, page);
        await page.reload();
        await page.waitForSelector('.closed-banner');
        assert.deepEqual(await rect(page, '#composer-slot'), openSlot, 'after a reload: the same slot');
        for (const selector of gone) assert.equal(await page.locator(selector).count(), 0, `reload: ${selector}`);
        assert.equal(await focusId(page), 'closed-new', 'reload: the button has the focus');
        // to the open session: the input comes back, in the slot of the banner; then back to the closed one
        await selectSession(page, 's2');
        await page.waitForFunction(FEED_READY);
        assert.equal(await page.locator('.closed-banner').count(), 0);
        assert.equal(await page.locator('#prompt').count(), 1);
        assert.deepEqual(await rect(page, '#composer-slot'), openSlot, 'the open session has the slot of the banner');
        await selectSession(page, 's1');
        await page.waitForSelector('.closed-banner');
        assert.deepEqual(await rect(page, '#composer-slot'), openSlot, 'back on the closed session: the same slot');
        for (const selector of gone) assert.equal(await page.locator(selector).count(), 0, `back: ${selector}`);
        // a second tab opened afterwards, and one that was already open when the session closed
        const second = await openTab(first);
        await second.waitForSelector('.closed-banner');
        assert.equal(await second.locator('#prompt').count(), 0, 'second tab, opened after');
        const third = await openTab(first);
        await selectSession(third, 's2');
        await third.waitForFunction(FEED_READY);
        await closeSession(model, third, 's2');
        assert.equal(await third.locator('#prompt').count(), 0, 'second tab, open when it closed');
    });

    await check('a closed session opened from the sidebar shows the banner before its history has arrived', async () => {
        const { page, model } = await app({ histories: { s1: records(HELLO), s2: records(HELLO) }, closed: ['s2'] });
        model.delay = 600;
        await selectSession(page, 's2');
        await page.waitForSelector('.closed-banner', { timeout: 300 });
        assert.equal(await page.locator('#history-status').isVisible(), true, 'still loading');
        model.delay = 0;
    });

    await check('New session from the banner creates the session in the same workspace, selects it and focuses the input', async () => {
        const { page, model } = await app({ histories: { s1: records(HELLO) }, closed: ['s1'] });
        await page.locator('.closed-new').click();
        await page.waitForFunction(() => document.querySelector('#session-title').textContent === 'new-1');
        assert.equal(created(model), 1);
        assert.deepEqual(model.requests.find(r => r.path === '/sessions').body, { server_id: 'local', workspace: '/work/Kvist' });
        assert.equal(await page.locator('.closed-banner').count(), 0);
        assert.equal(await focusId(page), 'prompt', 'the input has the focus');
        assert.equal(await page.locator('.nav-session.active').textContent(), 'new-1');
    });

    await check('a draft that was not sent is not carried into the next session', async () => {
        const { page, model } = await app({ histories: { s1: records(HELLO) } });
        await page.locator('#prompt').fill('half a message');
        await closeSession(model, page);
        await page.locator('.closed-new').click();
        await page.waitForFunction(() => document.querySelector('#session-title').textContent === 'new-1');
        assert.equal(await page.locator('#prompt').inputValue(), '', 'the new session starts empty');
        assert.deepEqual(page.errors, [], 'taking the input out of the page raised nothing');
    });

    await check('Enter starts one new session from the banner: focused button, nothing focused; not with modifiers, composing, a repeat, an overlay or a text field', async () => {
        const fresh = () => app({ histories: { s1: records(HELLO) }, closed: ['s1'] });
        // the button has the focus: its own Enter
        let { page, model } = await fresh();
        assert.equal(await focusId(page), 'closed-new');
        await page.keyboard.press('Enter');
        await page.waitForFunction(() => document.querySelector('#session-title').textContent === 'new-1');
        assert.equal(created(model), 1, 'a focused button: one session');
        // a held Enter on the button: the first press acts, the repeats do not
        ({ page, model } = await fresh());
        await page.keyboard.down('Enter');
        await page.keyboard.down('Enter');
        await page.keyboard.down('Enter');
        await page.keyboard.up('Enter');
        await page.waitForFunction(() => document.querySelector('#session-title').textContent === 'new-1');
        await sleep(150);
        assert.equal(created(model), 1, 'a held key: one session');
        // nothing focused: the fallback
        ({ page, model } = await fresh());
        await page.evaluate(() => document.activeElement.blur());
        assert.equal(await page.evaluate(() => document.activeElement === document.body), true);
        await page.keyboard.press('Enter');
        await page.waitForFunction(() => document.querySelector('#session-title').textContent === 'new-1');
        assert.equal(created(model), 1, 'the body has the focus: one session');
        // what must not start a session, one fresh page each
        const refused = async (name, act) => {
            const { page, model } = await fresh();
            await page.evaluate(() => document.activeElement.blur());
            await act(page);
            await sleep(200);
            assert.equal(created(model), 0, name);
        };
        for (const modifier of ['Control', 'Shift', 'Alt', 'Meta']) await refused(`${modifier}+Enter`, page => page.keyboard.press(`${modifier}+Enter`));
        await refused('composing', page => page.evaluate(() => document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', isComposing: true, bubbles: true }))));
        await refused('a repeated key as the first event', page => page.evaluate(() => document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', repeat: true, bubbles: true }))));
        await refused('the command palette is open', async page => { await page.keyboard.press('Control+k'); await page.locator('#palette-input').fill('zzzz'); await page.keyboard.press('Enter'); });
        await refused('a dialog is open', async page => { await page.locator('#settings-button').click(); await page.locator('#modal .modal-card').waitFor(); await page.evaluate(() => document.activeElement.blur()); await page.keyboard.press('Enter'); });
        await refused('a text field has the focus', async page => { await page.evaluate(() => { const input = document.createElement('input'); document.body.append(input); input.focus(); }); await page.keyboard.press('Enter'); });
        await refused('a question panel is open', async page => { await page.evaluate(() => { document.querySelector('#pending-input').hidden = false; document.activeElement.blur(); }); await page.keyboard.press('Enter'); });
    });

    await check('a thought shorter than a second with no text has no row; the others keep "thought for N"', async () => {
        const events = [
            { type: 'user', text: 'think' },
            { type: 'reasoning_commit', stream_id: 'quick', duration_seconds: 0.04 },
            { type: 'reasoning_start', stream_id: 'wrote' }, { type: 'reasoning_chunk', stream_id: 'wrote', text: 'Looking at the cursor.', append: false }, { type: 'reasoning_commit', stream_id: 'wrote', duration_seconds: 0.4 },
            { type: 'reasoning_commit', stream_id: 'long', duration_seconds: 2.4 },
            { type: 'final', message: 'ok' }, { type: 'turn_end' },
        ];
        const { page } = await app({ histories: { s1: records(events) } });
        const labels = await page.locator('.reasoning-label').allTextContents();
        assert.deepEqual(labels, ['thought for 0.4s', 'thought for 2s']);
        assert(!(await page.evaluate(() => document.body.innerText)).includes('0.0s'));
    });

    await check('banner screenshots', async () => {
        const { page, model } = await app({ histories: { s1: records([usage, ...HELLO]) } });
        await closeSession(model, page);
        await both(page, 'banner', async (page, name) => { await page.mouse.move(700, 300); await page.screenshot({ path: `${shots}/${name}.png` }); });
    });

    // ----- motion: view change
    await check('view change: the feed container fades in once, after the history, with nothing animated inside', async () => {
        const histories = { s1: records(toolJournal(40)), s2: records(HELLO) };
        const { page } = await app({ histories });
        const first = await started(page, 'conversation');
        assert.equal(first.length, 1, 'first load: one animation started on the container');
        assert.deepEqual(first[0].properties, ['opacity'], 'only the opacity (the rise was dropped: frames)');
        assert.equal(first[0].duration, 160);
        assert.equal((await startedOn(page, /message|tool|reasoning|notice/)).length, 0, 'first load: no block animates');
        // a reload, read in the first frame the animation exists
        await page.reload();
        const reload = await (await page.waitForFunction(() => { const c = document.querySelector('#conversation'); const web = x => !(x instanceof CSSAnimation) && !(x instanceof CSSTransition); const a = c.getAnimations().filter(web); return a.length ? { count: a.length, inside: c.getAnimations({ subtree: true }).filter(x => x.effect.target !== c && web(x)).length, drawn: c.children.length, scrolled: Math.round(c.scrollHeight - c.scrollTop - c.clientHeight) } : null; }, null, { polling: 'raf' })).jsonValue();
        assert.deepEqual([reload.count, reload.inside], [1, 0], 'reload: one running animation, none inside');
        assert(reload.drawn > 0 && reload.scrolled <= 1, `the feed is drawn and at its end when the animation starts (${JSON.stringify(reload)})`);
        await sleep(250);
        // a session switch
        await page.evaluate(() => { window.__animated.length = 0; });
        const switched = await page.evaluate(async () => {
            [...document.querySelectorAll('.nav-session')].find(b => b.dataset.key.endsWith('\u0000s2')).click();
            while (!document.querySelector('#history-status').hidden || !document.querySelector('#conversation').children.length) await new Promise(r => requestAnimationFrame(r));
            const c = document.querySelector('#conversation');
            return { count: c.getAnimations().length, inside: c.getAnimations({ subtree: true }).length - c.getAnimations().length };
        });
        assert.deepEqual(switched, { count: 1, inside: 0 }, 'a switch: one animation on the container, none inside');
        assert.equal((await startedOn(page, /message|tool|reasoning|notice/)).length, 0, 'a switch: no block animates');
        // a new session: the container and the empty state
        await sleep(250);
        await page.evaluate(() => { window.__animated.length = 0; });
        await page.locator('#new-session').click();
        await page.waitForFunction(() => document.querySelector('#session-title').textContent === 'new-1');
        const ids = (await page.evaluate(() => window.__animated.map(a => a.id || (/nav-session/.test(a.className) ? 'nav-session' : a.className)))).sort();
        assert.deepEqual(ids, ['conversation', 'empty-state', 'nav-session'], 'a new session: the container and the empty state fade in, and so does its row in the sidebar');
        assert.deepEqual(page.errors, []);
    });

    await check('five quick switches in a row never leave more than one animation on the container', async () => {
        const sessions = Array.from({ length: 6 }, (_, i) => session(`q${i}`, `Quick ${i}`));
        const histories = Object.fromEntries(sessions.map(s => [s.name, records(HELLO)]));
        const { page } = await app({ sessions, histories, delay: 40 });
        const most = await page.evaluate(async () => {
            const c = document.querySelector('#conversation');
            let max = 0, running = true;
            const sample = () => { max = Math.max(max, c.getAnimations().filter(a => !(a instanceof CSSAnimation) && !(a instanceof CSSTransition)).length); if (running) requestAnimationFrame(sample); };
            sample();
            for (const name of ['q1', 'q2', 'q3', 'q4', 'q5']) {
                [...document.querySelectorAll('.nav-session')].find(b => b.dataset.key.endsWith(`\u0000${name}`)).click();
                await new Promise(r => setTimeout(r, 25));
            }
            await new Promise(r => setTimeout(r, 700));
            running = false;
            return max;
        });
        assert.equal(most, 1, `at most one at any frame, and one was seen (saw ${most})`);
    });

    await check('view change screenshots: the switch at 0, 80 and 200 ms', async () => {
        const { page } = await app({ histories: { s1: records(toolJournal(30)), s2: records(HELLO) } });
        await both(page, 'switch', async (page, name) => {
            await sleep(250);
            await page.evaluate(() => [...document.querySelectorAll('.nav-session')].find(b => b.dataset.key.endsWith('\u0000s2')).click());
            await page.waitForFunction(FEED_READY);
            await shotAt(page, `${name}-0ms`, 0);
            await shotAt(page, `${name}-80ms`, 80);
            await sleep(220);
            await page.screenshot({ path: `${shots}/${name}-200ms.png` });
            await page.evaluate(() => [...document.querySelectorAll('.nav-session')].find(b => b.dataset.key.endsWith('\u0000s1')).click());
            await page.waitForFunction(FEED_READY);
        });
    });

    // ----- motion: blocks added live
    await check('live blocks: a user message fades and rises 4px, the agent blocks only fade; nothing at load, hidden tab or six at once', async () => {
        const { page, model } = await app({ histories: { s1: records(HELLO) } });
        const seenAnimations = () => page.evaluate(() => window.__animated.map(a => ({ cls: a.className, from: a.from, properties: a.properties, duration: a.duration })));
        const record = async (events, count) => {
            await page.evaluate(() => { window.__animated.length = 0; });
            const before = await page.locator('#conversation > *').count();
            await journal(model, page, 's1', events);
            await page.waitForFunction(([before, count]) => document.querySelectorAll('#conversation > *').length >= before + count, [before, count]);
            await frames(page, 2);
        };
        await record([{ type: 'user', text: 'Next question.' }], 1);
        let seen = await seenAnimations();
        const user = seen.find(a => /message user/.test(a.cls));
        assert.deepEqual(user.properties, ['opacity', 'transform'], 'user: opacity and transform');
        assert.equal(user.duration, 160);
        assert.equal(seen.filter(a => /message user/.test(a.cls)).length, 1);
        assert.match(user.from, /translateY\(4px\)/);
        await sleep(200);
        await record([{ type: 'step', step: 1, call_id: 'a1', action_type: 'read_file', action_args: { path: 'a.ts' } }, { type: 'action_result', step: 1, call_id: 'a1', action_type: 'read_file', ok: true, output: 'x', summary: 'x' }], 1);
        seen = await seenAnimations();
        assert(seen.some(a => /tool-block/.test(a.cls)), 'a tool row appears');
        assert(seen.every(a => a.properties.join() === 'opacity'), `agent blocks: opacity only (${JSON.stringify(seen)})`);
        await sleep(200);
        await record([{ type: 'message', markdown: 'A message.' }, { type: 'error', message: 'An error.' }], 2);
        // the error also stops the agent, so the Send button comes back and fades in: only feed rows are counted
        seen = (await seenAnimations()).filter(a => !/button/.test(a.cls));
        assert.equal(seen.length, 2, `text and notice (${JSON.stringify(seen)})`);
        assert(seen.every(a => a.properties.join() === 'opacity' && a.duration === 160));
        await sleep(200);
        await page.evaluate(() => { window.__animated.length = 0; });
        await emit(page, { type: 'reasoning_start', stream_id: 'think-1' });
        await frames(page, 2);
        assert.deepEqual((await seenAnimations()).map(a => [/reasoning/.test(a.cls), a.properties.join()]), [[true, 'opacity']], 'a thought starting: opacity only');
        // five blocks in one frame still appear; six do not
        await sleep(200);
        const burst = async count => { await record(Array.from({ length: count }, (_, i) => ({ type: 'message', markdown: `Burst ${count}-${i}` })), count); return (await seenAnimations()).length; };
        assert.equal(await burst(5), 5, 'five blocks in one frame appear');
        await sleep(200);
        assert.equal(await burst(6), 0, 'six blocks in one frame do not');
        // a hidden tab
        await sleep(200);
        await page.evaluate(() => Object.defineProperty(document, 'hidden', { configurable: true, get: () => true }));
        await record([{ type: 'message', markdown: 'While hidden.' }], 1);
        assert.equal((await seenAnimations()).length, 0, 'hidden tab: nothing appears');
        await page.evaluate(() => delete document.hidden);
        assert.deepEqual(page.errors, []);
    });

    await check('live blocks: the message the person sends rises 4px at once, and the journal record does not animate it again', async () => {
        const { page } = await app({ histories: { s1: records(HELLO) }, onSend: async (body, m) => { await sleep(300); await journal(m, m.pages[0], 's1', [{ type: 'user', text: body.text }]); } });
        await page.evaluate(() => { window.__animated.length = 0; });
        await page.locator('#prompt').fill('Send me.');
        await page.keyboard.press('Control+Enter');
        await page.locator('.message.user', { hasText: 'Send me.' }).waitFor({ timeout: 100 });
        await frames(page, 1);
        assert.equal((await startedOn(page, /message user/)).length, 1);
        await page.waitForFunction(() => document.querySelectorAll('.message.user').length === 2 && !document.querySelector('.message-failed'));
        await sleep(500);
        assert.equal((await startedOn(page, /message user/)).length, 1, 'the journal record merged without a second animation');
        assert.equal(await page.locator('.message.user').count(), 2);
    });

    await check('live blocks: screenshots of a message sent, 0, 80 and 200 ms after', async () => {
        const { page } = await app({ histories: { s1: records(HELLO) }, onSend: async (body, m) => { await journal(m, m.pages[0], 's1', [{ type: 'user', text: body.text }]); } });
        await both(page, 'live-add', async (page, name) => {
            await sleep(250);
            await page.locator('#prompt').fill('Please look at the failing test.');
            await page.keyboard.press('Control+Enter');
            await page.locator('.message.user', { hasText: 'failing test' }).last().waitFor();
            await shotAt(page, `${name}-0ms`, 0);
            await shotAt(page, `${name}-80ms`, 80);
            await sleep(220);
            await page.screenshot({ path: `${shots}/${name}-200ms.png` });
            await page.evaluate(() => { const users = document.querySelectorAll('.message.user'); users[users.length - 1].remove(); });
        });
    });

    // ----- motion: overlays
    await check('overlays: open with an animation, stay in the DOM until the close animation has finished, then hide', async () => {
        const { page } = await app({ histories: { s1: records(HELLO) } });
        const overlays = [
            ['palette', async () => { await page.keyboard.press('Control+k'); }, '#palette', '#palette .palette'],
            ['dialog', async () => { await page.locator('#settings-button').click(); }, '#modal', '#modal .modal-card'],
            ['session tools', async () => { await page.getByRole('button', { name: 'Files', exact: true }).click(); }, '#workspace-view', '#workspace-view .workspace-view'],
        ];
        for (const [name, openIt, overlay, card] of overlays) {
            await openIt();
            await page.locator(overlay).waitFor({ state: 'visible' });
            const opening = await webAnimations(page, overlay), rising = await webAnimations(page, card);
            assert.deepEqual([opening.length, opening[0].properties, opening[0].duration], [1, ['opacity'], 160], `${name}: the scrim fades in`);
            assert.deepEqual([rising.length, rising[0].properties, rising[0].duration], [1, ['opacity', 'transform'], 160], `${name}: the card fades in and rises`);
            assert.match(await page.evaluate(card => window.__animated.filter(a => document.querySelector(card).className === a.className).at(-1).from, card), /translateY\(8px\)/);
            await sleep(220);
            // Escape, read in the same task: still shown, fading out; then hidden when the animation has finished
            const closing = await page.evaluate(async overlay => {
                const node = document.querySelector(overlay);
                document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
                const during = { hidden: node.hidden, animations: node.getAnimations().filter(a => a.playState === 'running').map(a => a.effect.getTiming().duration) };
                await Promise.all(node.getAnimations().map(a => a.finished));
                await new Promise(r => requestAnimationFrame(r));
                return { during, hiddenAfter: node.hidden };
            }, overlay);
            assert.deepEqual(closing.during, { hidden: false, animations: [100] }, `${name}: shown while it fades out, in 100 ms`);
            assert.equal(closing.hiddenAfter, true, `${name}: hidden when the animation has finished`);
            assert.equal(await page.evaluate(overlay => document.querySelector(overlay).getAnimations().length, overlay), 0);
        }
        assert.deepEqual(page.errors, []);
    });

    await check('overlays: reopening during the fade-out cancels it and the dialog stays open', async () => {
        const { page } = await app({ histories: { s1: records(HELLO) } });
        await page.locator('#settings-button').click();
        await page.locator('#modal .modal-card').waitFor();
        await sleep(220);
        const result = await page.evaluate(async () => {
            const node = document.querySelector('#modal');
            document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
            const fading = node.getAnimations().length;
            document.querySelector('#settings-button').click();
            await new Promise(r => setTimeout(r, 300));
            return { fading, hidden: node.hidden, card: !!node.querySelector('.modal-card') };
        });
        assert.deepEqual(result, { fading: 1, hidden: false, card: true }, 'the dialog stayed open');
    });

    await check('overlays: the narrow-window sidebar drawer slides in with its scrim and leaves with them', async () => {
        const { page } = await app({ histories: { s1: records(HELLO) }, size: { width: 390, height: 844 } });
        await page.evaluate(() => { window.__animated.length = 0; });
        await page.locator('#toggle-sidebar').click();
        const opening = await page.evaluate(() => window.__animated.map(a => [a.id, a.properties.join(), a.duration]));
        assert.deepEqual(opening, [['sidebar', 'transform', 160], ['sidebar-scrim', 'opacity', 160]]);
        await sleep(220);
        assert.equal(await page.locator('#app.sidebar-open').count(), 1);
        const closing = await page.evaluate(async () => {
            const app = document.querySelector('#app');
            document.querySelector('#sidebar-scrim').click();
            const during = { open: app.classList.contains('sidebar-open'), animations: document.querySelector('#sidebar').getAnimations().map(a => a.effect.getTiming().duration) };
            await Promise.all([...document.querySelector('#sidebar').getAnimations(), ...document.querySelector('#sidebar-scrim').getAnimations()].map(a => a.finished));
            await new Promise(r => requestAnimationFrame(r));
            return { during, openAfter: app.classList.contains('sidebar-open') };
        });
        assert.deepEqual(closing, { during: { open: true, animations: [100] }, openAfter: false });
        await sleep(100);
        await page.locator('#toggle-sidebar').click();
        await shotAt(page, 'drawer-390x844-mid', 80);
        await sleep(220);
        await page.screenshot({ path: `${shots}/drawer-390x844-open.png` });
    });

    // ----- motion: sizes
    await check('two growths from 0fr to 1fr: a call output and a sidebar group, and nothing else changes size', async () => {
        const { page, model } = await app({ histories: { s1: records(toolJournal(24)) } });
        // a call's output: heights frame by frame, rising without a jump, to the size it ends at
        const grown = await page.evaluate(async () => {
            const details = [...document.querySelectorAll('#conversation details.tool:not(.tool-group):not(.tool-static):not([open])')][0];
            details.scrollIntoView({ block: 'center' });
            await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
            const fold = details.querySelector('.fold'), heights = [];
            details.querySelector('summary').click();
            for (let i = 0; i < 24; i++) { heights.push(fold.getBoundingClientRect().height); await new Promise(r => requestAnimationFrame(r)); }
            return { heights, open: details.open };
        });
        const final = grown.heights.at(-1);
        assert(grown.open && final > 20, 'the call opened');
        assert(grown.heights.every((h, i) => i === 0 || h >= grown.heights[i - 1] - 0.5), `rising: ${grown.heights.map(Math.round)}`);
        assert(grown.heights.some(h => h > 0 && h < final - 1), `in between, not a jump: ${grown.heights.map(Math.round)}`);
        assert.equal(grown.heights[0], 0, 'starts closed');
        // a call that opens by itself (a failed one) and a close do not animate
        const instant = await page.evaluate(async () => {
            const failed = document.querySelector('#conversation details.tool.tool-failed[open]');
            const closed = document.querySelector('#conversation details.tool:not(.tool-static)[open]');
            closed.querySelector('summary').click();
            const fold = closed.querySelector('.fold');
            await new Promise(r => requestAnimationFrame(r));
            return { failedOpen: !!failed, failedFoldClass: failed?.querySelector('.fold').className, closedAfterClick: closed.open, animations: fold.getAnimations().length };
        });
        assert.deepEqual([instant.failedOpen, instant.failedFoldClass, instant.closedAfterClick, instant.animations], [true, 'fold', false, 0]);
        // what the person opened stays open when the feed redraws
        await page.evaluate(() => { document.querySelector('#conversation details.tool:not(.tool-group):not(.tool-static):not([open]) > summary').click(); });
        await frames(page, 4);
        const openRows = () => page.evaluate(() => [...document.querySelectorAll('#conversation details.tool:not(.tool-group):not(.tool-static)')].filter(n => n.open).length);
        const keep = await openRows();
        await journal(model, page, 's1', [{ type: 'message', markdown: 'More.' }]);
        await page.waitForFunction(() => [...document.querySelectorAll('.message')].some(n => n.textContent.includes('More.')));
        assert.equal(await openRows(), keep, 'open rows stay open');
        // a sidebar group: collapse, then expand
        await page.locator('.nav-group-toggle').first().click();
        assert.equal(await page.locator('.nav-group .fold').count(), 0, 'closing is at once');
        const group = await page.evaluate(async () => {
            const toggle = document.querySelector('.nav-group-toggle'), heights = [];
            toggle.click();
            const fold = document.querySelector('.nav-group .fold');
            for (let i = 0; i < 24; i++) { heights.push(fold.getBoundingClientRect().height); await new Promise(r => requestAnimationFrame(r)); }
            return { heights, expanded: document.querySelector('.nav-group-toggle').getAttribute('aria-expanded') };
        });
        assert.equal(group.expanded, 'true');
        assert(group.heights.every((h, i) => i === 0 || h >= group.heights[i - 1] - 0.5) && group.heights.some(h => h > 0 && h < group.heights.at(-1) - 1), `group rising: ${group.heights.map(Math.round)}`);
        assert.equal(group.heights.at(-1), 56, 'ends at the height of its two rows');
        // the chevron turns in --dur-fast
        assert.equal(await page.locator('#conversation .chevron').first().evaluate(n => getComputedStyle(n).transitionDuration), '0.1s');
    });

    await check('sizes and properties: nothing animates width, height, margin, top or left; only the two growths use grid-template-rows; no will-change', async () => {
        const { page, model } = await app({ histories: { s1: records([usage, ...toolJournal(30)]) } });
        const sweep = () => page.evaluate(() => {
            const allowed = new Set(['color', 'background-color', 'border-color', 'opacity', 'transform', 'grid-template-rows']);
            const found = [], bad = [], pairs = (list, durations) => list.split(', ').map((value, i) => [value, durations.split(', ')[i % durations.split(', ').length]]);
            for (const node of document.querySelectorAll('*')) {
                for (const pseudo of [null, '::before', '::after']) {
                    const style = getComputedStyle(node, pseudo);
                    for (const [property, duration] of pairs(style.transitionProperty, style.transitionDuration)) {
                        if (parseFloat(duration) === 0) continue;
                        found.push(property);
                        if (!allowed.has(property)) bad.push(`${node.tagName.toLowerCase()}.${node.className} ${pseudo || ''} transitions ${property}`);
                        if (property === 'grid-template-rows' && !node.classList.contains('fold')) bad.push(`${node.className} transitions grid-template-rows outside .fold`);
                    }
                    for (const [name, duration] of pairs(style.animationName, style.animationDuration))
                        if (name !== 'none' && parseFloat(duration) > 0 && !['spin', 'pulse'].includes(name)) bad.push(`${node.className} runs ${name}`);
                    if (style.willChange !== 'auto') bad.push(`${node.className} has will-change ${style.willChange}`);
                }
            }
            return { found: [...new Set(found)].sort(), bad };
        });
        const result = await sweep();
        assert.deepEqual(result.bad, []);
        assert.deepEqual(result.found, ['background-color', 'border-color', 'color', 'grid-template-rows', 'opacity', 'transform']);
        // with the dialog open, and with a closed session
        await page.locator('#settings-button').click();
        await page.locator('#modal .modal-card').waitFor();
        assert.deepEqual((await sweep()).bad, []);
        await page.keyboard.press('Escape');
        await page.locator('#modal').waitFor({ state: 'hidden' });
        await closeSession(model, page);
        assert.deepEqual((await sweep()).bad, []);
        // every animation the app starts touches only opacity and transform
        const touched = await page.evaluate(() => [...new Set(window.__animated.flatMap(a => a.properties))].sort());
        assert.deepEqual(touched, ['opacity', 'transform']);
    });

    // ----- motion: the rest
    await check('banner, question panel, "Jump to latest", tab content, new session row, Send and Stop appear in their own time', async () => {
        const { page, model } = await app({ histories: { s1: records(toolJournal(30)) } });
        const last = id => page.evaluate(id => window.__animated.filter(a => a.id === id).at(-1), id);
        // the question panel: opacity and 8px
        await page.evaluate(() => { window.__animated.length = 0; });
        await journal(model, page, 's1', [REQUEST]);
        await page.locator('#pending-input').waitFor({ state: 'visible' });
        const panel = await last('pending-input');
        assert.deepEqual([panel.properties.join(), panel.duration], ['opacity,transform', 160]);
        assert.match(panel.from, /translateY\(8px\)/);
        await sleep(220);
        // "Jump to latest": opacity and 4px, keeping its centring
        await page.evaluate(() => { document.querySelector('#conversation').scrollTop = 100; });
        await frames(page, 3);
        await page.waitForSelector('#jump-latest', { state: 'visible' });
        const jump = await last('jump-latest');
        assert.deepEqual([jump.properties.join(), jump.duration], ['opacity,transform', 160]);
        assert.match(jump.from, /translateX\(-50%\) translateY\(4px\)/);
        assert.equal((await page.locator('#jump-latest').textContent()).trim(), 'Jump to latest');
        // the tab content fades in --dur-fast; opening the view does not fade the content
        await page.evaluate(() => { window.__animated.length = 0; });
        await page.getByRole('button', { name: 'Files', exact: true }).click();
        await page.locator('#workspace-view').waitFor({ state: 'visible' });
        assert.equal((await page.evaluate(() => window.__animated.filter(a => a.id === 'inspector-content').length)), 0, 'opening the view: the overlay animates, the content does not');
        await sleep(220);
        await page.getByRole('tab', { name: 'Diff' }).click();
        const tab = await last('inspector-content');
        assert.deepEqual([tab.properties.join(), tab.duration], ['opacity', 100]);
        await page.keyboard.press('Escape');
        await page.locator('#workspace-view').waitFor({ state: 'hidden' });
        // Send and Stop: the control that comes fades in --dur-fast
        await page.evaluate(() => { window.__animated.length = 0; });
        await emit(page, { type: 'agent.state_changed', state: 'running' });
        await page.locator('#stop-agent').waitFor({ state: 'visible' });
        const stop = await last('stop-agent');
        assert.deepEqual([stop.properties.join(), stop.duration], ['opacity', 100]);
        await emit(page, { type: 'agent.state_changed', state: 'finished' });
        await page.locator('#send-button').waitFor({ state: 'visible' });
        const send = await last('send-button');
        assert.deepEqual([send.properties.join(), send.duration], ['opacity', 100]);
        // a new session: its row fades in, the others do not
        await page.evaluate(() => { window.__animated.length = 0; });
        await page.locator('#new-session').click();
        await page.waitForFunction(() => document.querySelector('#session-title').textContent === 'new-1');
        const rowsStarted = await page.evaluate(() => window.__animated.filter(a => /nav-session/.test(a.className)));
        assert.deepEqual(rowsStarted.map(a => [a.properties.join(), a.duration]), [['opacity', 160]], 'one row, the new one');
        // the banner of a closed session, in --dur-base
        await selectSession(page, 's1');
        await page.waitForFunction(FEED_READY);
        await page.evaluate(() => { window.__animated.length = 0; });
        await closeSession(model, page);
        const banner = await page.evaluate(() => window.__animated.filter(a => /closed-banner/.test(a.className)));
        assert.deepEqual(banner.map(a => [a.properties.join(), a.duration]), [['opacity', 160]]);
        assert.deepEqual(page.errors, []);
    });

    await check('the send button scales to .96 while pressed', async () => {
        const { page } = await app({ histories: { s1: records(HELLO) } });
        await page.locator('#prompt').fill('text');
        const button = page.locator('#send-button');
        const box = await button.boundingBox();
        await page.mouse.move(box.x + 14, box.y + 14);
        await page.mouse.down();
        await page.evaluate(() => Promise.all(document.getAnimations().filter(a => a instanceof CSSTransition).map(a => a.finished)));
        assert.equal(await button.evaluate(n => getComputedStyle(n).transform), 'matrix(0.96, 0, 0, 0.96, 0, 0)');
        assert.equal(await button.evaluate(n => getComputedStyle(n).transitionDuration.split(', ').includes('0.1s')), true);
        await page.mouse.up();
        await page.mouse.move(700, 300);
    });

    await check('question panel screenshots', async () => {
        const { page, model } = await app({ histories: { s1: records(toolJournal(6)) } });
        await journal(model, page, 's1', [REQUEST]);
        await page.locator('#pending-input').waitFor({ state: 'visible' });
        await both(page, 'question', async (page, name) => { await sleep(250); await page.mouse.move(700, 300); await page.screenshot({ path: `${shots}/${name}.png` }); });
    });

    // ----- reduced motion
    await check('reduced motion: nothing animates and everything shows and hides at once', async () => {
        const { page, model } = await app({ histories: { s1: records([usage, ...toolJournal(12)]), s2: records(HELLO) }, reducedMotion: 'reduce' });
        const none = async what => assert.equal(await page.evaluate(() => window.__animated.length), 0, `${what}: no animation started`);
        await none('first load');
        assert.equal(await page.evaluate(() => document.getAnimations().filter(a => !(a instanceof CSSTransition)).length), 0);
        await selectSession(page, 's2');
        await page.waitForFunction(FEED_READY);
        await journal(model, page, 's2', [{ type: 'user', text: 'Reduced.' }], { announce: true });
        await emit(page, { type: 'user', text: 'Reduced.' }, { durable: true, session: 's2' });
        await page.locator('.message.user', { hasText: 'Reduced.' }).first().waitFor();
        await page.locator('#new-session').click();
        await page.waitForFunction(() => document.querySelector('#session-title').textContent === 'new-1');
        await none('switches and a live block');
        // an overlay hides in the same task as the Escape
        await page.locator('#settings-button').click();
        await page.locator('#modal .modal-card').waitFor();
        assert.equal(await page.evaluate(() => { document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); return document.querySelector('#modal').hidden; }), true, 'a dialog hides at once');
        await page.keyboard.press('Control+k');
        await page.locator('#palette').waitFor({ state: 'visible' });
        assert.equal(await page.evaluate(() => { document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); return document.querySelector('#palette').hidden; }), true, 'the palette hides at once');
        // a call opens at once, without a growth
        await selectSession(page, 's1');
        await page.waitForFunction(FEED_READY);
        const open = await page.evaluate(async () => {
            const details = [...document.querySelectorAll('#conversation details.tool:not(.tool-group):not(.tool-static):not([open])')][0];
            const fold = details.querySelector('.fold');
            details.querySelector('summary').click();
            await new Promise(r => setTimeout(r, 0));
            return { fold: fold.className, transitions: document.getAnimations().filter(a => a instanceof CSSTransition).length };
        });
        assert.deepEqual(open, { fold: 'fold', transitions: 0 });
        // the banner and the drawer
        await closeSession(model, page);
        await page.setViewportSize({ width: 390, height: 844 });
        await page.locator('#toggle-sidebar').click();
        assert.equal(await page.locator('#app.sidebar-open').count(), 1);
        await page.evaluate(() => { document.querySelector('#sidebar-scrim').click(); });
        assert.equal(await page.locator('#app.sidebar-open').count(), 0, 'the drawer is gone at once');
        await none('the banner and the drawer');
        assert.equal(await page.evaluate(() => getComputedStyle(document.querySelector('.closed-new')).transitionDuration), '0s');
    });

    console.log(`${checks.length} motion scenarios passed`);
} finally {
    await browser.close();
}

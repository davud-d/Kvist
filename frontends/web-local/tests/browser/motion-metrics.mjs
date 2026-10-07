// Jank metrics of the feed: for each flow the sum and the maximum of the layout shifts (PerformanceObserver,
// hadRecentInput not filtered), the longest frame and the 95th percentile of the frame durations (differences of
// requestAnimationFrame times), and how far blocks that did not change moved. It prints a table and writes
// metrics.json to KVIST_SHOTS (default /tmp/kvist-shots/5c); it never fails on a threshold. The targets are
// ours, not a standard: a layout shift sum of at most 0.02 per flow and a longest frame of at most 100 ms.
// Every flow runs twice: following the end of the feed, and with the person scrolled up (only there the blocks
// that did not change must not move at all).
// PLAYWRIGHT_MODULE=... CHROMIUM_EXECUTABLE=... KVIST_WEB_URL=http://127.0.0.1:PORT node .../motion-metrics.mjs
import { mkdir, writeFile } from 'node:fs/promises';
import { browser, open, emit, journal, records, session, toolJournal, frames, sleep, median } from './app-mock.mjs';

const out = process.env.KVIST_SHOTS || '/tmp/kvist-shots/5c';
await mkdir(out, { recursive: true });
const TARGETS = { shift: 0.02, frame: 100 };

const HELLO = [{ type: 'user', text: 'Say hello.' }, { type: 'final', message: 'Hello, how can I help?' }, { type: 'turn_end' }];
const usage = { type: 'usage', input_tokens: 10, output_tokens: 5, context_tokens: 54, context_limit: 100, model: 'm' };
const FILL = [...toolJournal(24).slice(0, -2), ...Array.from({ length: 12 }, (_, i) => ({ type: 'message', markdown: `Paragraph ${i}: the cursor is saved before the reconnect.\n\n- one\n- two` })), { type: 'final', message: 'Done.' }, { type: 'turn_end' }];
const ANSWER = '## Result\n\nThe cursor is saved before the reconnect, so nothing is lost:\n\n- Recover missed events without duplicating messages.\n- Keep background sessions running when switching workspaces.\n- Verify reconnect, cancellation and unread state.\n\nThe fix in `src/recovery.ts`:\n\n```ts\nexport function recover(cursor: number) {\n  return history.after(cursor);\n}\n```\n\nAll **24 tests** passed.\n\n'.repeat(8);

// Starts the recorders in the page: layout shifts, frame deltas and the tops of the blocks in view.
// `before` is a selector of the node a flow changes: only the blocks above its block are watched for moving.
const start = (page, track, before = '') => page.evaluate(([track, before]) => {
    const m = window.__metrics = { shifts: [], deltas: [], moved: 0, running: true, last: performance.now(), tracked: [] };
    m.observer = new PerformanceObserver(list => { for (const entry of list.getEntries()) m.shifts.push(entry.value); });
    m.observer.observe({ type: 'layout-shift', buffered: false });
    const feed = document.querySelector('#conversation'), box = feed.getBoundingClientRect();
    const limit = before ? [...document.querySelectorAll(before)].at(-1)?.closest('#conversation > *') : null;
    const above = limit ? [...feed.children].slice(0, [...feed.children].indexOf(limit)) : [...feed.children];
    if (track) m.tracked = above.map(node => ({ node, top: node.getBoundingClientRect().top })).filter(({ top }) => top > box.top && top < box.bottom - 40);
    const tick = now => {
        m.deltas.push(now - m.last);
        m.last = now;
        for (const item of m.tracked) if (item.node.isConnected) m.moved = Math.max(m.moved, Math.abs(item.node.getBoundingClientRect().top - item.top));
        if (m.running) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
}, [track, before]);
const stop = page => page.evaluate(() => {
    const m = window.__metrics;
    m.running = false;
    m.observer.takeRecords().forEach(entry => m.shifts.push(entry.value));
    m.observer.disconnect();
    const sorted = [...m.deltas].sort((a, b) => a - b);
    const round = value => Math.round(value * 1000) / 1000;
    return { shiftSum: round(m.shifts.reduce((a, b) => a + b, 0)), shiftMax: round(Math.max(0, ...m.shifts)), frameMax: round(Math.max(...m.deltas)), frameP95: round(sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * 0.95))]), moved: round(m.moved), frames: m.deltas.length, tracked: m.tracked.length };
});
const scrollUp = async page => { await page.evaluate(() => { document.querySelector('#conversation').scrollTop = 300; }); await frames(page, 4); };
const burst = (page, session, events, model) => journal(model, page, session, events);

const FLOWS = {
    'send a message': async (page, model) => {
        await page.locator('#prompt').fill('Please look at the failing test.');
        await page.keyboard.press('Control+Enter');
        await sleep(900);
    },
    'stream 200 chunks': async page => {
        await emit(page, { type: 'stream_start', stream_id: 'a1' });
        const step = Math.ceil(ANSWER.length / 200);
        for (let i = 0; i < 200; i++) {
            await emit(page, { type: 'stream_chunk', stream_id: 'a1', text: ANSWER.slice(i * step, (i + 1) * step), append: true });
            await frames(page, 1);
        }
        await emit(page, { type: 'stream_commit', stream_id: 'a1', text: ANSWER });
        await sleep(500);
    },
    'tool row and group': async (page, model) => {
        await burst(page, 's1', [{ type: 'step', step: 1, call_id: 'x1', action_type: 'read_file', action_args: { path: 'src/a.ts' } }], model);
        await sleep(300);
        await burst(page, 's1', [{ type: 'action_result', step: 1, call_id: 'x1', action_type: 'read_file', ok: true, output: 'export const a = 1;', summary: '1 line' }], model);
        await sleep(300);
        const calls = [2, 3, 4].flatMap(n => [{ type: 'step', step: n, call_id: `x${n}`, batch_id: 'g1', action_type: 'run_shell', action_args: { command: `npm test -- ${n}` } }, { type: 'action_result', step: n, call_id: `x${n}`, batch_id: 'g1', action_type: 'run_shell', ok: true, exit_code: 0, output: 'PASS\n3 tests', summary: 'ok' }]);
        await burst(page, 's1', calls, model);
        await sleep(700);
    },
    'thinking to done': async page => {
        await emit(page, { type: 'reasoning_start', stream_id: 't1' });
        for (let i = 0; i < 20; i++) { await emit(page, { type: 'reasoning_chunk', stream_id: 't1', text: `Checking the recovery path, step ${i}. `.repeat(i + 1), append: false }); await frames(page, 2); }
        await emit(page, { type: 'reasoning_commit', stream_id: 't1', duration_seconds: 4.2 });
        await sleep(600);
    },
    'question panel': async (page, model) => {
        await burst(page, 's1', [{ type: 'input_request', request_id: 'r1', kind: 'confirm', title: 'Run the tests?', body: 'Kvist needs permission.', subject: { kind: 'command', command: 'npm test', reason: 'Verify' }, remember_pattern: 'npm test' }], model);
        await sleep(700);
    },
    'expand an output': async page => {
        await page.evaluate(() => { const rows = [...document.querySelectorAll('#conversation details.tool:not(.tool-group):not(.tool-static):not([open])')]; rows.at(-1).querySelector('summary').click(); });
        await sleep(700);
    },
    'close the session': async (page, model) => {
        model.closed.add('s1');
        await burst(page, 's1', [{ type: 'user', text: 'You are useless.' }, { type: 'session_ended', session_id: 's1', warned_msg_id: 1, closed_msg_id: 2, reason: 'abuse', closed_notice: 'x' }, { type: 'final', message: 'This conversation is closed.', outcome: 'closed', closed: true }, { type: 'turn_end' }], model);
        await sleep(700);
    },
};

// What a flow needs before it is measured, after the scroll position of its mode is set.
const PREPARE = {
    'expand an output': async page => {
        await page.evaluate(() => [...document.querySelectorAll('#conversation details.tool:not(.tool-group):not(.tool-static):not([open])')].at(-1).scrollIntoView({ block: 'center' }));
        await frames(page, 4);
    },
};

const rowsOut = [];
for (const [flow, run] of Object.entries(FLOWS)) {
    for (const mode of ['following', 'scrolled up']) {
        const { page, context, model } = await open({
            histories: { s1: records([usage, ...FILL]) },
            onSend: async (body, m) => { await sleep(100); await journal(m, m.pages[0], 's1', [{ type: 'user', text: body.text }]); },
        });
        if (mode === 'scrolled up') await scrollUp(page);
        else await page.evaluate(() => { const c = document.querySelector('#conversation'); c.scrollTop = c.scrollHeight; });
        await frames(page, 3);
        await PREPARE[flow]?.(page);
        await start(page, mode === 'scrolled up', flow === 'expand an output' ? '#conversation details.tool:not(.tool-group):not(.tool-static):not([open])' : '');
        await run(page, model);
        const result = await stop(page);
        rowsOut.push({ flow, mode, ...result, moved: mode === 'scrolled up' ? result.moved : null });
        await context.close();
    }
}

// Switching to a session of 300 tool calls: no block existed before, so nothing can have moved.
for (const mode of ['switch']) {
    const { page, context } = await open({ sessions: [session('s1', 'Light'), session('s2', 'Heavy')], histories: { s1: records(HELLO), s2: records(toolJournal(300)) } });
    await sleep(300);
    const times = [];
    let result;
    for (let run = 0; run < 5; run++) {
        await start(page, false);
        await page.evaluate(async () => {
            const target = document.querySelector('#conversation').children.length > 3 ? 's1' : 's2';
            [...document.querySelectorAll('.nav-session')].find(b => b.dataset.key.endsWith(`\u0000${target}`)).click();
            while (!document.querySelector('#history-status').hidden || !document.querySelector('#conversation').children.length) await new Promise(r => requestAnimationFrame(r));
        });
        await sleep(700);
        const current = await stop(page);
        const heavy = await page.evaluate(() => document.querySelectorAll('#conversation .tool').length > 100);
        if (heavy) { times.push(current); result = current; }
    }
    const worst = times.reduce((a, b) => (b.frameMax > a.frameMax ? b : a), times[0]);
    rowsOut.push({ flow: 'switch to 300 tool rows', mode: 'worst of ' + times.length, ...worst, moved: null });
    await context.close();
}

const pad = (value, width) => String(value).padEnd(width);
console.log(`${pad('flow', 26)}${pad('mode', 14)}${pad('shift sum', 11)}${pad('shift max', 11)}${pad('frame max', 11)}${pad('frame p95', 11)}${pad('moved px', 10)}targets (shift <= ${TARGETS.shift}, frame <= ${TARGETS.frame} ms)`);
for (const row of rowsOut) {
    const misses = [row.shiftSum > TARGETS.shift && 'shift', row.frameMax > TARGETS.frame && 'frame'].filter(Boolean);
    row.misses = misses;
    console.log(`${pad(row.flow, 26)}${pad(row.mode, 14)}${pad(row.shiftSum, 11)}${pad(row.shiftMax, 11)}${pad(row.frameMax, 11)}${pad(row.frameP95, 11)}${pad(row.moved ?? '-', 10)}${misses.length ? `OVER: ${misses.join(', ')}` : 'ok'}`);
}
await writeFile(`${out}/metrics.json`, JSON.stringify({ targets: TARGETS, rows: rowsOut }, null, 2));
console.log(`written to ${out}/metrics.json`);
await browser.close();

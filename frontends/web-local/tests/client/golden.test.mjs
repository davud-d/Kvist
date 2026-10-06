import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { initialState, reduce, reducesLive } from '../../kvist_web/client/features/blocks.js';

// Recorded from the real core and the real web server (see fixtures/README in the commit message):
// live = SSE envelopes as they arrived, history = the /api/v1/history response of the same session.
const read = name => JSON.parse(readFileSync(new URL(`./fixtures/${name}`, import.meta.url), 'utf8'));
const LIVE = read('session-basic.live.json');
const HISTORY = read('session-basic.history.json').events;

const fold = (envelopes, state = initialState()) => envelopes.reduce(reduce, state);
const fromHistory = records => records.map(({ seq, ts, event }) => ({ seq, ts, event }));
// A live envelope carries a broker id, never a journal seq (protocol.py LiveEnvelope).
const fromLive = envelopes => envelopes.map(({ ts, durable, event }) => ({ ts, durable, event }));
// What the client reduces from the live stream: transient events plus stream_start and hello (session.js applyLive).
const liveClient = envelopes => fromLive(envelopes).filter(reducesLive);

// Excluded from every comparison:
//  - block ids (b1, b2, ...): they count blocks, and the live path has fewer;
//  - startedAt and endedAt (of a block and of its calls): the envelope timestamp, which differs between the
//    live envelope and the journal record;
//  - user blocks on the live side: the core writes the `user` record only into the journal, so a live
//    stream never contains one (session_events.py begin_turn);
//  - meta: usage is summed per event live but written once per turn in the journal, hello and route are live-only state.
const withoutCallTimes = ({ calls, ...rest }) => (calls ? { ...rest, calls: calls.map(({ startedAt, endedAt, ...call }) => call) } : rest);
const comparable = state => state.blocks.filter(b => b.kind !== 'user').map(({ id, startedAt, ...rest }) => withoutCallTimes(rest));

test('the fixtures are what the core wrote: two turns, a stream, a two-call batch with output, an answer', () => {
    const types = events => events.map(e => e.type);
    assert.deepEqual(types(HISTORY.map(r => r.event)), ['user', 'route', 'stream_start', 'stream_commit', 'usage', 'turn_end',
        'user', 'route', 'step', 'action_result', 'step', 'action_result', 'step', 'final', 'usage', 'turn_end']);
    assert.ok(LIVE.some(e => e.event.type === 'stream_chunk' && e.event.append === true));
    assert.ok(LIVE.some(e => e.event.type === 'action_output'));
    assert.ok(!LIVE.some(e => e.event.type === 'user'));
    assert.ok(LIVE.every(e => !('seq' in e)));
});

test('blocks from the complete live stream equal blocks from the history of the same session', () => {
    const live = fold(fromLive(LIVE)), history = fold(fromHistory(HISTORY));
    assert.deepEqual(comparable(live), comparable(history));
    assert.deepEqual(history.blocks.map(b => b.kind), ['user', 'text', 'user', 'tool', 'text']);
    // the batch of two calls is one block with the shell output from the result
    const tool = history.blocks.find(b => b.kind === 'tool');
    assert.deepEqual(tool.calls.map(c => [c.actionType, c.status]), [['read_file', 'ok'], ['run_shell', 'ok']]);
    assert.equal(tool.calls[1].output, 'fixture-output');
});

test('history plus live events, some already in the history, give the same blocks as the history alone', () => {
    const expected = fold(fromHistory(HISTORY)).blocks;
    for (let k = 0; k <= HISTORY.length; k++) {
        const known = fold(fromHistory(HISTORY.slice(0, k)));
        // the whole live stream arrives after a history snapshot of k records
        assert.deepEqual(fold(liveClient(LIVE), known).blocks.length >= known.blocks.length, true);
        const complete = fold(fromHistory(HISTORY), fold(liveClient(LIVE), known));
        assert.deepEqual(complete.blocks.map(({ id, startedAt, ...rest }) => rest), expected.map(({ id, startedAt, ...rest }) => rest), `snapshot of ${k} records`);
    }
});

test('live events that precede the history, and live events that follow it, give the same blocks', () => {
    const expected = fold(fromHistory(HISTORY)).blocks.map(({ id, ...rest }) => rest);
    const live = liveClient(LIVE);
    for (let k = 0; k <= live.length; k++) {
        const state = fold(live.slice(k), fold(fromHistory(HISTORY), fold(live.slice(0, k))));
        assert.deepEqual(state.blocks.map(({ id, ...rest }) => rest), expected, `live cut at ${k}`);
    }
});

test('a cut at every event boundary with the last three events delivered again does not change the result', () => {
    const records = fromHistory(HISTORY), whole = fold(records);
    for (let k = 0; k <= records.length; k++) {
        const resumed = fold(records.slice(Math.max(0, k - 3)), fold(records.slice(0, k)));
        assert.deepEqual(resumed, whole, `history cut at ${k}`);
    }
    const live = fromLive(LIVE), liveWhole = fold(live);
    for (let k = 0; k <= live.length; k++) {
        // the state is plain data: a copy taken at the cut continues exactly like the original
        const resumed = fold(live.slice(k), structuredClone(fold(live.slice(0, k))));
        assert.deepEqual(resumed, liveWhole, `live cut at ${k}`);
    }
});

test('seq 0 (a journal_notice before the first turn) is applied, not dropped as a duplicate', () => {
    const notice = { type: 'journal_notice', omitted_turns: 3 };
    const records = [{ seq: 0, ts: 1, event: notice }, ...fromHistory(HISTORY)];
    const state = fold(records);
    assert.deepEqual(state.blocks[0], { id: 'b1', kind: 'notice', source: 'journal_notice', level: 'info', text: '', data: { omitted_turns: 3 } });
    assert.equal(state.blocks.length, 1 + fold(fromHistory(HISTORY)).blocks.length);
    // and still deduplicated once seen
    assert.equal(fold([records[0]], state).blocks.length, state.blocks.length);
    assert.deepEqual(fold(records.slice(0, 3), state), state);
});

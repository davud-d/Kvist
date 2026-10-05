import test from 'node:test';
import assert from 'node:assert/strict';
import { initialState, reduce, reducesLive, TOOL_NAMES } from '../../kvist_web/client/features/blocks.js';

// Fixtures follow the to_dict() output of kernel/frontend_protocol.py (class line in each comment).
const env = (event, seq, ts) => ({ ...(seq != null && { seq }), ...(ts != null && { ts }), event });
const fold = (envelopes, state = initialState()) => envelopes.reduce(reduce, state);
const seqd = events => events.map((event, i) => env(event, i + 1, 1000 + i));
const kinds = state => state.blocks.map(b => b.kind);

function deepFreeze(value) {
    if (value && typeof value === 'object' && !Object.isFrozen(value)) {
        Object.freeze(value);
        Object.values(value).forEach(deepFreeze);
    }
    return value;
}

// session_events.py:315 (user record)
const user = text => ({ type: 'user', text });
// frontend_protocol.py:209 StreamStartEvent, :218 StreamChunkEvent, :237 StreamCommitEvent
const streamStart = id => ({ type: 'stream_start', stream_id: id });
const streamChunk = (id, text, append) => ({ type: 'stream_chunk', stream_id: id, text, ...(append !== undefined && { append }) });
const streamCommit = (id, text) => ({ type: 'stream_commit', stream_id: id, text });
// frontend_protocol.py:247 ReasoningStartEvent, :259 ReasoningChunkEvent, :277 ReasoningCommitEvent
const reasoningStart = id => ({ type: 'reasoning_start', stream_id: id });
const reasoningChunk = (id, text, append) => ({ type: 'reasoning_chunk', stream_id: id, text, ...(append !== undefined && { append }) });
const reasoningCommit = (id, seconds) => ({ type: 'reasoning_commit', stream_id: id, duration_seconds: seconds });
// frontend_protocol.py:121 StepEvent
const step = (n, call_id, action_type, action_args = {}, batch_id = '') => ({
    type: 'step', step: n, plan_step: 'inspect', thought: '', user_message: '', action_type, action_args,
    reasoning_seconds: 0, action_description: '', call_id, batch_id,
});
// frontend_protocol.py:338 ActionResultEvent
const result = (n, call_id, action_type, extra = {}) => ({
    type: 'action_result', step: n, action_type, ok: true, summary: 'done', output: '', exit_code: null,
    path: null, first_line: null, call_id, batch_id: '', ...extra,
});
// frontend_protocol.py:378 ActionOutputEvent
const output = (n, call_id, text) => ({ type: 'action_output', step: n, output: text, call_id });
// frontend_protocol.py:489 InputRequestEvent
const input = (request_id, kind = 'confirm', extra = {}) => ({
    type: 'input_request', request_id, kind, title: 'Run?', body: '', options: null, current_value: '', help_text: '', ...extra,
});
// frontend_protocol.py:200 MessageEvent, :174 FinalEvent, :154 PlanEvent
const message = markdown => ({ type: 'message', markdown });
const final = text => ({ type: 'final', message: text, user_messages: [], outcome: 'done' });

// One of each shape, in journal order, used by the cut and freeze tests.
const SCENARIOS = {
    stream: [user('hi'), reasoningStart('a'), reasoningChunk('a', 'think'), reasoningCommit('a', 3.5),
        streamStart('a'), streamCommit('a', 'Hello'), { type: 'turn_end' }],
    tools: [user('go'), step(1, 'c1', 'read_file', { path: 'a.py' }, 'b1'), step(2, 'c2', 'run_shell', { command: ['ls'] }, 'b1'),
        result(1, 'c1', 'read_file', { output: 'x', batch_id: 'b1' }), result(2, 'c2', 'run_shell', { exit_code: 1, ok: false, batch_id: 'b1' }),
        message('mid'), step(3, 'c3', 'git_status'), result(3, 'c3', 'git_status'), final('end')],
    input: [user('go'), input('r1', 'secret', { current_value: 'hunter2' }), input('r1'),
        { type: 'turn_end', stopped_by_user: true }],
    mixed: [user('x'), { type: 'usage', input_tokens: 5, output_tokens: 2, model: 'm', kind: 'step', step: 1, context_tokens: 10, context_limit: 100 },
        { type: 'error', message: 'boom' }, { type: 'sandbox_notice', note: 'weak' }, { type: 'journal_notice', omitted_turns: 2 },
        { type: 'route', role: 'coder', source: 'llm' }, { type: 'plan', markdown: 'p' }, { type: 'session_title', session: 's', title: 'T' }],
};

test('stream deltas append, snapshots replace, commit is final', () => {
    let s = fold([env(streamStart('a')), env(streamChunk('a', 'Hel', true)), env(streamChunk('a', 'lo', true))]);
    assert.equal(s.blocks[0].text, 'Hello');
    assert.equal(s.blocks[0].streaming, true);
    s = fold([env(streamChunk('a', 'Hello world', false))], s);
    assert.equal(s.blocks[0].text, 'Hello world');
    s = fold([env(streamChunk('a', 'Snapshot'))], s); // append absent: older cores
    assert.equal(s.blocks[0].text, 'Snapshot');
    s = fold([env(streamCommit('a', 'Final text'))], s);
    assert.deepEqual([s.blocks[0].text, s.blocks[0].streaming], ['Final text', false]);
});

test('stream_start is idempotent by stream_id; chunks for unknown streams are ignored; commit alone makes a block', () => {
    let s = fold([env(streamStart('a')), env(streamStart('a')), env(streamChunk('zz', 'lost', true))]);
    assert.equal(s.blocks.length, 1);
    s = fold([env(streamCommit('b', 'whole'))], s);
    assert.deepEqual(s.blocks.map(b => b.text), ['', 'whole']);
});

test('durable stream_start moves the live row into journal order', () => {
    let s = fold([env(streamStart('a')), env(streamChunk('a', 'x', true))]);
    s = fold([env(user('later'), 1), env(streamStart('a'), 2)], s);
    assert.deepEqual(s.blocks.map(b => b.kind), ['user', 'text']);
    assert.equal(s.blocks[1].text, 'x');
});

test('reasoning: deltas, snapshot, seconds from duration_seconds, start time from ts', () => {
    let s = fold([env(reasoningStart('a'), null, 500.5), env(reasoningChunk('a', 'a', true)), env(reasoningChunk('a', 'b', true))]);
    const r = () => s.blocks[0];
    assert.deepEqual([r().kind, r().text, r().streaming, r().seconds, r().startedAt], ['reasoning', 'ab', true, null, 500.5]);
    s = fold([env(reasoningChunk('a', 'whole', false))], s);
    assert.equal(r().text, 'whole');
    s = fold([env(reasoningChunk('a', 'snap'))], s); // journal form: no append field
    assert.equal(r().text, 'snap');
    s = fold([env(reasoningCommit('a', 3.5))], s);
    assert.deepEqual([r().streaming, r().seconds, r().startedAt], [false, 3.5, 500.5]);
    // the reasoning block and the answer share a stream_id without colliding
    s = fold([env(streamStart('a'))], s);
    assert.deepEqual(kinds(s), ['reasoning', 'text']);
});

test('reasoning journal triple (start, snapshot, commit) builds one finished block', () => {
    const s = fold(seqd([reasoningStart('a'), reasoningChunk('a', 'why'), reasoningCommit('a', 2)]));
    assert.equal(s.blocks.length, 1);
    assert.deepEqual([s.blocks[0].text, s.blocks[0].seconds, s.blocks[0].streaming], ['why', 2, false]);
});

test('tool: step opens, result completes, output snapshots replace, linked by call_id', () => {
    let s = fold([env(step(1, 'c1', 'run_shell', { command: ['ls'] }))]);
    const call = () => s.blocks[0].calls[0];
    assert.deepEqual([s.blocks.length, call().status, call().args], [1, 'running', { command: ['ls'] }]);
    s = fold([env(output(1, 'c1', 'a')), env(output(1, 'c1', 'a\nb'))], s);
    assert.equal(call().output, 'a\nb');
    s = fold([env(result(1, 'c1', 'run_shell', { output: 'a\nb\nc', exit_code: 0 }))], s);
    assert.deepEqual([s.blocks.length, call().status, call().output, call().exitCode], [1, 'ok', 'a\nb\nc', 0]);
    s = fold([env(output(1, 'c1', 'late'))], s); // a finished call keeps its final output
    assert.equal(call().output, 'a\nb\nc');
});

test('tool: failure by ok=false or non-zero exit; result alone opens a call; step key without call_id', () => {
    let s = fold([env(result(1, 'c1', 'run_shell', { ok: true, exit_code: 2 }))]);
    assert.equal(s.blocks[0].calls[0].status, 'failed');
    s = fold([env(result(2, 'c2', 'read_file', { ok: false }))], s);
    assert.equal(s.blocks[1].calls[0].status, 'failed');
    s = fold([env(step(3, '', 'list_dir')), env(result(3, '', 'list_dir', { output: 'f' }))], s);
    assert.deepEqual([s.blocks.length, s.blocks[2].calls[0].key, s.blocks[2].calls[0].output], [3, 'step:3', 'f']);
    s = fold([env(output(9, 'ghost', 'x'))], s);
    assert.equal(s.blocks.length, 3);
});

test('tool: terminal actions never get a row', () => {
    const s = fold(['final_answer', 'report_to_user', 'refuse'].flatMap((t, i) => [env(step(i, `c${i}`, t)), env(result(i, `c${i}`, t))]));
    assert.equal(s.blocks.length, 0);
});

test('batch_id groups calls into one block; other batches and unbatched calls stay apart', () => {
    const s = fold([env(step(1, 'c1', 'read_file', {}, 'b1')), env(step(2, 'c2', 'read_file', {}, 'b1')),
        env(step(3, 'c3', 'run_shell', {}, 'b2')), env(step(4, 'c4', 'run_shell')),
        env(result(2, 'c2', 'read_file', { batch_id: 'b1', output: 'two' }))]);
    assert.deepEqual(s.blocks.map(b => b.calls.map(c => c.key)), [['c1', 'c2'], ['c3'], ['c4']]);
    assert.equal(s.blocks[0].calls[1].output, 'two');
    assert.equal(s.blocks[0].batchId, 'b1');
});

test('a new user turn restarts call keys', () => {
    const s = fold([env(step(1, '', 'list_dir')), env(user('again')), env(step(1, '', 'list_dir'))]);
    assert.deepEqual(kinds(s), ['tool', 'user', 'tool']);
});

test('input_request opens a block; client.input_answered folds it; duplicates and secrets', () => {
    let s = fold([env(input('r1', 'secret', { current_value: 'hunter2' })), env(input('r1', 'secret'))]);
    assert.equal(s.blocks.length, 1);
    assert.deepEqual([s.blocks[0].kind, s.blocks[0].inputKind, s.blocks[0].currentValue, s.blocks[0].answered], ['input', 'secret', '', null]);
    s = fold([env({ type: 'client.input_answered', request_id: 'r1', summary: 'Allowed' })], s);
    assert.deepEqual(s.blocks[0].answered, { summary: 'Allowed' });
    s = fold([env({ type: 'client.input_answered', request_id: 'nope', summary: 'x' })], s);
    assert.equal(s.blocks.length, 1);
});

test('turn_end and command_result close inputs that never got an answer', () => {
    let s = fold([env(input('r1')), env({ type: 'turn_end', stopped_by_user: true })]);
    assert.deepEqual(s.blocks[0].answered, { summary: '', closed: true });
    assert.deepEqual(s.meta.turn_end, { stopped_by_user: true });
    s = fold([env(input('r2')), env({ type: 'command_result', name: 'effort', ok: true, message: 'ok', effects: {} })], s);
    assert.equal(s.blocks[1].answered.closed, true);
});

test('client.inputs_closed closes unanswered inputs only and touches nothing else', () => {
    let s = fold([env(input('r1')), env(input('r2')), env({ type: 'client.input_answered', request_id: 'r1', summary: 'Allowed' })]);
    const before = s.meta;
    s = fold([env({ type: 'client.inputs_closed' })], s);
    assert.deepEqual(s.blocks.map(b => b.answered), [{ summary: 'Allowed' }, { summary: '', closed: true }]);
    assert.equal(s.meta, before);
    assert.equal(fold([env({ type: 'client.inputs_closed' })], s), s);
});

test('text events: message, final, plan', () => {
    const s = fold([env(message('m')), env(final('f')), env({ type: 'plan', markdown: 'p' })]);
    assert.deepEqual(s.blocks.map(b => [b.kind, b.source, b.text, !!b.plan]), [
        ['text', 'message', 'm', false], ['text', 'final', 'f', false], ['text', 'plan', 'p', true]]);
});

test('notices: error, sandbox_notice, journal_notice, session_ended, connection.*', () => {
    const s = fold([
        env({ type: 'error', message: 'boom' }), env({ type: 'sandbox_notice', note: 'weak' }),
        env({ type: 'journal_notice', omitted_turns: 3 }),
        env({ type: 'session_ended', session_id: 's', warned_msg_id: 1, closed_msg_id: 2, reason: 'r', closed_notice: 'closed' }),
        env({ type: 'connection.state_changed', state: 'failed', error: 'no route' }),
        env({ type: 'connection.lost', message: 'gone' }),
        env({ type: 'connection.restored', recovered_events: 4 }),
        env({ type: 'connection.log', attempt_id: 'a', text: 'noise' }),
    ]);
    assert.deepEqual(s.blocks.map(b => [b.kind, b.source, b.level, b.text]), [
        ['notice', 'error', 'error', 'boom'], ['notice', 'sandbox_notice', 'info', 'weak'],
        ['notice', 'journal_notice', 'info', ''], ['notice', 'session_ended', 'info', 'closed'],
        ['notice', 'connection.state_changed', 'error', 'no route'], ['notice', 'connection.lost', 'error', 'gone'],
        ['notice', 'connection.restored', 'info', '']]);
    assert.deepEqual(s.blocks[2].data, { omitted_turns: 3 });
    assert.equal(s.meta.closed, 'closed');
});

test('meta: usage sums, context replaces, route, title, status, hello and session stay out of the feed', () => {
    const usage = (i, o, c) => ({ type: 'usage', input_tokens: i, output_tokens: o, model: 'm1', kind: 'step', step: 1, context_tokens: c, context_limit: 100 });
    const s = fold([env(usage(5, 2, 10)), env(usage(3, 1, 0)), env({ type: 'route', role: 'coder', source: 'llm' }),
        env({ type: 'session_title', session: 's1', title: 'T' }),
        env({ type: 'status', user_message: 'u', status: 'x', step: 2, progress: 0, plan_step: 'edit', is_error: false }),
        env({ type: 'hello', protocol_version: 1, models: { fast: 'fm' } }),
        env({ type: 'session', session: 's1', has_checkpoint: false, closed: true, closed_notice: 'read only' }),
        env({ type: 'live_start' }), env({ type: 'debug', message: 'd' })]);
    assert.equal(s.blocks.length, 0);
    assert.deepEqual(s.meta.usage, { input: 8, output: 3, calls: 2 });
    assert.deepEqual(s.meta.context, { context_tokens: 10, context_limit: 100 });
    assert.deepEqual([s.meta.model, s.meta.route, s.meta.session_title, s.meta.closed], ['fm', { role: 'coder', source: 'llm' }, { session: 's1', title: 'T' }, 'read only']);
    assert.equal(s.meta.status.step, 2);
    assert.deepEqual(s.meta.unknown, {});
    const effects = fold([env({ type: 'command_result', name: 'm', ok: true, message: '', effects: { models: { fast: 'f2' } } })], s);
    assert.equal(effects.meta.model, 'f2');
    assert.equal(fold([env({ type: 'session', session: 's1', has_checkpoint: false })], s).meta.closed, null);
});

test('order is preserved when steps and messages interleave', () => {
    const s = fold([env(step(1, 'c1', 'read_file')), env(message('a')), env(step(2, 'c2', 'read_file')),
        env(message('b')), env(result(1, 'c1', 'read_file'))]);
    assert.deepEqual(s.blocks.map(b => b.kind === 'tool' ? b.calls[0].key : b.text), ['c1', 'a', 'c2', 'b']);
});

test('seq: seen and older seq are ignored, seq-less events are never deduplicated', () => {
    let s = fold([env(message('one'), 5), env(message('one'), 5), env(message('old'), 4)]);
    assert.equal(s.blocks.length, 1);
    assert.equal(s.lastSeq, 5);
    s = fold([env({ type: 'error', message: 'x' }), env({ type: 'error', message: 'x' })], s);
    assert.equal(s.blocks.length, 3);
    s = fold([env(message('two'), 6)], s);
    assert.equal(s.blocks.length, 4);
});

test('seq 0 is applied, a repeat of it is a duplicate, and the next seq follows', () => {
    // The journal numbers its first record 0 (a journal_notice before the first turn).
    let s = fold([env({ type: 'journal_notice', omitted_turns: 2 }, 0)]);
    assert.equal(s.blocks.length, 1);
    assert.equal(s.lastSeq, 0);
    s = fold([env({ type: 'journal_notice', omitted_turns: 2 }, 0)], s);
    assert.equal(s.blocks.length, 1);
    s = fold([env(message('first'), 1)], s);
    assert.deepEqual([s.blocks.length, s.lastSeq], [2, 1]);
});

test('a chunk after the commit of its stream is ignored, for the answer and for reasoning', () => {
    let s = fold([env(streamStart('a')), env(streamChunk('a', 'Hel', true)), env(streamCommit('a', 'Hello'))]);
    const committed = s;
    s = fold([env(streamChunk('a', 'late', true)), env(streamChunk('a', 'Snapshot', false))], s);
    assert.deepEqual(s, committed);
    assert.deepEqual([s.blocks[0].text, s.blocks[0].streaming], ['Hello', false]);

    s = fold([env(reasoningStart('r')), env(reasoningChunk('r', 'think', true)), env(reasoningCommit('r', 2))]);
    const sealed = s;
    s = fold([env(reasoningChunk('r', ' more', true)), env(reasoningChunk('r', 'rewritten', false))], s);
    assert.deepEqual(s, sealed);
});

test('reducesLive keeps transient live events, stream_start and hello, and drops other durable ones', () => {
    const live = (event, durable) => ({ event, durable });
    assert.equal(reducesLive(live(streamChunk('a', 'x', true), false)), true);
    assert.equal(reducesLive(live({ type: 'action_output', step: 1, output: 'x', call_id: 'c' }, false)), true);
    assert.equal(reducesLive(live(streamStart('a'), true)), true);
    assert.equal(reducesLive(live({ type: 'hello' }, true)), true);
    for (const event of [message('m'), streamCommit('a', 'x'), step(1, 'c', 'read_file'), result(1, 'c', 'read_file'), final('end'), reasoningCommit('a', 1)]) {
        assert.equal(reducesLive(live(event, true)), false, event.type);
    }
    assert.equal(reducesLive(live(message('m'))), true, 'an envelope without durable is not a durable one');
    assert.equal(reducesLive(undefined), true);
});

for (const [name, events] of Object.entries(SCENARIOS)) {
    test(`overlap: any cut point replayed with a 3-event overlap equals one pass (${name})`, () => {
        const all = seqd(events), whole = fold(all);
        for (let k = 0; k <= all.length; k++) {
            const resumed = fold(all.slice(Math.max(0, k - 3)), fold(all.slice(0, k)));
            assert.deepEqual(resumed, whole, `cut at ${k}`);
        }
    });
}

test('stream.reset keeps blocks, refuses stale deltas until a snapshot or commit', () => {
    let s = fold([env(user('q')), env(streamStart('a')), env(streamChunk('a', 'ab', true)), env(reasoningStart('r'))]);
    const before = s.blocks;
    s = fold([env({ type: 'stream.reset', epoch: 'e2', reason: 'history_gap' })], s);
    assert.deepEqual(s.blocks, before);
    s = fold([env(streamChunk('a', 'zz', true)), env(reasoningChunk('r', 'zz', true))], s);
    assert.deepEqual(s.blocks.map(b => b.text), ['q', 'ab', '']);
    s = fold([env(streamStart('a')), env(streamChunk('a', 'snapshot', false)), env(streamChunk('a', '+', true))], s);
    assert.deepEqual([s.blocks.length, s.blocks[1].text], [3, 'snapshot+']);
    s = fold([env(reasoningCommit('r', 1)), env(streamCommit('a', 'done'))], s);
    assert.deepEqual([s.blocks[1].text, s.blocks[1].streaming, s.blocks[2].seconds], ['done', false, 1]);
});

test('unknown event types are counted, never thrown, and leave the feed alone', () => {
    let s = fold([env(message('m'))]);
    s = fold([env({ type: 'novel_event' }), env({ type: 'novel_event' }), env({ type: 'constructor' }), env({ type: '__proto__' })], s);
    assert.deepEqual(s.meta.unknown, { novel_event: 2, constructor: 1, ['__proto__']: 1 });
    assert.equal(s.blocks.length, 1);
    assert.equal(reduce(s, null), s);
    assert.equal(reduce(s, { event: { type: 5 } }), s);
});

test('ids named like Object.prototype members are ordinary keys', () => {
    const s = fold([env(step(1, 'constructor', 'read_file', {}, '__proto__')), env(step(2, 'toString', 'read_file', {}, '__proto__')),
        env(result(1, 'constructor', 'read_file', { output: 'ok', batch_id: '__proto__' }))]);
    assert.deepEqual(s.blocks.map(b => b.calls.map(c => [c.key, c.output])), [[['constructor', 'ok'], ['toString', '']]]);
});

test('a deeply frozen state is never mutated, at any step of any scenario', () => {
    for (const events of Object.values(SCENARIOS)) {
        let s = deepFreeze(initialState());
        for (const e of [...seqd(events), env(streamStart('z')), env(streamChunk('z', 'q', true)), env({ type: 'stream.reset' }), env(output(9, 'ghost', 'x'))]) {
            s = deepFreeze(reduce(s, deepFreeze(structuredClone(e))));
        }
        assert.ok(s.blocks.length > 0);
    }
});

test('TOOL_NAMES merges the row labels and the activity wording', () => {
    assert.deepEqual({ ...TOOL_NAMES.run_shell }, { label: 'Bash', doing: 'Running a command', done: 'Command finished' });
    assert.deepEqual({ ...TOOL_NAMES.read_multiple_files }, { label: 'Read', doing: 'Reading files', done: 'Reading files' });
    assert.equal(TOOL_NAMES.web_search.label, 'Search the web');
    assert.equal(TOOL_NAMES.final_answer.doing, 'Preparing response');
    assert.ok(Object.isFrozen(TOOL_NAMES) && Object.isFrozen(TOOL_NAMES.run_shell));
});

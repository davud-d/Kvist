// Pure feed reducer: no DOM, no module state, no clock, no randomness.
// reduce(state, envelope) -> new state; the input state is never mutated.
//
// envelope = {seq?, ts?, durable?, event}.
// History records (session_events.py:173-182) carry seq, the journal cursor, and
// are deduplicated by it. Live envelopes (protocol.py:98-109) carry a broker id,
// which is not a journal seq, so they enter without seq and are never deduplicated.
// Inputs without seq: status, live_start, live_stop, debug, stream_chunk and
// action_output (transient, session_events.py:62-65), live copies of durable events,
// stream.reset, agent.*, connection.* and the synthetic client.input_answered, client.inputs_closed,
// client.session_closed and client.user_* (below, before HANDLERS).

// One name table for tools. `label` is the short row name, `doing`/`done` the activity wording.
const NAMES = {
    read_file: ['Read', 'Reading a file'], read_multiple_files: ['Read', 'Reading files'],
    write_file: ['Edit', 'Writing a file'], append_file: ['Edit', 'Updating a file'],
    replace_in_file: ['Edit', 'Editing a file'],
    list_dir: ['List', 'Browsing files'], list_tree: ['List', 'Browsing workspace'],
    find_files: ['Search', 'Finding files'], search_code: ['Search', 'Searching code'],
    git_status: ['Bash', 'Checking repository'], git_diff: ['Bash', 'Reviewing changes'],
    run_shell: ['Bash', 'Running a command', 'Command finished'],
    web_search: ['Search the web', 'Searching the web'], fetch_url: ['Fetch', 'Reading a web page'],
    delegate_task: ['Task', 'Delegating work'],
    offer_plan: ['Plan'], propose_plan: ['Plan'], end_session: ['Close session'],
    final_answer: [undefined, 'Preparing response'], report_to_user: [undefined, 'Preparing report'],
    refuse: [undefined, 'Task declined'],
};
export const TOOL_NAMES = Object.freeze(Object.fromEntries(Object.entries(NAMES).map(([type, [label, doing, done]]) => [
    type, Object.freeze({ ...(label && { label }), ...(doing && { doing }), done: done || doing }),
])));

// An answer of its own follows these, so a call row would only repeat it (session.js:59).
const TERMINAL_ACTIONS = new Set(['final_answer', 'report_to_user', 'refuse']);
// Live envelopes the client reduces itself: everything transient, plus stream_start and reasoning_* (the
// journal writes a thought only when it ends, so without the live copy there is no "thinking" row while it
// streams) and hello. Every other durable event reaches the feed through the journal, whose seq deduplicates
// it; reducing its live copy as well would show it twice (session.js applyLive).
const LIVE_DURABLE = new Set(['stream_start', 'reasoning_start', 'reasoning_chunk', 'reasoning_commit', 'hello']);
export const reducesLive = envelope => envelope?.durable !== true || LIVE_DURABLE.has(envelope.event?.type);
// Known events that never reach the feed or meta.
const DROPPED = new Set(['live_start', 'live_stop', 'debug', 'manifest', 'connection.log']);

export function initialState() {
    return {
        blocks: [],
        meta: {
            usage: { input: 0, output: 0, calls: 0 }, context: null, model: '', hello: null, command: null,
            session: null, session_title: null, route: null, status: null, closed: null, turn_end: null,
            agent: null, unknown: {},
        },
        lastSeq: -1, nextId: 1,
        index: { streams: {}, calls: {}, batches: {} },
        stale: {},
    };
}

const at = (s, id) => s.blocks.findLastIndex(b => b.id === id);
const meta = (s, patch) => ({ ...s, meta: { ...s.meta, ...patch } });
// Own-key lookup: ids come off the wire and may be named like Object.prototype members.
const look = (s, group, key) => (Object.hasOwn(s.index[group], key) ? s.index[group][key] : undefined);
const reg = (s, group, key, id) => ({ ...s, index: { ...s.index, [group]: { ...s.index[group], [key]: id } } });
const setStale = (s, key, on) => {
    const { [key]: _, ...rest } = s.stale;
    return { ...s, stale: on ? { ...rest, [key]: true } : rest };
};
function add(s, block) {
    const id = `b${s.nextId}`;
    return [{ ...s, nextId: s.nextId + 1, blocks: [...s.blocks, { id, ...block }] }, id];
}
function patch(s, id, fn) {
    const blocks = s.blocks.slice(), i = at(s, id);
    blocks[i] = fn(blocks[i]);
    return { ...s, blocks };
}
// Moves a block to the end: a live row reserved early takes its place in journal order.
function toEnd(s, id) {
    const i = at(s, id);
    return { ...s, blocks: [...s.blocks.slice(0, i), ...s.blocks.slice(i + 1), s.blocks[i]] };
}
const notice = (s, source, level, text = '', data = {}) => add(s, { kind: 'notice', source, level, text, data })[0];
const text = (s, block) => add(s, { kind: 'text', ...block })[0];
const num = (value, fallback = null) => (typeof value === 'number' ? value : fallback);

// A run that ends or a command that answers leaves no input waiting (the journal has no answer record).
function closeInputs(s) {
    if (!s.blocks.some(b => b.kind === 'input' && b.answered === null)) return s;
    return { ...s, blocks: s.blocks.map(b => (b.kind === 'input' && b.answered === null ? { ...b, answered: { summary: '', closed: true } } : b)) };
}

// A turn that ends leaves its tool blocks final, which is what lets the feed fold a finished group.
function endTurn(s) {
    if (!s.blocks.some(b => b.kind === 'tool' && !b.turnEnded)) return s;
    return { ...s, blocks: s.blocks.map(b => (b.kind === 'tool' && !b.turnEnded ? { ...b, turnEnded: true } : b)) };
}

// stream_* (text) and reasoning_* share chunk logic; only the key prefix differs.
// frontend_protocol.py:209-215
function streamStart(s, e, env) {
    if (typeof e.stream_id !== 'string') return s;
    const key = `s:${e.stream_id}`, id = look(s, 'streams', key);
    if (!id) {
        const [next, nid] = add(s, { kind: 'text', source: 'stream', streamId: e.stream_id, text: '', streaming: true });
        return reg(next, 'streams', key, nid);
    }
    if (env.seq == null) return s;
    // The live row was reserved early; its durable start places it in journal order (session.js:183).
    return toEnd(s, id);
}
// frontend_protocol.py:218-233 and 259-274; append absent = cumulative snapshot (older cores, journal)
const chunk = prefix => (s, e) => {
    const key = `${prefix}:${e.stream_id}`, id = look(s, 'streams', key);
    if (!id) return s;
    // A committed block is final: a chunk that arrives after its commit (live events overtaken by the journal) is late.
    if (!s.blocks[at(s, id)].streaming) return s;
    const delta = e.append === true;
    if (delta && s.stale[key]) return s;
    return setStale(patch(s, id, b => ({ ...b, text: delta ? b.text + (e.text || '') : e.text || '' })), key, false);
};
// frontend_protocol.py:237-244; a commit without a start still produces the answer (session.js:235)
function streamCommit(s, e) {
    const key = `s:${e.stream_id}`, id = look(s, 'streams', key);
    if (!id) {
        const [next, nid] = add(s, { kind: 'text', source: 'stream', streamId: e.stream_id, text: e.text || '', streaming: false });
        return reg(next, 'streams', key, nid);
    }
    return setStale(patch(s, id, b => ({ ...b, text: e.text || '', streaming: false })), key, false);
}
// frontend_protocol.py:247-256; startedAt is the envelope ts, the renderer ticks while streaming
function reasoningStart(s, e, env) {
    const key = `r:${e.stream_id}`;
    if (typeof e.stream_id !== 'string') return s;
    const known = look(s, 'streams', key);
    if (known) return env.seq == null ? s : toEnd(s, known);
    const [next, id] = add(s, { kind: 'reasoning', streamId: e.stream_id, text: '', streaming: true, startedAt: num(env.ts), seconds: null });
    return reg(next, 'streams', key, id);
}
// frontend_protocol.py:277-288
function reasoningCommit(s, e, env) {
    const key = `r:${e.stream_id}`;
    if (!look(s, 'streams', key)) s = reasoningStart(s, e, env);
    const id = look(s, 'streams', key);
    return id ? setStale(patch(s, id, b => ({ ...b, streaming: false, seconds: num(e.duration_seconds, 0) })), key, false) : s;
}
// http_api.py:287-299, remote.py:174-183: live events were lost, so delta text of open streams
// cannot be trusted. Blocks stay; deltas are refused until a snapshot or commit arrives.
function streamReset(s) {
    const stale = {};
    for (const [key, id] of Object.entries(s.index.streams)) if (s.blocks[at(s, id)]?.streaming) stale[key] = true;
    return { ...s, stale };
}

// Tool calls. frontend_protocol.py:121-136 (step), :338-359 (action_result), :378-389 (action_output)
const callKey = e => e.call_id || `step:${e.step}`;
const newCall = e => ({
    key: callKey(e), callId: e.call_id || '', step: e.step, actionType: e.action_type, args: e.action_args || {},
    description: e.action_description || '', userMessage: e.user_message || '', status: 'running', ok: null,
    output: '', summary: '', exitCode: null, path: null, firstLine: null, startedAt: null, endedAt: null,
});
// The envelope ts when it is a time: the journal writes 0.0 for a record whose ts it could not parse.
const stamp = env => (Number.isFinite(env.ts) && env.ts > 0 ? env.ts : null);
// A known call is updated in place; a new one joins the block of its batch_id or opens its own.
function upsertCall(s, e, change) {
    const key = callKey(e), known = look(s, 'calls', key);
    if (known) return patch(s, known, b => ({ ...b, calls: b.calls.map(c => (c.key === key ? change(c) : c)) }));
    const batch = e.batch_id || '', call = change(newCall(e));
    let next, id = batch && look(s, 'batches', batch);
    if (id) next = patch(s, id, b => ({ ...b, calls: [...b.calls, call] }));
    else {
        [next, id] = add(s, { kind: 'tool', batchId: batch, calls: [call] });
        if (batch) next = reg(next, 'batches', batch, id);
    }
    return reg(next, 'calls', key, id);
}
const step = (s, e, env) => TERMINAL_ACTIONS.has(e.action_type) ? s : upsertCall(s, e, c => ({
    ...c, actionType: e.action_type, args: e.action_args || c.args,
    description: e.action_description || c.description, userMessage: e.user_message || c.userMessage,
    startedAt: c.startedAt ?? stamp(env),
}));
const actionResult = (s, e, env) => {
    if (TERMINAL_ACTIONS.has(e.action_type)) return s;
    const code = num(e.exit_code), failed = e.ok === false || (code !== null && code !== 0);
    return upsertCall(s, e, c => ({
        ...c, actionType: e.action_type || c.actionType, status: failed ? 'failed' : 'ok', ok: e.ok ?? null,
        output: e.output || '', summary: e.summary || '', exitCode: code, path: e.path ?? null, firstLine: num(e.first_line),
        endedAt: stamp(env),
    }));
};
// A rolling snapshot, not a delta; a finished call keeps its final output.
const actionOutput = (s, e) => !look(s, 'calls', callKey(e)) ? s
    : upsertCall(s, e, c => (c.status === 'running' ? { ...c, output: e.output || '' } : c));

// frontend_protocol.py:489-516. secret values never enter state (session.js:132).
function inputRequest(s, e) {
    if (s.blocks.some(b => b.kind === 'input' && b.requestId === e.request_id)) return s;
    return add(s, {
        kind: 'input', requestId: e.request_id, inputKind: e.kind, title: e.title || '', body: e.body || '',
        options: e.options ?? null, currentValue: e.kind === 'secret' ? '' : e.current_value || '',
        helpText: e.help_text || '', subject: e.subject ?? null, rememberPattern: e.remember_pattern ?? null, answered: null,
    })[0];
}
// Synthetic, created by the client after the person answers (no seq):
// {type: 'client.input_answered', request_id: string, summary: string}
function inputAnswered(s, e) {
    const i = s.blocks.findLastIndex(b => b.kind === 'input' && b.requestId === e.request_id);
    return i < 0 ? s : patch(s, s.blocks[i].id, b => ({ ...b, answered: { summary: String(e.summary ?? '') } }));
}

// Synthetic, created by the client when the person sends (no seq), so the message is in the feed before the
// journal has it. The block is {kind: 'user', text, local: 'sending' | 'failed', localId}:
//   {type: 'client.user_sent', local_id, text}   adds it as 'sending'
//   {type: 'client.user_failed', local_id}       the send was refused: 'failed' ("Not sent", Retry)
//   {type: 'client.user_retry', local_id}        back to 'sending'
//   {type: 'client.user_unsent', local_id}       the send never started (the provider is not set up): removed
// The journal's user record with the same text turns the first such block into the real one in place, with the
// block id (the feed's row key) unchanged; a 'sending' block goes before a 'failed' one. A block the journal
// never merged is gone after a reload, like all client state.
const userSent = (s, e) => add(s, { kind: 'user', text: e.text || '', local: 'sending', localId: e.local_id })[0];
const localBlock = (s, id) => s.blocks.findIndex(b => b.kind === 'user' && b.local && b.localId === id);
const setLocal = local => (s, e) => {
    const i = localBlock(s, e.local_id);
    return i < 0 ? s : patch(s, s.blocks[i].id, b => ({ ...b, local }));
};
const userUnsent = (s, e) => {
    const i = localBlock(s, e.local_id);
    return i < 0 ? s : { ...s, blocks: s.blocks.filter((_, at) => at !== i) };
};
function journalUser(state, e) {
    const s = { ...state, index: { ...state.index, calls: {}, batches: {} } }, body = e.text || '';
    const waiting = local => s.blocks.findIndex(b => b.kind === 'user' && b.local === local && b.text === body);
    const i = waiting('sending') >= 0 ? waiting('sending') : waiting('failed');
    if (i < 0) return text(s, { kind: 'user', text: body, ...(e.resumed && { resumed: true }) });
    return patch(s, s.blocks[i].id, ({ local, localId, ...block }) => ({ ...block, ...(e.resumed && { resumed: true }) }));
}

const HANDLERS = {
    // session_events.py:315-317 (begin_turn); a new turn restarts step numbering (session.js:209)
    user: journalUser,
    'client.user_sent': userSent,
    'client.user_failed': setLocal('failed'),
    'client.user_retry': setLocal('sending'),
    'client.user_unsent': userUnsent,
    // frontend_protocol.py:200-206
    message: (s, e) => text(s, { source: 'message', text: e.markdown || '' }),
    // frontend_protocol.py:154-162
    plan: (s, e) => text(s, { source: 'plan', plan: true, text: e.markdown || '' }),
    // frontend_protocol.py:174-197
    final: (s, e) => text(s, { source: 'final', text: e.message || '', outcome: e.outcome || 'done', ...(e.closed && { closed: true }) }),
    stream_start: streamStart,
    stream_chunk: chunk('s'),
    stream_commit: streamCommit,
    reasoning_start: reasoningStart,
    reasoning_chunk: chunk('r'),
    reasoning_commit: reasoningCommit,
    step,
    action_result: actionResult,
    action_output: actionOutput,
    input_request: inputRequest,
    'client.input_answered': inputAnswered,
    // Synthetic: the client saw that the session has no pending input (a fresh /navigation); no fields.
    'client.inputs_closed': closeInputs,
    // Synthetic: navigation or a history page says the session is closed, whatever its journal still holds (no fields).
    'client.session_closed': s => (s.meta.closed === null ? meta(s, { closed: '' }) : s),
    // frontend_protocol.py:479-485
    error: (s, e) => notice(s, 'error', 'error', e.message || ''),
    // frontend_protocol.py:165-171
    sandbox_notice: (s, e) => notice(s, 'sandbox_notice', 'info', e.note || ''),
    // session_events.py:250 and 355: counts of dropped turns or events
    journal_notice: (s, e) => notice(s, 'journal_notice', 'info', '', {
        ...(num(e.omitted_turns) !== null && { omitted_turns: e.omitted_turns }),
        ...(num(e.omitted_events) !== null && { omitted_events: e.omitted_events }),
    }),
    // frontend_protocol.py:421-444
    session_ended: (s, e) => meta(notice(s, 'session_ended', 'info', e.closed_notice || '', { reason: e.reason || '' }), { closed: e.closed_notice ?? '' }),
    // controller.py:353-357, :372-376 (fields of the connection result)
    'connection.state_changed': (s, e) => notice(s, 'connection.state_changed', ['failed', 'offline'].includes(e.state) ? 'error' : 'info', e.error || e.message || '', { state: e.state || '' }),
    // remote.py:224-229
    'connection.lost': (s, e) => notice(s, 'connection.lost', 'error', e.message || ''),
    // remote.py:204-209
    'connection.restored': (s, e) => notice(s, 'connection.restored', 'info', '', { recovered_events: num(e.recovered_events, 0) }),
    'stream.reset': streamReset,
    // frontend_protocol.py:19-63; session.js:185-189
    hello: (s, e) => meta(s, { hello: e, model: e.models?.fast || s.meta.model }),
    // event_stdio.py:204-218; session.js:190-197
    command_result: (s, e) => closeInputs(meta(s, {
        command: { name: e.name || '', ok: e.ok === true, message: e.message || '' },
        ...(e.ok && e.effects && { hello: { ...s.meta.hello, ...e.effects }, model: e.effects.models?.fast || s.meta.model }),
    })),
    // frontend_protocol.py:300-334; input/output are summed, context is replaced (session.js:198-207)
    usage: (s, e) => meta(s, {
        usage: { input: s.meta.usage.input + (Number(e.input_tokens) || 0), output: s.meta.usage.output + (Number(e.output_tokens) || 0), calls: s.meta.usage.calls + 1 },
        ...(e.context_tokens > 0 && { context: { context_tokens: e.context_tokens, context_limit: e.context_limit || 0 } }),
        ...(e.model && { model: e.model }),
    }),
    // frontend_protocol.py:98-117
    status: (s, e) => meta(s, { status: { user_message: e.user_message || '', status: e.status || '', step: e.step, plan_step: e.plan_step || '', is_error: e.is_error === true } }),
    // frontend_protocol.py:395-418; a session event without closed clears the closed state
    session: (s, e) => meta(s, { session: { session: e.session, has_checkpoint: e.has_checkpoint === true }, closed: e.closed ? e.closed_notice ?? '' : null }),
    // frontend_protocol.py:464-476: names its own session, which may not be the open one
    session_title: (s, e) => meta(s, { session_title: { session: e.session, title: e.title || '' } }),
    // frontend_protocol.py:447-461
    route: (s, e) => meta(s, { route: { role: e.role, source: e.source } }),
    // event_stdio.py:406, 528; session_events.py:356-359
    turn_end: (s, e) => endTurn(closeInputs(meta(s, { turn_end: { stopped_by_user: e.stopped_by_user === true } }))),
    // processes.py:348 (raw value; the sidebar state is derived elsewhere)
    'agent.state_changed': (s, e) => meta(s, { agent: { state: e.state ?? null, code: null } }),
    // processes.py:322
    'agent.process_exited': (s, e) => endTurn(closeInputs(meta(s, { agent: { state: null, code: num(e.code) } }))),
};

export function reduce(state, envelope) {
    const event = envelope?.event;
    if (!event || typeof event.type !== 'string') return state;
    const seq = Number.isInteger(envelope.seq) ? envelope.seq : null;
    if (seq !== null) {
        if (seq <= state.lastSeq) return state;
        state = { ...state, lastSeq: seq };
    }
    if (Object.hasOwn(HANDLERS, event.type)) return HANDLERS[event.type](state, event, envelope);
    if (DROPPED.has(event.type)) return state;
    const unknown = state.meta.unknown;
    return meta(state, { unknown: { ...unknown, [event.type]: (Object.hasOwn(unknown, event.type) ? unknown[event.type] : 0) + 1 } });
}

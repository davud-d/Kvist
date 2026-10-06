import { api } from "/assets/utils/api.js";
import { state, sessionOf, update, keyOf, busy, stateLabel } from "/assets/state/store.js";
import { $, el, markdown, toast } from "/assets/utils/ui.js";
import { renderInspector, scheduleInspector } from "/assets/features/views.js";
import { configuredModel } from '/assets/features/configuration.js';
import { initialState, reduce, reducesLive } from '/assets/features/blocks.js';
import { createFeed } from '/assets/features/feed.js';
export { renderInspector } from "/assets/features/views.js";
const conversation = document.querySelector("#conversation"), empty = document.querySelector("#empty-state");
const composer = document.querySelector("#composer"), pending = document.querySelector("#pending-input");
const title = document.querySelector("#session-title"), path = document.querySelector("#session-path");
const agentState = document.querySelector("#agent-state"), stop = document.querySelector("#stop-agent");
// The open session's feed: the reducer state is the single source of what the conversation shows.
let feedState = initialState(), feed = createFeed(conversation);
let selectionGeneration = 0;
let activeRecovery = null;
function sameSelection(left, right) {
    return Boolean(left && right && left.serverId === right.serverId && left.workspace === right.workspace && left.session === right.session);
}
export function selectionSnapshot() {
    return state.selected ? { selection: { ...state.selected }, generation: selectionGeneration } : null;
}
export function isCurrentSelection(snapshot) {
    return Boolean(snapshot && snapshot.generation === selectionGeneration && sameSelection(snapshot.selection, state.selected));
}
function currentSelectionMatches(selection, generation) {
    return generation === selectionGeneration && sameSelection(selection, state.selected);
}
let followTail = true, paintFrame = 0;
function paintFeed() {
    feed.render(feedState);
    if (conversation.children.length)
        empty.hidden = true;
}
function queuePaint() {
    if (paintFrame)
        return;
    paintFrame = requestAnimationFrame(() => {
        paintFrame = 0;
        paintFeed();
        if (followTail)
            conversation.scrollTop = conversation.scrollHeight;
        $('#jump-latest').hidden = followTail;
    });
}
export function renderMetadata() {
    $('#model-label').textContent = state.model || configuredModel();
    $('#model-selector').title = state.model ? `Model: ${state.model}` : 'Model and reasoning details';
    const c = state.context;
    $('#context-button').textContent = c?.context_limit ? `Context ${Math.round(c.context_tokens / c.context_limit * 100)}%` : 'Context —';
    $('#context-button').title = c ? `${c.context_tokens.toLocaleString()} / ${c.context_limit?.toLocaleString() || 'unknown'} tokens` : 'Context has not been reported yet';
}
function rememberEvent(event) {
    // A compliant producer never includes a secret's existing value, but keep
    // browser state safe if an older/third-party producer gets that wrong.
    const value = event.type === "input_request" && event.kind === "secret" ? { ...event, current_value: "" } : event;
    // The reducer takes these live and again from the journal; no view reads them.
    if (['stream_chunk', 'status', 'reasoning_chunk', 'stream_start', 'reasoning_start', 'reasoning_commit'].includes(event.type))
        return;
    if (event.type === 'action_output') {
        const previous = state.events.findLastIndex(e => e.type === 'action_output' && e.call_id === event.call_id && e.step === event.step);
        if (previous >= 0) {
            state.events[previous] = value;
            return;
        }
    }
    state.events.push(value);
    if (state.events.length > 3000)
        state.events.splice(0, state.events.length - 3000);
}
function reconcileAgentState(event) {
    const kind = event.type;
    if (kind === "agent.state_changed" && typeof event.state === "string") {
        setAgentState(event.state);
    }
    else if (["status", "step", "live_start", "stream_start", "stream_chunk"].includes(kind)) {
        setAgentState("running");
    }
    else if (kind === "input_request") {
        setAgentState("waiting_for_user");
    }
    else if (kind === 'command_result') {
        pending.hidden = true;
        setAgentState('idle');
    }
    else if (kind === "turn_end") {
        pending.hidden = true;
        setAgentState(event.stopped_by_user ? "stopped" : "finished");
    }
    else if (kind === "error") {
        pending.hidden = true;
        setAgentState("failed");
    }
    else if (kind === "agent.process_exited") {
        pending.hidden = true;
        setAgentState(Number(event.code) === 0 ? "finished" : "failed");
    }
}
// envelope: {seq?, ts?} of the journal record the event came from; a live event has neither seq nor
// a journal copy yet, so the reducer never deduplicates it.
export function applyEvent(event, { initial = false, durableReplay = false, seq, ts } = {}) {
    if (!event || typeof event.type !== "string")
        return;
    const kind = event.type;
    rememberEvent(event);
    feedState = reduce(feedState, { ...(seq != null && { seq }), ...(ts != null && { ts }), event });
    if (kind === 'hello') {
        state.hello = event;
        state.model = event.models?.fast || state.model;
        renderMetadata();
    }
    else if (kind === 'command_result') {
        if (event.ok && event.effects) {
            state.hello = { ...state.hello, ...event.effects };
            state.model = event.effects.models?.fast || state.model;
            renderMetadata();
        }
        if (!initial && !durableReplay) toast(event.message || (event.ok ? 'Configuration updated' : 'Configuration change failed'), !event.ok);
    }
    else if (kind === 'usage') {
        state.usage.input += Number(event.input_tokens) || 0;
        state.usage.output += Number(event.output_tokens) || 0;
        state.usage.calls++;
        if (event.context_tokens > 0)
            state.context = event;
        if (event.model)
            state.model = event.model;
        renderMetadata();
    }
    else if (kind === "input_request" && !initial)
        showPending(event);
    else if (kind === 'turn_end')
        pending.hidden = true;
    if (!initial)
        reconcileAgentState(event);
    // Streamed text and replayed records are painted once per frame; everything else shows at once.
    if (!initial && kind !== 'stream_chunk' && !durableReplay)
        paintFeed();
    if (!initial)
        queuePaint();
    scheduleInspector();
}
export async function openSession(serverId, workspace, session) {
    if (state.selected)
        state.drafts.set(keyOf(state.selected), $('#prompt').value);
    const generation = ++selectionGeneration;
    update(s => { s.selected = { serverId, workspace, session }; s.events = []; s.eventSeq = 0; s.diffReport = ""; s.usage = { input: 0, output: 0, calls: 0 }; s.context = null; s.model = ""; s.hello = null; });
    feedState = initialState();
    feed.destroy();
    feed = createFeed(conversation);
    followTail = true;
    conversation.replaceChildren();
    empty.hidden = true;
    $('#prompt').value = state.drafts.get(keyOf(state.selected)) || '';
    $('#prompt').dispatchEvent(new Event('input'));
    state.hello = state.modelInfo.get(keyOf(state.selected)) || null;
    state.model = state.hello?.models?.fast || '';
    renderMetadata();
    conversation.hidden = false;
    composer.hidden = false;
    pending.hidden = true;
    const item = sessionOf();
    title.textContent = item?.title || session;
    path.textContent = `${workspace} · ${serverId}`;
    setAgentState(item?.execution || "idle");
    const selection = { serverId, workspace, session };
    $('#history-status').hidden = false;
    $('#history-status').textContent = 'Loading conversation…';
    try {
        await replayDurable(selection, generation, { initial: true });
    }
    catch (error) {
        if (!currentSelectionMatches(selection, generation))
            return false;
        $('#history-status').textContent = 'Could not load conversation. ';
        const retry = el('button', 'quiet-button', 'Retry');
        retry.onclick = () => openSession(serverId, workspace, session).catch(error => toast(error.message, true));
        $('#history-status').append(retry);
        throw error;
    }
    if (!currentSelectionMatches(selection, generation))
        return false;
    $('#history-status').hidden = true;
    paintFeed();
    empty.hidden = conversation.children.length > 0;
    const latest = sessionOf();
    setAgentState(latest?.execution || 'idle');
    pending.hidden = true;
    if (latest?.pending_input && latest.execution === 'waiting_for_user')
        showPending(latest.pending_input);
    conversation.scrollTop = conversation.scrollHeight;
    renderInspector();
    scheduleRead();
    return true;
}
export async function recoverSelected() {
    const snapshot = selectionSnapshot();
    if (!snapshot)
        return;
    const { selection, generation } = snapshot;
    const key = `${keyOf(selection)}:${generation}`;
    if (activeRecovery?.key === key) {
        // A live durable envelope can arrive after the in-flight history snapshot
        // was taken. Drain once more before settling so its journal cursor cannot
        // be stranded behind the event that woke us.
        activeRecovery.dirty = true;
        return activeRecovery.promise;
    }
    const recovery = { key, dirty: false, promise: null };
    const promise = (async () => {
        let recovered = 0;
        do {
            recovery.dirty = false;
            recovered += await replayDurable(selection, generation);
        } while (recovery.dirty && currentSelectionMatches(selection, generation));
        if (recovered && currentSelectionMatches(selection, generation))
            scheduleRead();
        return recovered;
    })().finally(() => {
        if (activeRecovery?.promise === promise)
            activeRecovery = null;
    });
    recovery.promise = promise;
    activeRecovery = recovery;
    return promise;
}
async function replayDurable(selection, generation, options = {}) {
    let cursor = state.eventSeq, count = 0;
    while (true) {
        const history = await api.history(selection, cursor, 1000);
        if (!currentSelectionMatches(selection, generation))
            return count;
        for (const record of history.events || []) {
            if (record.seq <= state.eventSeq)
                continue;
            state.eventSeq = record.seq;
            cursor = record.seq;
            count += 1;
            applyEvent(record.event, { ...options, durableReplay: true, seq: record.seq, ts: record.ts });
        }
        if (!history.has_more || !(history.events || []).length)
            return count;
    }
}
export function applyLive(envelope) {
    if (envelope?.event?.type === 'hello')
        state.modelInfo.set(keyOf({ serverId: envelope.server_id, workspace: envelope.workspace, session: envelope.session }), envelope.event);
    if (!state.selected || !envelope || !envelope.event)
        return;
    const selected = state.selected;
    if (envelope.server_id !== selected.serverId || envelope.workspace !== selected.workspace || envelope.session !== selected.session)
        return;
    const event = envelope.event;
    const durable = envelope.durable === true;
    // stream_start reserves one row so transient cumulative chunks can be painted immediately.
    // Its later durable replay moves that row into journal order (reducer, by stream_id).
    if (reducesLive(envelope))
        applyEvent(event, { ts: envelope.ts });
    else
        reconcileAgentState(event);
    if (durable)
        recoverSelected().catch(() => { });
}
// The broker epoch changed or a remote connection dropped live events: open streams cannot be
// continued from deltas until a snapshot or the commit arrives (reducer, stream.reset).
export function resetLive() {
    feedState = reduce(feedState, { event: { type: 'stream.reset' } });
}
// A navigation refresh newer than everything the feed has seen shows no pending request:
// inputs the journal never closed (no answer record) are over.
export function closeStaleInputs(lastEventId) {
    if (lastEventId >= feedState.lastSeq)
        feedState = reduce(feedState, { event: { type: 'client.inputs_closed' } });
}
export function setAgentState(value) {
    const stateValue = typeof value === 'string' && value ? value : 'idle';
    state.execution = stateValue;
    agentState.textContent = stateLabel(stateValue);
    agentState.dataset.state = stateValue;
    stop.hidden = !busy(stateValue);
    $('#send-button').hidden = busy(stateValue);
    $('#prompt').placeholder = busy(stateValue) ? 'Kvist is working. You can draft your next message…' : 'Message Kvist…';
}
function appendSubject(subject) {
    if (!subject || typeof subject !== "object")
        return;
    const details = document.createElement("details");
    details.className = "tool";
    details.open = true;
    const summary = document.createElement("summary");
    const pre = document.createElement("pre");
    if (subject.kind === "command") {
        summary.textContent = "Command awaiting approval";
        pre.textContent = [subject.command, subject.reason].filter(Boolean).join("\n\n") || "No command details were supplied.";
    }
    else if (subject.kind === "overwrite") {
        summary.textContent = subject.path ? `Overwrite ${subject.path}` : "File overwrite awaiting approval";
        pre.textContent = subject.diff || "No diff details were supplied.";
    }
    else {
        summary.textContent = "Request details";
        pre.textContent = JSON.stringify(subject, null, 2);
    }
    details.append(summary, pre);
    pending.append(details);
}
function pendingField(event) {
    if (event.kind === "choice" && Array.isArray(event.options) && event.options.length) {
        const select = document.createElement("select");
        select.setAttribute("aria-label", event.title || "Choose a response");
        for (const option of event.options) {
            const node = document.createElement("option");
            const value = typeof option === "object" && option ? option.value : option;
            const label = typeof option === "object" && option ? option.label || option.value : option;
            node.value = String(value ?? "");
            node.textContent = String(label ?? value ?? "");
            if (String(value ?? "") === String(event.current_value || ""))
                node.selected = true;
            select.append(node);
        }
        return select;
    }
    const input = document.createElement("input");
    input.type = event.kind === "secret" ? "password" : "text";
    input.autocomplete = "off";
    input.value = event.kind === "secret" ? "" : event.current_value || "";
    input.setAttribute("aria-label", event.title || "Response");
    return input;
}
function showPending(event) {
    pending.hidden = false;
    pending.replaceChildren();
    const heading = document.createElement("h3");
    heading.textContent = event.title || "Kvist needs input";
    const body = document.createElement("p");
    body.textContent = event.body || event.help_text || "Review this request before continuing.";
    pending.append(heading, body);
    appendSubject(event.subject);
    if (event.kind === "confirm") {
        const deny = document.createElement("button");
        deny.className = "stop-button";
        deny.textContent = "Deny";
        const allow = document.createElement("button");
        allow.className = "primary-button";
        allow.textContent = "Allow";
        let remember = null;
        if (event.remember_pattern) {
            const label = document.createElement("label");
            remember = document.createElement("input");
            remember.type = "checkbox";
            label.append(remember, document.createTextNode(" Remember this safe approval"));
            pending.append(label);
        }
        deny.onclick = () => respond(event, { approved: false });
        allow.onclick = () => respond(event, { approved: true, ...(remember?.checked ? { remember: true } : {}) });
        pending.append(deny, allow);
        queueMicrotask(() => { if (!document.querySelector(".overlay:not([hidden])"))
            deny.focus(); });
        return;
    }
    const input = pendingField(event);
    const send = document.createElement("button");
    send.className = "primary-button";
    send.textContent = "Continue";
    const submit = () => respond(event, { value: input.value });
    send.onclick = submit;
    input.addEventListener("keydown", key => { if (key.key === "Enter") {
        key.preventDefault();
        submit();
    } });
    pending.append(input, send);
    queueMicrotask(() => { if (!document.querySelector(".overlay:not([hidden])"))
        input.focus(); });
}
async function respond(event, response) {
    const snapshot = selectionSnapshot();
    if (!snapshot)
        return;
    for (const button of pending.querySelectorAll('button'))
        button.disabled = true;
    try {
        await api.respond(snapshot.selection, { request_id: event.request_id, ...response });
    }
    catch (error) {
        toast(error.message, true);
        if (isCurrentSelection(snapshot))
            for (const button of pending.querySelectorAll('button'))
                button.disabled = false;
        return;
    }
    if (!isCurrentSelection(snapshot))
        return;
    feedState = reduce(feedState, { event: { type: 'client.input_answered', request_id: event.request_id, summary: answerSummary(event, response) } });
    pending.hidden = true;
    setAgentState("running");
}
// What the person answered, for the record of the request; a secret's value is never kept.
function answerSummary(event, response) {
    if (typeof response.approved === 'boolean')
        return response.approved ? 'Allowed' : 'Denied';
    return event.kind === 'secret' ? '' : String(response.value ?? '');
}
let readTimer;
export function scheduleRead() {
    clearTimeout(readTimer);
    const snapshot = selectionSnapshot();
    if (!snapshot || state.eventSeq < 1 || !canRead())
        return;
    const cursor = state.eventSeq;
    readTimer = setTimeout(async () => {
        if (!isCurrentSelection(snapshot) || !canRead())
            return;
        try {
            await api.markRead(snapshot.selection, cursor);
            if (isCurrentSelection(snapshot))
                document.dispatchEvent(new CustomEvent('kvist:navigation-refresh'));
        }
        catch (_) { /* Navigation keeps the authoritative unread cursor. */ }
    }, 1400);
}
function canRead() { return !document.hidden && $('#history-status').hidden && !document.querySelector('.overlay:not([hidden])') && conversation.scrollHeight - conversation.scrollTop - conversation.clientHeight < 60; }
conversation.addEventListener('scroll', () => {
    followTail = conversation.scrollHeight - conversation.scrollTop - conversation.clientHeight < 60;
    $('#jump-latest').hidden = followTail;
    scheduleRead();
}, { passive: true });
$('#jump-latest').onclick = () => { followTail = true; conversation.scrollTop = conversation.scrollHeight; scheduleRead(); };
document.addEventListener('visibilitychange', scheduleRead);
document.addEventListener('kvist:overlay', scheduleRead);
export async function loadDiff() {
    const snapshot = selectionSnapshot();
    if (!snapshot)
        return;
    state.diffLoading = true;
    state.diffError = '';
    renderInspector();
    let result;
    try {
        result = await api.changes(snapshot.selection);
    }
    catch (error) {
        if (isCurrentSelection(snapshot)) {
            state.diffError = error.message;
            state.diffLoading = false;
            renderInspector();
        }
        return;
    }
    if (!isCurrentSelection(snapshot))
        return;
    state.diffLoading = false;
    state.diffReport = result.report || "No diff is available yet.";
    if (state.inspectorTab === "diff")
        renderInspector();
}

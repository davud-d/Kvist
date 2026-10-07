import { api } from "/assets/utils/api.js";
import { state, serverOf, sessionOf, update, keyOf, busy, stateLabel, execution } from "/assets/state/store.js";
import { $, el, markdown, toast } from "/assets/utils/ui.js";
import { renderInspector, scheduleInspector } from "/assets/features/views.js";
import { configuredModel } from '/assets/features/configuration.js';
import { initialState, reduce, reducesLive } from '/assets/features/blocks.js';
import { createFeed } from '/assets/features/feed.js';
import { appear } from '/assets/utils/motion.js';
import { renderPending } from '/assets/features/pending.js';
import { answerSummary } from '/assets/features/format.js';
export { renderInspector } from "/assets/features/views.js";
const conversation = document.querySelector("#conversation"), empty = document.querySelector("#empty-state");
const composer = document.querySelector("#composer"), pending = document.querySelector("#pending-input");
// The input's parts are kept as references: a closed session takes them out of the DOM (see showClosed).
const prompt = document.querySelector("#prompt"), sendButton = document.querySelector("#send-button");
const modelLabel = document.querySelector("#model-label"), modelSelector = document.querySelector("#model-selector");
const slot = document.querySelector("#composer-slot"), hint = document.querySelector(".composer-hint");
const title = document.querySelector("#session-title"), path = document.querySelector("#session-path"), server = document.querySelector("#session-server");
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
// A closed session has no input: the banner takes the place of the field and its hint, at the same size
// (the slot keeps the height the input had), and the status line below stays. The unsent draft stays in
// state.drafts under this session's key; it is never carried into the next session.
const banner = el('div', 'closed-banner'), newButton = el('button', 'closed-new', 'New session');
banner.setAttribute('role', 'status');
newButton.type = 'button';
newButton.onclick = () => document.dispatchEvent(new Event('kvist:new-session'));
// A held Enter would repeat the click; one press is one new session.
newButton.addEventListener('keydown', event => { if (event.key === 'Enter' && event.repeat)
    event.preventDefault(); });
banner.append(el('span', 'closed-text', 'This conversation has ended'), newButton);
let closedShown = false;
function focusBanner() {
    if (closedShown && !document.querySelector('.overlay:not([hidden])'))
        newButton.focus();
}
function showClosed(closed, animate = false) {
    if (closed === closedShown)
        return;
    closedShown = closed;
    if (!closed) {
        slot.style.minHeight = '';
        slot.replaceChildren(composer, hint);
        return;
    }
    const height = slot.getBoundingClientRect().height;
    if (height > 0)
        slot.style.minHeight = `${height}px`;
    slot.replaceChildren(banner);
    if (animate)
        appear(banner);
    focusBanner();
}
// feedReady is false from the moment a session starts opening until its first draw: what is painted before
// that is the restored session, not something that arrived live, and nothing in it appears.
let followTail = true, paintFrame = 0, feedReady = false;
function paintFeed(live = true) {
    feed.render(feedState, { live: live && feedReady });
    showClosed(feedState.meta.closed !== null, live && feedReady);
    if (conversation.children.length)
        empty.hidden = true;
}
// The feed container of a session that has just been drawn and scrolled to its end fades in and rises 6px
// (the empty state does the same when there is no feed); a quick switch cancels the animation still running.
let feedAnimation = null;
function revealContent() {
    feedAnimation?.cancel();
    feedAnimation = appear(empty.hidden ? conversation : empty, { rise: 6 });
}
function queuePaint() {
    if (paintFrame)
        return;
    paintFrame = requestAnimationFrame(() => {
        paintFrame = 0;
        paintFeed();
        if (followTail)
            conversation.scrollTop = conversation.scrollHeight;
        showJump(!followTail);
    });
}
// "Latest messages" fades in and rises 4px (it is centred by its own translateX).
function showJump(visible) {
    const button = $('#jump-latest');
    if (button.hidden === !visible)
        return;
    button.hidden = !visible;
    if (visible)
        appear(button, { rise: 4, translate: 'translateX(-50%)' });
}
// Header line under the title: the workspace path, and the server's name beside it unless it is the local one.
export function showPlace(serverId, workspace) {
    const entry = serverId === 'local' ? null : serverOf(serverId);
    path.textContent = workspace;
    server.hidden = !entry;
    server.textContent = entry ? entry.server.name : '';
}
export function renderMetadata() {
    modelLabel.textContent = state.model || configuredModel();
    modelSelector.title = state.model ? `Model: ${state.model}` : 'Model and reasoning details';
    const c = state.context;
    $('#context-button').hidden = !c?.context_limit;
    $('#context-button').textContent = c?.context_limit ? `Context ${Math.round(c.context_tokens / c.context_limit * 100)}%` : '';
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
        hidePending();
        setAgentState('idle');
    }
    else if (kind === "turn_end") {
        hidePending();
        setAgentState(event.stopped_by_user ? "stopped" : "finished");
    }
    else if (kind === "error") {
        hidePending();
        setAgentState("failed");
    }
    else if (kind === "agent.process_exited") {
        hidePending();
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
        hidePending();
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
        state.drafts.set(keyOf(state.selected), prompt.value);
    const generation = ++selectionGeneration;
    feedReady = false;
    feedAnimation?.cancel();
    update(s => { s.selected = { serverId, workspace, session }; s.events = []; s.eventSeq = 0; s.diffReport = ""; s.usage = { input: 0, output: 0, calls: 0 }; s.context = null; s.model = ""; s.hello = null; });
    feedState = initialState();
    feed.destroy();
    feed = createFeed(conversation);
    followTail = true;
    conversation.replaceChildren();
    empty.hidden = true;
    prompt.value = state.drafts.get(keyOf(state.selected)) || '';
    prompt.dispatchEvent(new Event('input'));
    state.hello = state.modelInfo.get(keyOf(state.selected)) || null;
    state.model = state.hello?.models?.fast || '';
    renderMetadata();
    conversation.hidden = false;
    composer.hidden = false;
    hidePending();
    const item = sessionOf();
    // Navigation already knows a closed session; the history page confirms it, so the banner is there before the feed is.
    if (item?.closed === true)
        feedState = reduce(feedState, { event: { type: 'client.session_closed' } });
    showClosed(feedState.meta.closed !== null);
    title.textContent = item?.title || session;
    showPlace(serverId, workspace);
    setAgentState(item?.execution);
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
    paintFeed(false);
    empty.hidden = conversation.children.length > 0;
    const latest = sessionOf();
    setAgentState(latest?.execution || 'idle');
    hidePending();
    if (latest?.pending_input && latest.execution === 'waiting_for_user')
        showPending(latest.pending_input);
    conversation.scrollTop = conversation.scrollHeight;
    feedReady = true;
    // The first session opens while #app is hidden; revealFeed() shows it once the app is.
    if (!$('#app').hidden)
        revealContent();
    renderInspector();
    scheduleRead();
    return true;
}
// The first session opens while the app is still hidden, where nothing has a size: the feed is placed at its
// end and the banner takes the focus once the app is shown.
export function revealFeed() {
    conversation.scrollTop = conversation.scrollHeight;
    focusBanner();
    revealContent();
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
        // The history page says so even when the journal lost the session_ended record to its size cap.
        if (history.closed === true && feedState.meta.closed === null) {
            feedState = reduce(feedState, { event: { type: 'client.session_closed' } });
            queuePaint();
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
    if (lastEventId < feedState.lastSeq)
        return;
    feedState = reduce(feedState, { event: { type: 'client.inputs_closed' } });
    queuePaint();
}
// The status line names only the state that needs the person: the sidebar indicators carry the rest.
export function setAgentState(value) {
    const stateValue = execution(value), waiting = stateValue === 'waiting_for_user';
    state.execution = stateValue;
    agentState.hidden = !waiting;
    agentState.textContent = waiting ? stateLabel(stateValue) : '';
    agentState.dataset.state = stateValue;
    const wasBusy = !stop.hidden;
    stop.hidden = !busy(stateValue);
    sendButton.hidden = busy(stateValue);
    // The control that takes the place fades in; the one that leaves is gone at once.
    if (feedReady && busy(stateValue) !== wasBusy)
        appear(busy(stateValue) ? stop : sendButton, { duration: '--dur-fast' });
    prompt.placeholder = busy(stateValue) ? 'Draft your next message…' : 'Message Kvist…';
}
// Removing the controls with the panel also removes what the person typed into them, a secret included.
function hidePending() {
    pending.hidden = true;
    pending.replaceChildren();
}
function showPending(event) {
    const first = renderPending(pending, event, response => respond(event, response));
    const shown = !pending.hidden;
    pending.hidden = false;
    if (feedReady && !shown)
        appear(pending, { rise: 8 });
    queueMicrotask(() => {
        // A person who is typing a message keeps the focus; the panel waits for Tab.
        if (document.querySelector('.overlay:not([hidden])') || (document.activeElement === prompt && prompt.value))
            return;
        first.focus();
    });
}
async function respond(event, response) {
    const snapshot = selectionSnapshot();
    if (!snapshot)
        return;
    const controls = () => pending.querySelectorAll('button, input');
    for (const control of controls())
        control.disabled = true;
    try {
        await api.respond(snapshot.selection, { request_id: event.request_id, ...response });
    }
    catch (error) {
        toast(error.message, true);
        if (isCurrentSelection(snapshot))
            for (const control of controls())
                control.disabled = false;
        return;
    }
    if (!isCurrentSelection(snapshot))
        return;
    feedState = reduce(feedState, { event: { type: 'client.input_answered', request_id: event.request_id, summary: answerSummary(event, response) } });
    queuePaint();
    hidePending();
    setAgentState("running");
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
    showJump(!followTail);
    scheduleRead();
}, { passive: true });
$('#jump-latest').onclick = () => { followTail = true; conversation.scrollTop = conversation.scrollHeight; showJump(false); scheduleRead(); };
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

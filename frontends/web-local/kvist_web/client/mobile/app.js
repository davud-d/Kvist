import { api } from '/assets/utils/api.js';
import { initialState, reduce, reducesLive } from '/assets/features/blocks.js';
import { answerSummary } from '/assets/features/format.js';
import { $, el } from '/assets/utils/ui.js';
import { isWinter } from './season.js';
import { lang, t } from './i18n.js';
import { createMascot } from './mascot.js';
import { startSnow } from './snow.js';
import { createFeed } from './feed.js';
import { renderRequest } from './request.js';
import { createControls } from './controls.js';
import { counts, renderHome, stateWord } from './home.js';

// The phone's Kvist: a pixel interface over the same HTTP/SSE contract as the browser one. It shows and
// steers the agent that runs on the computer; nothing runs here. Blocks come from the shared pure reducer,
// everything on screen is drawn by this folder.

const NAVIGATION_EVENTS = new Set(['turn_end', 'error', 'input_request', 'session_title', 'agent.state_changed',
    'agent.process_exited', 'connection.state_changed', 'connection.lost', 'connection.restored']);
const REFRESH_MS = 500;
const PROBE_MS = 1500;
const BUSY = new Set(['running', 'starting', 'waiting_for_user']);

const winter = isWinter(new Date(), location.search);
document.documentElement.dataset.season = winter ? 'winter' : 'default';
document.documentElement.lang = lang;
if (winter) document.querySelector('meta[name=theme-color]').content = '#0e1a33';
// Inside the Android app the person can go back to the list of computers.
$('#switch').hidden = !navigator.userAgent.includes('KvistApp');
$('#switch').textContent = t('switchComputer');

const app = {
    navigation: { servers: [], inbox: [] },
    cursor: 0, epoch: '', source: null, transport: 'connecting',
    selection: null, generation: 0, feedState: initialState(), seq: 0, execution: 'idle',
    sending: false, recovering: false, refreshTimer: 0, paintQueued: false, probeTimer: 0,
};

const screens = { home: $('#home'), chat: $('#chat') };
const homeMascot = createMascot($('#home-mascot'), { winter });
const chatMascot = createMascot($('#chat-mascot'), { winter });
const feedBox = $('#feed'), prompt = $('#prompt'), sheet = $('#request');
const feed = createFeed(feedBox, { onRetry: block => retry(block) });
if (winter) startSnow($('#snow'));

// The core's own words about the open session: models, reasoning and role, kept current by command_result.
const controls = createControls({
    selection: () => app.selection,
    hello: () => ({ ...app.feedState.meta.hello, ...(app.feedState.meta.model && { model: app.feedState.meta.model }) }),
    busy: () => BUSY.has(app.execution),
    createSession: () => (app.selection ? createSession({ serverId: app.selection.serverId, workspace: app.selection.workspace }) : Promise.resolve()),
    stop: () => (app.selection ? api.stop(app.selection) : Promise.resolve()),
    toast: message => toast(message),
});

let localCounter = 0;
const nextLocalId = () => `m${Date.now()}-${localCounter++}`;

function toast(message) {
    const node = el('div', 'toast', message);
    $('#toasts').append(node);
    setTimeout(() => node.remove(), 4000);
}

function guard(promise) {
    promise.catch(error => toast(error.message));
}

// ---- state of the open session -------------------------------------------------------------------

function apply(event, envelope = {}) {
    app.feedState = reduce(app.feedState, { ...envelope, event });
    schedulePaint();
}

function setExecution(value) {
    app.execution = typeof value === 'string' && value ? value : 'idle';
    schedulePaint();
}

// What a live event says about the run, whether or not the reducer takes it.
function reconcile(event) {
    const kind = event.type;
    if (kind === 'agent.state_changed' && typeof event.state === 'string') setExecution(event.state);
    else if (['status', 'step', 'live_start', 'stream_start', 'stream_chunk'].includes(kind)) setExecution('running');
    else if (kind === 'input_request') setExecution('waiting_for_user');
    else if (kind === 'command_result') setExecution('idle');
    else if (kind === 'turn_end') setExecution(event.stopped_by_user ? 'stopped' : 'finished');
    else if (kind === 'error') setExecution('failed');
    else if (kind === 'agent.process_exited') setExecution(Number(event.code) === 0 ? 'finished' : 'failed');
}

function schedulePaint() {
    if (app.paintQueued) return;
    app.paintQueued = true;
    requestAnimationFrame(() => { app.paintQueued = false; paint(); });
}

function pendingRequest() {
    return app.feedState.blocks.findLast(block => block.kind === 'input' && block.answered === null) || null;
}

function nearBottom() {
    return feedBox.scrollHeight - feedBox.scrollTop - feedBox.clientHeight < 80;
}

function paint() {
    if (!app.selection) return;
    const follow = nearBottom();
    feed.render(app.feedState.blocks);
    paintHeader();
    paintSheet();
    paintComposer();
    if (follow) feedBox.scrollTop = feedBox.scrollHeight;
}

function paintHeader() {
    const item = sessionItem(app.selection);
    $('#chat-title').textContent = app.feedState.meta.session_title || item?.title || t('untitled');
    $('#chat-state').textContent = stateWord(app.execution);
    $('#chat-state').dataset.state = app.execution;
    const busy = BUSY.has(app.execution);
    $('#pause').hidden = !busy;
    chatMascot.setMood(app.execution === 'running' || app.execution === 'starting' ? 'work' : app.execution === 'waiting_for_user' ? 'wait' : 'idle');
}

let shownRequest = null;
function paintSheet() {
    const block = pendingRequest();
    sheet.hidden = !block;
    if (!block) { shownRequest = null; return; }
    // Redrawing would wipe what the person typed into the sheet, so a request is drawn once.
    if (shownRequest === block.requestId) return;
    shownRequest = block.requestId;
    renderRequest(sheet, block, response => guard(respond(block, response)));
}

function paintComposer() {
    const closed = app.feedState.meta.closed !== null;
    $('#composer').hidden = closed;
    $('#closed').hidden = !closed;
    const busy = BUSY.has(app.execution);
    const send = $('#send');
    send.dataset.mode = busy ? 'stop' : 'send';
    send.textContent = busy ? '■' : '▶';
    send.ariaLabel = busy ? t('stop') : t('send');
    send.disabled = !busy && !prompt.value.trim();
    prompt.placeholder = t('message');
}

// ---- data -----------------------------------------------------------------------------------------

function sessionItem(selection) {
    if (!selection) return null;
    const server = app.navigation.servers.find(entry => entry.server.id === selection.serverId);
    const workspace = server?.workspaces.find(entry => entry.path === selection.workspace);
    return workspace?.sessions?.session_items.find(item => item.name === selection.session) || null;
}

const sameSelection = (a, b) => Boolean(a && b) && a.serverId === b.serverId && a.workspace === b.workspace && a.session === b.session;

async function replay(selection, generation) {
    for (;;) {
        const page = await api.history(selection, app.seq);
        if (generation !== app.generation) return;
        for (const record of page.events || []) {
            if (record.seq <= app.seq) continue;
            app.seq = record.seq;
            apply(record.event, { seq: record.seq, ts: record.ts });
        }
        if (page.closed === true && app.feedState.meta.closed === null) apply({ type: 'client.session_closed' });
        if (!page.has_more || !(page.events || []).length) return;
    }
}

// Reads what the journal has beyond the cursor; one read at a time, because two would apply a record twice.
async function recover() {
    if (!app.selection || app.recovering) return;
    app.recovering = true;
    const { selection, generation } = app;
    try {
        await replay(selection, generation);
    } finally {
        app.recovering = false;
    }
    if (generation === app.generation) markRead();
}

function markRead() {
    if (!app.selection || app.seq <= 0) return;
    api.markRead(app.selection, app.seq).catch(error => console.warn('markRead failed', error));
}

async function openSession(selection) {
    const generation = ++app.generation;
    app.selection = selection;
    app.feedState = initialState();
    app.seq = 0;
    shownRequest = null;
    feedBox.replaceChildren();
    sheet.hidden = true;
    sheet.replaceChildren();
    setExecution(sessionItem(selection)?.execution);
    show('chat');
    paint();
    $('#status').hidden = false;
    $('#status').textContent = t('loading');
    try {
        await replay(selection, generation);
    } catch (error) {
        if (generation !== app.generation) return;
        $('#status').textContent = t('loadFailed');
        throw error;
    }
    if (generation !== app.generation) return;
    $('#status').hidden = true;
    setExecution(sessionItem(selection)?.execution || 'idle');
    paint();
    feedBox.scrollTop = feedBox.scrollHeight;
    markRead();
}

function onLive(envelope) {
    const event = envelope.event;
    if (!app.selection || !sameSelection(app.selection, { serverId: envelope.server_id, workspace: envelope.workspace, session: envelope.session })) return;
    reconcile(event);
    if (event.type === 'command_result') toast(event.message || t('commandDone'));
    if (reducesLive(envelope)) apply(event, { ts: envelope.ts });
    if (envelope.durable === true) guard(recover());
}

async function refresh() {
    app.navigation = await api.navigation();
    if (app.selection) {
        const item = sessionItem(app.selection);
        if (item) {
            setExecution(item.execution);
            // A refresh newer than everything the feed has seen shows no pending request: it is over.
            if (!item.pending_input && item.last_event_id >= app.feedState.lastSeq) apply({ type: 'client.inputs_closed' });
        }
    }
    paintHome();
    schedulePaint();
}

function scheduleRefresh() {
    clearTimeout(app.refreshTimer);
    app.refreshTimer = setTimeout(() => guard(refresh()), REFRESH_MS);
}

// ---- screens ----------------------------------------------------------------------------------------

function show(name) {
    for (const [key, node] of Object.entries(screens)) node.hidden = key !== name;
    scrollTo(0, 0);
}

function paintHome() {
    renderHome($('#home-list'), app.navigation, { open: go, create: selection => createSession(selection).catch(error => toast(error.message)) });
    const { waiting, working } = counts(app.navigation);
    const line = waiting ? (winter ? t('winterWaiting', waiting) : t('waiting', waiting))
        : working ? t('working', working) : winter ? t('winterGreeting') : t('quiet');
    $('#bubble').textContent = line;
    homeMascot.setMood(waiting ? 'wait' : working ? 'work' : 'idle');
    paintLink();
}

function paintLink() {
    const label = { connected: t('online'), connecting: t('connecting'), reconnecting: t('reconnecting') }[app.transport] || t('offline');
    $('#link').dataset.state = app.transport;
    $('#link').title = $('#link').ariaLabel = label;
}

// A session is addressed by the hash, so the phone's Back button goes from a conversation to the list.
const hashOf = selection => `#/${[selection.serverId, selection.workspace, selection.session].map(encodeURIComponent).join('/')}`;

function go(selection) {
    location.hash = hashOf(selection);
}

function route() {
    const parts = location.hash.replace(/^#\//, '').split('/').filter(Boolean).map(decodeURIComponent);
    if (parts.length !== 3) {
        app.generation++;
        app.selection = null;
        show('home');
        paintHome();
        return;
    }
    const [serverId, workspace, session] = parts;
    guard(openSession({ serverId, workspace, session }));
}

let creating = null;

// One at a time: a second tap while the first is on its way would ask for the same session. The new session
// opens as soon as the computer has made it; the lists catch up in the background.
function createSession(target) {
    if (creating) return creating;
    creating = (async () => {
        try {
            const value = await api.createSession(target);
            go({ ...target, session: value.active_session });
            guard(refresh());
        } finally {
            creating = null;
        }
    })();
    return creating;
}

// ---- actions ------------------------------------------------------------------------------------------

async function send(text) {
    if (!app.selection || app.sending) return;
    app.sending = true;
    const localId = nextLocalId();
    apply({ type: 'client.user_sent', local_id: localId, text });
    prompt.value = '';
    autosize();
    try {
        await api.send(app.selection, text);
        setExecution('running');
        scheduleRefresh();
    } catch (error) {
        apply({ type: 'client.user_failed', local_id: localId });
        toast(`${t('sendFailed')} ${error.message}`);
    } finally {
        app.sending = false;
    }
}

async function retry(block) {
    if (!app.selection || BUSY.has(app.execution)) return;
    apply({ type: 'client.user_retry', local_id: block.localId });
    try {
        await api.send(app.selection, block.text);
        setExecution('running');
        scheduleRefresh();
    } catch (error) {
        apply({ type: 'client.user_failed', local_id: block.localId });
        toast(`${t('sendFailed')} ${error.message}`);
    }
}

async function respond(block, response) {
    const selection = app.selection;
    try {
        await api.respond(selection, { request_id: block.requestId, ...response });
    } catch (error) {
        // The sheet is drawn once, so it is drawn again for the person to try once more.
        shownRequest = null;
        schedulePaint();
        throw error;
    }
    if (!sameSelection(selection, app.selection)) return;
    const asked = { kind: block.inputKind, subject: block.subject, title: block.title, options: block.options };
    apply({ type: 'client.input_answered', request_id: block.requestId, summary: answerSummary(asked, response) });
    scheduleRefresh();
}

function autosize() {
    prompt.style.height = 'auto';
    prompt.style.height = `${Math.min(prompt.scrollHeight, 132)}px`;
}

$('#composer').addEventListener('submit', event => {
    event.preventDefault();
    if (BUSY.has(app.execution)) {
        if (app.selection) guard(api.stop(app.selection));
        return;
    }
    const text = prompt.value.trim();
    if (!text) return;
    const command = controls.typed(text);
    if (command) {
        prompt.value = '';
        autosize();
        guard(command);
        return;
    }
    guard(send(text));
});
$('#more').onclick = () => controls.main();
$('#slash').onclick = () => controls.main();
prompt.addEventListener('input', () => { autosize(); paintComposer(); });
$('#back').onclick = () => history.back();
$('#pause').onclick = () => { if (app.selection) guard(api.control(app.selection, 'pause')); };
$('#new-after-close').onclick = () => {
    if (app.selection) guard(createSession({ serverId: app.selection.serverId, workspace: app.selection.workspace }));
};
addEventListener('hashchange', route);

// ---- live stream ----------------------------------------------------------------------------------------

function probeSession() {
    if (app.probeTimer) return;
    app.probeTimer = setTimeout(async () => {
        app.probeTimer = 0;
        try {
            await api.bootstrap();
        } catch (error) {
            // The browser session is gone (the page was open for days): a reload makes a new one.
            if (error.status === 401) location.reload();
        }
    }, PROBE_MS);
}

function resetStream(source, envelope) {
    apply({ type: 'stream.reset' });
    if (envelope.event.remote !== true) {
        app.cursor = Number(envelope.id) || 0;
        app.epoch = envelope.event.epoch || '';
        source.close();
        queueMicrotask(connect);
    }
    guard(recover());
    scheduleRefresh();
}

function connect() {
    app.source?.close();
    const source = api.events(app.cursor, app.epoch);
    app.source = source;
    source.onopen = () => {
        if (source !== app.source) return;
        app.transport = 'connected';
        paintLink();
        guard(recover());
        scheduleRefresh();
    };
    source.onmessage = message => {
        if (source !== app.source) return;
        let envelope;
        try {
            envelope = JSON.parse(message.data);
        } catch (error) {
            console.warn('unreadable live event', error);
            return;
        }
        if (!envelope?.event?.type) return;
        if (envelope.event.type === 'stream.reset') { resetStream(source, envelope); return; }
        const id = Number(envelope.id);
        if (Number.isFinite(id)) app.cursor = Math.max(app.cursor, id);
        onLive(envelope);
        if (NAVIGATION_EVENTS.has(envelope.event.type)) scheduleRefresh();
    };
    source.onerror = () => {
        if (source !== app.source) return;
        app.transport = 'reconnecting';
        paintLink();
        probeSession();
    };
}

async function boot() {
    try {
        const data = await api.bootstrap();
        const stream = data.event_stream || { cursor: data.event_cursor || 0, epoch: '' };
        app.navigation = data.navigation;
        app.cursor = Number(stream.cursor) || 0;
        app.epoch = stream.epoch || '';
        connect();
        $('#boot').hidden = true;
        route();
    } catch (error) {
        $('#boot-text').textContent = error.message;
        $('#boot-retry').hidden = false;
    }
}

$('#boot-retry').onclick = () => { $('#boot-retry').hidden = true; $('#boot-text').textContent = t('connecting'); boot(); };
$('#tagline').textContent = t('tagline');
$('#boot-text').textContent = t('connecting');
$('#back').ariaLabel = t('back');
$('#pause').textContent = t('pause');
$('#closed-text').textContent = t('closed');
$('#new-after-close').textContent = t('newSession');
boot();

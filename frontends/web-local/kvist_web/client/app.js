import { mountImageAttachments } from "./utils/image-attachments.js";
import { api } from '/assets/utils/api.js';
import { state, subscribe, update, serverOf, sessionOf, keyOf, busy, stateLabel } from '/assets/state/store.js';
import { renderNavigation } from '/assets/features/navigation.js';
import { applyLive, changeEcho, closeStaleInputs, echoSent, isCurrentSelection, loadDiff, openSession, recoverSelected, resetLive, revealFeed, selectionSnapshot, setAgentState, showPlace } from '/assets/features/session.js';
import { openView, closeView } from '/assets/features/views.js';
import { closePalette, openPalette } from '/assets/features/palette.js';
import { $, icons, toast, closeModal, trapFocus } from '/assets/utils/ui.js';
import { appear, slide, vanish } from '/assets/utils/motion.js';
import { configureConnections, handleConnectionLog, mountServerSwitcher, showServers, showServerModal, showWorkspaces, addWorkspace, showModel, showSettings } from '/assets/features/connections.js';
import { refreshConfiguration, ensureConfigured } from '/assets/features/configuration.js';
import { parseCommand, slashQuery } from '/assets/features/commands.js';
import { mountSlash, openSlash, showUnknown } from '/assets/features/slash.js';
import { mountChips, openChip } from '/assets/features/chips.js';
const NAVIGATION_EVENT_TYPES = new Set(['turn_end', 'error', 'input_request', 'session_title', 'agent.state_changed', 'agent.process_exited', 'connection.state_changed', 'connection.lost', 'connection.restored']);
const SLOW_CONNECT_MS = 1500;
let navigationGeneration = 0, eventSource = null, eventSourceGeneration = 0, eventProbeTimer = null, refreshTimer = null, sending = false, creatingSession = null;
let connectionValue = 'connecting', connectionSince = performance.now(), connectionTimer = null;
const drafts = state.drafts;
function paintNavigation() { renderNavigation(selectSession, newSession); renderConnection(); if (!state.selected && state.workspace)
    showPlace(state.workspace.serverId, state.workspace.workspace); }
export async function refresh() {
    const generation = ++navigationGeneration;
    const navigation = await api.navigation();
    if (generation !== navigationGeneration)
        return false;
    update(s => { s.navigation = navigation; });
    paintNavigation();
    const item = sessionOf();
    if (item) {
        setAgentState(item.execution);
        $('#session-title').textContent = item.title || item.name;
        if (!item.pending_input)
            closeStaleInputs(item.last_event_id);
    }
    return true;
}
function scheduleRefresh() { if (refreshTimer)
    return; refreshTimer = setTimeout(() => { refreshTimer = null; refresh().catch(() => { }); }, 120); }
// The drawer (a narrow window) slides in over the main column and its scrim fades in; leaving takes --dur-fast
// and the class goes when the animations have finished, not on a timer. With motion off it is at once.
let drawerAnimations = [], drawerClosing = false;
function resetDrawer() {
    for (const animation of drawerAnimations)
        animation.cancel();
    drawerAnimations = [];
    drawerClosing = false;
}
function setDrawer(open) {
    const app = $('#app');
    resetDrawer();
    if (open) {
        app.classList.add('sidebar-open');
        drawerAnimations = [slide($('#sidebar'), 'translateX(-100%)'), appear($('#sidebar-scrim'))].filter(Boolean);
        return;
    }
    if (!app.classList.contains('sidebar-open'))
        return;
    const leaving = [slide($('#sidebar'), 'translateX(-100%)', { out: true }), vanish($('#sidebar-scrim'))].filter(Boolean);
    if (!leaving.length) {
        app.classList.remove('sidebar-open');
        return;
    }
    drawerAnimations = leaving;
    drawerClosing = true;
    Promise.all(leaving.map(animation => animation.finished)).then(() => {
        for (const animation of leaving)
            animation.cancel();
        app.classList.remove('sidebar-open');
        drawerAnimations = [];
        drawerClosing = false;
    }).catch(error => {
        if (error.name !== 'AbortError')
            throw error; // only setDrawer cancelling the animations is expected here
    });
}
const drawerOpen = () => $('#app').classList.contains('sidebar-open') && !drawerClosing;
function collapseMobile() { if (matchMedia('(max-width:1000px)').matches)
    setDrawer(false); }
async function selectSession(serverId, workspace, session) {
    refreshConfiguration(serverId).catch(() => {});
    state.workspace = { serverId, workspace };
    collapseMobile();
    try {
        const opened = openSession(serverId, workspace, session);
        paintNavigation();
        await opened;
        paintNavigation();
    }
    catch (error) {
        toast(error.message, true);
    }
}
function chooseWorkspace(serverId, workspace) {
    refreshConfiguration(serverId, true).catch(error => toast(error.message, true));
    const entry = serverOf(serverId);
    if (entry?.connection.state !== 'connected') {
        showServers();
        return;
    }
    const sessions = entry.workspaces.find(w => w.path === workspace)?.sessions?.session_items || [];
    if (sessions.length) {
        selectSession(serverId, workspace, sessions[0].name);
        return;
    }
    state.workspace = { serverId, workspace };
    newSession({ serverId, workspace });
}
function chooseServer(serverId) { const entry = serverOf(serverId); const workspace = entry?.workspaces[0]?.path; if (workspace)
    chooseWorkspace(serverId, workspace);
else
    addWorkspace(serverId); }
configureConnections({ refresh, chooseServer, chooseWorkspace });
// place is {serverId, workspace}: a group's "+" or a workspace chosen without sessions. Without one the
// session goes where the selected session is, or into the workspace that holds the unsent draft.
function newSession(place) {
    // Sending immediately after New session must await the same creation request.
    // Two concurrent auto-name allocations can otherwise collide in the runtime.
    if (creatingSession)
        return creatingSession;
    const selection = place || state.selected || state.workspace;
    if (!selection?.workspace) {
        showWorkspaces();
        return Promise.resolve(false);
    }
    const target = { serverId: selection.serverId, workspace: selection.workspace };
    const button = $('#new-session');
    button.disabled = true;
    creatingSession = (async () => {
        try {
            const value = await api.createSession(target);
            await refresh();
            await selectSession(target.serverId, target.workspace, value.active_session);
            prompt.focus();
            return true;
        }
        catch (error) {
            toast(error.message, true);
            return false;
        }
        finally {
            button.disabled = false;
            creatingSession = null;
        }
    })();
    return creatingSession;
}
// The status line speaks only when the connection is not in order. Connecting is not yet a problem for
// SLOW_CONNECT_MS after the start and after each change of state.
function renderConnection() {
    const entry = serverOf((state.selected || state.workspace)?.serverId || 'local'), label = $('#connection-label');
    const value = state.transport !== 'connected' ? state.transport : entry?.connection.state || 'connected';
    if (value !== connectionValue) {
        connectionValue = value;
        connectionSince = performance.now();
    }
    const wait = value === 'connecting' ? SLOW_CONNECT_MS - (performance.now() - connectionSince) : 0;
    clearTimeout(connectionTimer);
    if (wait > 0)
        connectionTimer = setTimeout(renderConnection, wait);
    label.hidden = value === 'connected' || wait > 0;
    label.textContent = label.hidden ? '' : stateLabel(value);
    label.dataset.state = value;
    label.title = entry?.connection.error || '';
}
async function bootstrap() {
    try {
        $('#retry-startup').hidden = true;
        const data = await api.bootstrap(), stream = data.event_stream || { cursor: data.event_cursor || 0, epoch: '' };
        update(s => { s.navigation = data.navigation; s.liveCursor = Number(stream.cursor) || 0; s.liveEpoch = stream.epoch || ''; });
        paintNavigation();
        connectEvents();
        const first = state.navigation.servers.find(e => e.connection.state === 'connected' && e.workspaces.length);
        if (first) {
            const workspace = first.workspaces[0];
            state.workspace = { serverId: first.server.id, workspace: workspace.path };
            const session = workspace.sessions?.session_items?.[0];
            if (session)
                await selectSession(first.server.id, workspace.path, session.name);
            else
                paintNavigation();
        }
        // Expose session controls only after restoration finishes. Otherwise a
        // quick New session / Send can race the initial session selection.
        $('#startup').hidden = true;
        $('#app').hidden = false;
        revealFeed();
        await refreshConfiguration(undefined, true);
    }
    catch (error) {
        $('#startup-stage').textContent = error.message;
        $('#retry-startup').hidden = false;
    }
}
function probeBrowserSession(source, generation) {
    if (eventProbeTimer)
        return;
    eventProbeTimer = setTimeout(async () => {
        eventProbeTimer = null;
        if (source !== eventSource || generation !== eventSourceGeneration)
            return;
        try {
            await api.bootstrap();
        }
        catch (error) {
            if (error?.status === 401) {
                try {
                    await api.renewSession();
                    if (source === eventSource)
                        connectEvents();
                }
                catch (_) { /* EventSource and the next probe retry without losing the transcript. */ }
            }
        }
    }, 1200);
}
function recovered() { recoverSelected().then(count => { if (count)
    toast(`Back online, recovered ${count} events`); }).catch(error => toast(`Recovery paused: ${error.message}`, true)); refresh().catch(() => { }); }
function resetLiveStream(source, generation, envelope) {
    const event = envelope.event || {};
    resetLive();
    if (event.remote !== true) {
        state.liveCursor = Number(envelope.id) || 0;
        state.liveEpoch = event.epoch || '';
        source.close();
        if (source === eventSource && generation === eventSourceGeneration)
            queueMicrotask(connectEvents);
    }
    recovered();
}
function connectEvents() {
    clearTimeout(eventProbeTimer);
    eventProbeTimer = null;
    const generation = ++eventSourceGeneration;
    if (eventSource)
        eventSource.close();
    const source = api.events(state.liveCursor, state.liveEpoch);
    eventSource = source;
    source.onopen = () => { if (source !== eventSource || generation !== eventSourceGeneration)
        return; clearTimeout(eventProbeTimer); eventProbeTimer = null; state.transport = 'connected'; renderConnection(); recovered(); };
    source.onmessage = message => {
        if (source !== eventSource || generation !== eventSourceGeneration)
            return;
        let envelope;
        try {
            envelope = JSON.parse(message.data);
        }
        catch (_) {
            return;
        }
        if (!envelope?.event?.type)
            return;
        if (envelope.event.type === "stream.reset") {
            resetLiveStream(source, generation, envelope);
            return;
        }
        const id = Number(envelope.id);
        if (Number.isFinite(id))
            state.liveCursor = Math.max(state.liveCursor, id);
        if (envelope.event.type === 'connection.log') { handleConnectionLog(envelope); return; }
        applyLive(envelope);
        if (NAVIGATION_EVENT_TYPES.has(envelope.event.type))
            scheduleRefresh();
    };
    source.onerror = () => { if (source !== eventSource)
        return; state.transport = 'reconnecting'; renderConnection(); probeBrowserSession(source, generation); };
}
const form = $('#composer'), prompt = $('#prompt'), sendButton = $('#send-button');
mountServerSwitcher(form.querySelector('.composer-controls'));
const imageAttachments = mountImageAttachments(form, prompt, form.querySelector('.composer-controls'), message => toast(message, true));
// The module draws its button as a "+"; the client restyles it as an icon button with a paperclip.
const attach = form.querySelector('.image-attach-button');
attach.classList.add('icon-button');
attach.dataset.icon = 'paperclip';
icons(form);
// Sending is possible with text or an attachment, and not while a send is in flight.
function syncSend() {
    sendButton.disabled = sending || !(prompt.value.trim() || imageAttachments.count());
    sendButton.setAttribute('aria-disabled', String(sendButton.disabled));
}
new MutationObserver(syncSend).observe(form.querySelector('.image-attachments'), { childList: true, attributes: true, attributeFilter: ['hidden'] });
let imageSelection = keyOf(state.selected || state.workspace);
subscribe(() => { const next = keyOf(state.selected || state.workspace); if (next !== imageSelection && !sending) imageAttachments.clear(); imageSelection = next; });
form.addEventListener('submit', async (event) => {
    event.preventDefault();
    // "/word" is a command, never a task for the agent; /stop must work while the agent is busy.
    const typed = parseCommand(prompt.value);
    if (typed?.command) {
        prompt.value = '';
        prompt.dispatchEvent(new Event('input'));
        runCommand(typed.command);
    }
    if (typed) {
        if (typed.unknown)
            showUnknown(typed.unknown);
        return;
    }
    if (sending || busy(state.execution))
        return;
    await imageAttachments.ready();
    if (sending || busy(state.execution)) return;
    const text = prompt.value.trim();
    if (!text && !imageAttachments.count())
        return;
    sending = true;
    syncSend();
    // The message is in the feed at once, as "sending", once the session it goes to is open. A message with
    // images has no local copy: the journal appends their [Image #N] labels to the text, so the two could not be
    // matched. A failed send leaves the text in its block ("Not sent", Retry), not in the input.
    let echo = null, snapshot = null;
    const clearInput = () => { prompt.value = ''; prompt.dispatchEvent(new Event('input')); };
    const echoMessage = () => {
        echo = imageAttachments.count() ? null : echoSent(text);
        if (echo)
            clearInput();
    };
    const giveUp = () => {
        if (!echo)
            return;
        changeEcho(echo, 'unsent');
        prompt.value = text;
        prompt.dispatchEvent(new Event('input'));
    };
    try {
        if (state.selected && !creatingSession) {
            snapshot = selectionSnapshot();
            echoMessage();
        }
        if (!(await ensureConfigured())) {
            giveUp();
            return;
        }
        if (creatingSession && !(await creatingSession))
            return;
        if (!state.selected) {
            drafts.set(keyOf(state.workspace), text);
            if (!(await newSession()) || !state.selected)
                return;
            prompt.value = text;
        }
        if (!snapshot) {
            snapshot = selectionSnapshot();
            if (!snapshot)
                return;
            echoMessage();
        }
        await api.send(snapshot.selection, imageAttachments.pack(text));
        imageAttachments.clear();
        drafts.delete(keyOf(snapshot.selection));
        if (isCurrentSelection(snapshot)) {
            if (!echo)
                clearInput();
            setAgentState('running');
        }
        scheduleRefresh();
    }
    catch (error) {
        if (echo)
            changeEcho(echo, 'failed');
        toast(`Message was not sent. ${error.message}`, true);
    }
    finally {
        sending = false;
        syncSend();
    }
});
// Retry on a "Not sent" message: the same block goes back to "sending", so a success leaves one message.
document.addEventListener('kvist:retry-send', async (event) => {
    const { localId, text } = event.detail, snapshot = selectionSnapshot();
    if (!snapshot || sending || busy(state.execution))
        return;
    sending = true;
    syncSend();
    changeEcho(localId, 'retry');
    try {
        await api.send(snapshot.selection, text);
        if (isCurrentSelection(snapshot))
            setAgentState('running');
        scheduleRefresh();
    }
    catch (error) {
        changeEcho(localId, 'failed');
        toast(`Message was not sent. ${error.message}`, true);
    }
    finally {
        sending = false;
        syncSend();
    }
});
// The field is 56px (CSS); it grows only once the text overflows it. Setting the height from scrollHeight on every
// input made it 56px while the app was hidden and 57px once typed in, which moved everything below by 1px.
prompt.addEventListener('input', () => { prompt.style.height = ''; if (prompt.scrollHeight > prompt.clientHeight) prompt.style.height = `${Math.min(prompt.scrollHeight, 180)}px`; drafts.set(keyOf(state.selected || state.workspace), prompt.value); syncSend(); });
prompt.addEventListener('keydown', event => { if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && !event.isComposing) {
    event.preventDefault();
    form.requestSubmit();
} });
$('#stop-agent').onclick = async () => { const snapshot = selectionSnapshot(); if (!snapshot)
    return; const button = $('#stop-agent'); button.disabled = true; try {
    await api.stop(snapshot.selection);
    toast('Stop requested');
    scheduleRefresh();
}
catch (error) {
    toast(error.message, true);
}
finally {
    button.disabled = false;
} };
function paletteChoice(item) { if (item.selection?.session)
    selectSession(item.selection.serverId, item.selection.workspace, item.selection.session);
else if (item.selection)
    chooseWorkspace(item.selection.serverId, item.selection.workspace);
else
    command(item.label); }
function command(label) {
    if (label === 'New session')
        newSession();
    else if (label === 'Show unread' || label === 'Show inbox')
        openPalette("inbox", paletteChoice);
    else if (label === 'Switch session')
        openPalette('search', paletteChoice);
    else if (label === 'Switch workspace')
        showWorkspaces();
    else if (label === 'Switch server')
        showServers();
    else if (label === 'Connect server')
        showServerModal();
    else if (label === 'Add workspace')
        addWorkspace();
    else if (label === 'Stop agent') {
        if (busy(state.execution))
            $('#stop-agent').click();
        else
            toast('No agent is running in this session.');
    }
    else if (label === 'Settings')
        showSettings();
    else if (label.startsWith('Open '))
        openView(label.slice(5).toLowerCase());
}
const commands = () => openPalette('commands', paletteChoice);
function runCommand({ name }) {
    if (name === 'provider')
        showModel();
    else if (name === 'model' || name === 'role')
        openChip(name);
    else if (name === 'reasoning')
        openChip('effort');
    else if (name === 'new')
        newSession();
    else if (name === 'stop')
        command('Stop agent');
    else
        openView(name);
}
mountSlash($('.composer-area'), { run: runCommand, all: commands });
mountChips();
$('#slash-button').onclick = () => prompt.value && slashQuery(prompt.value) === null ? commands() : openSlash();
$('#search-button').onclick = () => openPalette('search', paletteChoice);
$('#new-session').onclick = () => newSession();
$('#add-workspace').onclick = () => addWorkspace();
$('#settings-button').onclick = showSettings;
$('#inbox-button').onclick = () => openPalette("inbox", paletteChoice);
$('#commands-button').onclick = commands;
$('#more-tools').onclick = commands;
$('#context-button').onclick = () => openView('context');
$('#retry-startup').onclick = bootstrap;
for (const button of document.querySelectorAll('[data-view]'))
    button.onclick = () => openView(button.dataset.view);
function toggleSidebar() {
    const app = $('#app'), small = matchMedia('(max-width:1000px)').matches;
    if (small)
        setDrawer(!drawerOpen());
    else
        app.classList.toggle('sidebar-collapsed');
    $('#toggle-sidebar').setAttribute('aria-expanded', String(small ? drawerOpen() : !app.classList.contains('sidebar-collapsed')));
}
$('#toggle-sidebar').onclick = toggleSidebar;
$('#sidebar-scrim').onclick = toggleSidebar;
const smallMedia = matchMedia('(max-width:1000px)');
smallMedia.addEventListener('change', () => { resetDrawer(); $('#app').classList.remove('sidebar-open'); $('#toggle-sidebar').setAttribute('aria-expanded', String(!smallMedia.matches && !$('#app').classList.contains('sidebar-collapsed'))); });
$('#toggle-sidebar').setAttribute('aria-expanded', String(!smallMedia.matches));
document.addEventListener('keydown', event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        closeModal();
        closeView();
        commands();
    }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "p") {
        event.preventDefault();
        closeModal();
        closeView();
        openPalette('search', paletteChoice);
    }
    if (event.key === 'Escape') {
        if (!$('#modal').hidden)
            closeModal();
        else if (!$('#palette').hidden)
            closePalette();
        else if (!$('#workspace-view').hidden)
            closeView();
        else if ($('#app').classList.contains('sidebar-open')) {
            collapseMobile();
            $('#toggle-sidebar').focus();
        }
    }
    if (event.key === 'Tab') {
        const active = !$('#modal').hidden ? $('#modal') : !$('#palette').hidden ? $('#palette') : !$('#workspace-view').hidden ? $('#workspace-view') : null;
        if (active)
            trapFocus(event, active);
    }
});
// A closed session has only its banner. Enter with nothing focused starts the next session; a focused button
// takes its own Enter, and a repeated key (held down) never starts a second session.
document.addEventListener('kvist:new-session', () => newSession());
document.addEventListener('keydown', event => {
    if (event.key !== 'Enter' || event.repeat || event.isComposing || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey)
        return;
    if (!$('.closed-banner') || document.activeElement !== document.body || document.querySelector('.overlay:not([hidden])') || !$('#pending-input').hidden)
        return;
    event.preventDefault();
    newSession();
});
$('#modal').addEventListener('mousedown', event => { if (event.target === event.currentTarget)
    closeModal(); });
document.addEventListener('kvist:navigation-refresh', scheduleRefresh);
document.addEventListener('kvist:view-open', event => { if (event.detail === 'diff')
    loadDiff(); });
icons();
bootstrap();

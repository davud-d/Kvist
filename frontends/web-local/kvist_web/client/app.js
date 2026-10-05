import { mountImageAttachments } from "./utils/image-attachments.js";
import { api } from '/assets/utils/api.js';
import { state, subscribe, update, serverOf, sessionOf, keyOf, busy, stateLabel } from '/assets/state/store.js';
import { renderNavigation } from '/assets/features/navigation.js';
import { applyLive, isCurrentSelection, loadDiff, openSession, recoverSelected, selectionSnapshot, setAgentState } from '/assets/features/session.js';
import { openView, closeView } from '/assets/features/views.js';
import { closePalette, openPalette } from '/assets/features/palette.js';
import { $, icons, toast, closeModal, trapFocus } from '/assets/utils/ui.js';
import { configureConnections, handleConnectionLog, showServers, showServerModal, showWorkspaces, addWorkspace, showModel, showSettings } from '/assets/features/connections.js';
import { refreshConfiguration, ensureConfigured } from '/assets/features/configuration.js';
const NAVIGATION_EVENT_TYPES = new Set(['turn_end', 'error', 'input_request', 'session_title', 'agent.state_changed', 'agent.process_exited', 'connection.state_changed', 'connection.lost', 'connection.restored']);
let navigationGeneration = 0, eventSource = null, eventSourceGeneration = 0, eventProbeTimer = null, refreshTimer = null, sending = false, creatingSession = null;
const drafts = state.drafts;
function paintNavigation() { renderNavigation(selectSession, chooseWorkspace); renderConnection(); if (!state.selected && state.workspace)
    $('#session-path').textContent = state.workspace.workspace; }
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
    }
    return true;
}
function scheduleRefresh() { if (refreshTimer)
    return; refreshTimer = setTimeout(() => { refreshTimer = null; refresh().catch(() => { }); }, 120); }
function collapseMobile() { if (matchMedia('(max-width:1000px)').matches)
    $('#app').classList.remove('sidebar-open'); }
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
    newSession();
}
function chooseServer(serverId) { const entry = serverOf(serverId); const workspace = entry?.workspaces[0]?.path; if (workspace)
    chooseWorkspace(serverId, workspace);
else
    addWorkspace(serverId); }
configureConnections({ refresh, chooseServer, chooseWorkspace });
function newSession() {
    // Sending immediately after New session must await the same creation request.
    // Two concurrent auto-name allocations can otherwise collide in the runtime.
    if (creatingSession)
        return creatingSession;
    const selection = state.workspace || state.selected;
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
            $('#prompt').focus();
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
function renderConnection() {
    const entry = serverOf((state.selected || state.workspace)?.serverId || 'local');
    const value = state.transport !== 'connected' ? state.transport : entry?.connection.state || 'connected';
    $('#connection-dot').className = `connection-dot ${value}`;
    $('#connection-label').textContent = stateLabel(value);
    $('#connection-label').title = entry?.connection.error || 'Connection to Kvist';
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
    toast(`Connected · recovered ${count} events`); }).catch(error => toast(`Recovery paused: ${error.message}`, true)); refresh().catch(() => { }); }
function resetLiveStream(source, generation, envelope) {
    const event = envelope.event || {};
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
const form = $('#composer'), prompt = $('#prompt');
const imageAttachments = mountImageAttachments(form, prompt, form.querySelector('.composer-controls'), message => toast(message, true));
let imageSelection = keyOf(state.selected || state.workspace);
subscribe(() => { const next = keyOf(state.selected || state.workspace); if (next !== imageSelection && !sending) imageAttachments.clear(); imageSelection = next; });
form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (sending || busy(state.execution))
        return;
    await imageAttachments.ready();
    if (sending || busy(state.execution)) return;
    const text = prompt.value.trim();
    if (!text && !imageAttachments.count())
        return;
    sending = true;
    $('#send-button').disabled = true;
    try {
        if (!(await ensureConfigured())) return;
        if (creatingSession && !(await creatingSession))
            return;
        if (!state.selected) {
            drafts.set(keyOf(state.workspace), text);
            if (!(await newSession()) || !state.selected)
                return;
            prompt.value = text;
        }
        const snapshot = selectionSnapshot();
        if (!snapshot)
            return;
        await api.send(snapshot.selection, imageAttachments.pack(text));
        imageAttachments.clear();
        drafts.delete(keyOf(snapshot.selection));
        if (isCurrentSelection(snapshot)) {
            prompt.value = '';
            prompt.dispatchEvent(new Event('input'));
            setAgentState('running');
        }
        scheduleRefresh();
    }
    catch (error) {
        toast(`Message was not sent. ${error.message}`, true);
    }
    finally {
        sending = false;
        $('#send-button').disabled = false;
    }
});
prompt.addEventListener('input', () => { prompt.style.height = 'auto'; prompt.style.height = `${Math.min(prompt.scrollHeight, 180)}px`; drafts.set(keyOf(state.selected || state.workspace), prompt.value); });
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
$('#search-button').onclick = () => openPalette('search', paletteChoice);
$('#workspace-switcher').onclick = showWorkspaces;
$('#server-switcher').onclick = showServers;
$('#new-session').onclick = newSession;
$('#add-workspace').onclick = () => addWorkspace();
$('#settings-button').onclick = showSettings;
$('#model-selector').onclick = showModel;
$('#inbox-button').onclick = () => openPalette("inbox", paletteChoice);
$('#commands-button').onclick = commands;
$('#more-tools').onclick = commands;
$('#composer-tools').onclick = commands;
$('#context-button').onclick = () => openView('context');
$('#retry-startup').onclick = bootstrap;
for (const button of document.querySelectorAll('[data-view]'))
    button.onclick = () => openView(button.dataset.view);
function toggleSidebar() { const app = $('#app'), small = matchMedia('(max-width:1000px)').matches; app.classList.toggle(small ? 'sidebar-open' : 'sidebar-collapsed'); $('#toggle-sidebar').setAttribute('aria-expanded', String(small ? app.classList.contains('sidebar-open') : !app.classList.contains('sidebar-collapsed'))); }
$('#toggle-sidebar').onclick = toggleSidebar;
$('#sidebar-scrim').onclick = toggleSidebar;
const smallMedia = matchMedia('(max-width:1000px)');
smallMedia.addEventListener('change', () => { $('#app').classList.remove('sidebar-open'); $('#toggle-sidebar').setAttribute('aria-expanded', String(!smallMedia.matches && !$('#app').classList.contains('sidebar-collapsed'))); });
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
$('#modal').addEventListener('mousedown', event => { if (event.target === event.currentTarget)
    closeModal(); });
document.addEventListener('kvist:navigation-refresh', scheduleRefresh);
document.addEventListener('kvist:view-open', event => { if (event.detail === 'diff')
    loadDiff(); });
icons();
bootstrap();

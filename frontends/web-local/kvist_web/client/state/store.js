const listeners = new Set();
export const state = {
    navigation: { servers: [], inbox: [] }, selected: null, workspace: null,
    events: [], eventSeq: 0, liveCursor: 0, liveEpoch: '', inspectorTab: 'activity',
    diffReport: '', execution: 'idle', usage: { input: 0, output: 0, calls: 0 },
    context: null, model: '', hello: null, modelInfo: new Map(), drafts: new Map(), transport: 'connecting',
    openView: '',
};
export function update(change) { change(state); for (const listener of listeners)
    listener(state); }
export function subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); }
export function keyOf(selection) { return selection ? [selection.serverId, selection.workspace, selection.session || ''].join('\u0000') : ''; }
export function serverOf(id) { return state.navigation.servers.find(item => item.server.id === id); }
export function sessionOf(selection = state.selected) {
    if (!selection)
        return null;
    const workspace = serverOf(selection.serverId)?.workspaces.find(item => item.path === selection.workspace);
    return workspace?.sessions?.session_items.find(item => item.name === selection.session) || null;
}
export const busy = value => ['running', 'starting', 'waiting_for_user'].includes(value);
const KNOWN_EXECUTIONS = new Set(['idle', 'finished', 'stopped', 'running', 'starting', 'waiting_for_user', 'failed']), warned = new Set();
// An execution this client does not know stays as it is (nothing is drawn for it); one console warning per value.
export function execution(value) {
    const known = typeof value === 'string' && value ? value : 'idle';
    if (!KNOWN_EXECUTIONS.has(known) && !warned.has(known)) {
        warned.add(known);
        console.warn(`Unknown session execution: ${known}`);
    }
    return known;
}
export const stateLabel = value => ({ running: 'Running', starting: 'Starting', waiting_for_user: 'Needs input', finished: 'Finished', failed: 'Failed', stopped: 'Stopped', idle: 'Idle', connected: 'Connected', connecting: 'Connecting…', reconnecting: 'Reconnecting…', offline: 'Offline', trust_required: 'Verify host key' }[value] || String(value || 'Idle'));

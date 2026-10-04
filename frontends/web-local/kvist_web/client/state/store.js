const listeners = new Set();
export const state = {
    navigation: { servers: [], inbox: [] }, selected: null, workspace: null,
    events: [], eventSeq: 0, liveCursor: 0, liveEpoch: '', inspectorTab: 'activity',
    diffReport: '', execution: 'idle', usage: { input: 0, output: 0, calls: 0 },
    context: null, model: '', hello: null, modelInfo: new Map(), drafts: new Map(), transport: 'connecting',
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
export const stateLabel = value => ({ running: 'Running', starting: 'Starting', waiting_for_user: 'Needs input', finished: 'Finished', failed: 'Failed', stopped: 'Stopped', idle: 'Idle', connected: 'Connected', connecting: 'Connecting…', reconnecting: 'Reconnecting…', offline: 'Offline', trust_required: 'Verify host key' }[value] || 'Idle');

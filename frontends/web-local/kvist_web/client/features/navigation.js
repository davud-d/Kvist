import { state, keyOf, stateLabel } from '/assets/state/store.js';
import { $, el } from '/assets/utils/ui.js';
const nameOf = path => path.split(/[\\/]/).filter(Boolean).at(-1) || path;
let lastSignature = '';
export function renderNavigation(onSelect, onWorkspace) {
    const signature = JSON.stringify([state.navigation, state.selected, state.workspace]);
    if (signature === lastSignature)
        return;
    lastSignature = signature;
    const nav = $('#navigation'), focused = document.activeElement?.dataset?.key, scroll = nav.scrollTop;
    const fragment = document.createDocumentFragment();
    $('#inbox-count').textContent = String(state.navigation.inbox.length);
    for (const entry of state.navigation.servers)
        for (const workspace of entry.workspaces) {
            const group = el('section', 'nav-group'), heading = el('button', 'nav-workspace-head');
            heading.append(el('span', '', `${nameOf(workspace.path)}${entry.server.id === 'local' ? '' : ' · ' + entry.server.name}`));
            heading.title = workspace.path;
            heading.onclick = () => onWorkspace?.(entry.server.id, workspace.path);
            group.append(heading);
            const sessions = workspace.sessions?.session_items || [];
            if (!sessions.length)
                group.append(el('div', 'menu-caption', entry.connection.state === 'connected' ? 'No sessions yet' : stateLabel(entry.connection.state)));
            for (const session of sessions) {
                const selection = { serverId: entry.server.id, workspace: workspace.path, session: session.name }, selected = keyOf(selection) === keyOf(state.selected);
                const button = el('button', `nav-session${selected ? ' active' : ''}`), execution = session.execution || 'idle';
                const indicator = ['running', 'starting', 'waiting_for_user', 'failed'].includes(execution) ? execution : session.unread ? 'unread' : execution;
                button.dataset.key = keyOf(selection);
                button.setAttribute('aria-current', selected ? 'page' : 'false');
                button.title = `${session.title || session.name} · ${stateLabel(execution)}${session.unread ? ' · Unread' : ''}`;
                button.setAttribute('aria-label', button.title);
                const dot = el('span', `status-dot ${indicator}`);
                dot.setAttribute('aria-hidden', 'true');
                button.append(el('span', 'nav-title', session.title || session.name), dot);
                button.onclick = () => onSelect(entry.server.id, workspace.path, session.name);
                group.append(button);
            }
            fragment.append(group);
        }
    nav.replaceChildren(fragment);
    nav.scrollTop = scroll;
    if (focused)
        [...nav.querySelectorAll('[data-key]')].find(n => n.dataset.key === focused)?.focus({ preventScroll: true });
    const selected = state.selected || state.workspace;
    $('#workspace-label').textContent = selected ? nameOf(selected.workspace) : 'Choose workspace';
    $('#workspace-switcher').title = selected?.workspace || 'Switch workspace';
    $('#active-server-label').textContent = selected?.serverId === 'local' || !selected ? 'Local' : state.navigation.servers.find(s => s.server.id === selected.serverId)?.server.name || selected.serverId;
}
export function paletteItems() {
    const items = [];
    for (const entry of state.navigation.servers)
        for (const workspace of entry.workspaces) {
            items.push({ type: 'workspace', label: nameOf(workspace.path), detail: entry.server.name, selection: { serverId: entry.server.id, workspace: workspace.path } });
            for (const session of workspace.sessions?.session_items || [])
                items.push({ type: 'session', label: session.title || session.name, detail: `${entry.server.name} / ${nameOf(workspace.path)}`, selection: { serverId: entry.server.id, workspace: workspace.path, session: session.name } });
        }
    return items;
}
export function inboxItems() { return (state.navigation.inbox || []).map(item => ({ type: 'inbox', label: item.title || item.session, detail: `${item.server_name || item.server_id} · ${stateLabel(item.state)}`, selection: { serverId: item.server_id, workspace: item.workspace, session: item.session } })); }

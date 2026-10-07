import { state, keyOf, stateLabel, execution } from '/assets/state/store.js';
import { $, chevron, el, icons } from '/assets/utils/ui.js';
const nameOf = path => path.split(/[\\/]/).filter(Boolean).at(-1) || path;
const COLLAPSED_KEY = 'kvist.nav.collapsed';
// Indicator kinds, most urgent first; the label is what a screen reader hears in place of the shape.
const LABELS = { waiting: 'waiting for your answer', failed: 'failed', running: 'running', unread: 'unread' };
const collapsed = loadCollapsed();
let lastSignature = '', handlers = [];
function loadCollapsed() {
    try {
        const keys = JSON.parse(localStorage.getItem(COLLAPSED_KEY));
        return new Set(Array.isArray(keys) ? keys : []);
    }
    catch (error) { return new Set(); } // blocked or corrupt storage: every group starts open
}
function saveCollapsed() {
    try { localStorage.setItem(COLLAPSED_KEY, JSON.stringify([...collapsed])); }
    catch (error) { /* blocked storage: the choice lasts until the page reloads */ }
}
function indicatorOf(session) {
    const value = execution(session.execution);
    if (value === 'waiting_for_user')
        return 'waiting';
    if (value === 'failed')
        return 'failed';
    if (value === 'running' || value === 'starting')
        return 'running';
    return session.unread ? 'unread' : '';
}
function indicator(kind) {
    const node = el('span', `nav-ind${kind ? ' ind-' + kind : ''}`);
    if (kind) {
        node.setAttribute('role', 'img');
        node.setAttribute('aria-label', LABELS[kind]);
    }
    return node;
}
function sessionRow(entry, workspace, session, onSelect) {
    const selection = { serverId: entry.server.id, workspace: workspace.path, session: session.name }, selected = keyOf(selection) === keyOf(state.selected);
    const kind = indicatorOf(session), title = session.title || session.name;
    const button = el('button', `nav-session${selected ? ' active' : ''}${kind === 'running' && session.unread ? ' unread-running' : ''}`);
    button.dataset.key = keyOf(selection);
    button.setAttribute('aria-current', selected ? 'page' : 'false');
    button.title = `${title}, ${stateLabel(session.execution || 'idle')}${session.unread ? ', Unread' : ''}`;
    button.setAttribute('aria-label', button.title);
    button.append(el('span', 'nav-title', title), indicator(kind));
    button.onclick = () => onSelect(entry.server.id, workspace.path, session.name);
    return button;
}
function group(entry, workspace, onSelect, onNewSession) {
    const key = keyOf({ serverId: entry.server.id, workspace: workspace.path }), closed = collapsed.has(key), name = nameOf(workspace.path);
    const sessions = workspace.sessions?.session_items || [];
    const node = el('section', 'nav-group'), head = el('div', 'nav-group-head'), toggle = el('button', 'nav-group-toggle'), label = el('span', 'nav-group-label');
    toggle.dataset.key = `group:${key}`;
    toggle.title = workspace.path;
    toggle.setAttribute('aria-expanded', String(!closed));
    label.append(el('span', 'nav-group-name', name));
    if (entry.server.id !== 'local')
        label.append(el('span', 'nav-group-server', entry.server.name));
    toggle.append(chevron(), label);
    if (closed) {
        const kinds = sessions.map(indicatorOf), kind = Object.keys(LABELS).find(item => kinds.includes(item));
        if (kind)
            toggle.append(indicator(kind));
    }
    toggle.onclick = () => {
        if (!collapsed.delete(key))
            collapsed.add(key);
        saveCollapsed();
        renderNavigation(...handlers);
    };
    head.append(toggle);
    if (entry.connection.state === 'connected') {
        const add = el('button', 'nav-group-add');
        add.dataset.key = `new:${key}`;
        add.append(el('span'));
        add.firstChild.dataset.icon = 'plus';
        add.title = `New session in ${name}`;
        add.setAttribute('aria-label', add.title);
        add.onclick = () => onNewSession({ serverId: entry.server.id, workspace: workspace.path });
        icons(add);
        head.append(add);
    }
    node.append(head);
    if (closed)
        return node;
    if (!sessions.length)
        node.append(el('div', 'menu-caption', entry.connection.state === 'connected' ? 'No sessions yet' : stateLabel(entry.connection.state)));
    for (const session of sessions)
        node.append(sessionRow(entry, workspace, session, onSelect));
    return node;
}
export function renderNavigation(onSelect, onNewSession) {
    handlers = [onSelect, onNewSession];
    const signature = JSON.stringify([state.navigation, state.selected, [...collapsed]]);
    if (signature === lastSignature)
        return;
    lastSignature = signature;
    const nav = $('#navigation'), focused = document.activeElement?.dataset?.key, scroll = nav.scrollTop;
    const fragment = document.createDocumentFragment();
    const unread = state.navigation.inbox.length;
    $('#inbox-count').hidden = !unread;
    $('#inbox-count').textContent = unread > 99 ? '99+' : String(unread);
    for (const entry of state.navigation.servers)
        for (const workspace of entry.workspaces)
            fragment.append(group(entry, workspace, onSelect, onNewSession));
    nav.replaceChildren(fragment);
    nav.scrollTop = scroll;
    if (focused)
        [...nav.querySelectorAll('[data-key]')].find(n => n.dataset.key === focused)?.focus({ preventScroll: true });
    const selected = state.selected || state.workspace;
    const label = $('#active-server-label');
    if (label)
        label.textContent = selected?.serverId === 'local' || !selected ? 'Local' : state.navigation.servers.find(s => s.server.id === selected.serverId)?.server.name || selected.serverId;
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
export function inboxItems() { return (state.navigation.inbox || []).map(item => ({ type: 'inbox', label: item.title || item.session, detail: `${item.server_name || item.server_id}, ${stateLabel(item.state)}`, selection: { serverId: item.server_id, workspace: item.workspace, session: item.session } })); }

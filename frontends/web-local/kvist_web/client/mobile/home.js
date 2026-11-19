import { el } from '/assets/utils/ui.js';
import { t } from './i18n.js';

// The home screen's lists: what needs the person, then every project with its sessions. A state is a
// colored square and a word, so it does not rest on color alone.

const STATE_WORDS = {
    running: 'running', starting: 'starting', waiting_for_user: 'waitingState', finished: 'finished',
    failed: 'failed', stopped: 'stopped', idle: 'idle',
};
export const stateWord = state => t(STATE_WORDS[state] || 'idle');

const folderName = path => path.split(/[\\/]/).filter(Boolean).pop() || path;

const isSelected = (selected, server, workspace, name) => Boolean(selected) && selected.serverId === server && selected.workspace === workspace && selected.session === name;

function sessionRow(server, workspace, item, { open, selected }) {
    const state = item.execution || 'idle';
    const row = el('button', `session${item.unread ? ' unread' : ''}`);
    row.type = 'button';
    row.dataset.state = state;
    if (isSelected(selected, server, workspace, item.name)) row.ariaCurrent = 'true';
    row.append(
        el('span', `dot ${state}`),
        el('span', 'session-title', item.title || t('untitled')),
        el('span', 'session-state', stateWord(state)),
    );
    row.onclick = () => open({ serverId: server, workspace, session: item.name });
    return row;
}

function inboxRow(entry, { open, selected }) {
    const row = el('button', 'session');
    row.type = 'button';
    row.dataset.state = entry.state;
    if (isSelected(selected, entry.server_id, entry.workspace, entry.session)) row.ariaCurrent = 'true';
    row.append(
        el('span', `dot ${entry.state}`),
        el('span', 'session-title', entry.title || entry.session),
        el('span', 'session-state', stateWord(entry.state)),
    );
    row.onclick = () => open({ serverId: entry.server_id, workspace: entry.workspace, session: entry.session });
    return row;
}

// The way to start a session is the yellow "+" beside the folder's name. `create` returns a promise; the button
// shows it is working and ignores taps until it is done, so a slow disk cannot turn two taps into two sessions.
function projectCard(server, workspace, handlers, showServer) {
    const { create } = handlers;
    const card = el('section', 'card');
    const head = el('div', 'card-head');
    const names = el('div', 'card-names');
    names.append(el('h3', 'card-title', folderName(workspace.path)), el('p', 'card-path', showServer ? `${server.name} · ${workspace.path}` : workspace.path));
    const add = el('button', 'btn small primary', '+');
    add.type = 'button';
    add.title = add.ariaLabel = t('newSession');
    add.onclick = async () => {
        if (add.ariaBusy === 'true') return;
        add.ariaBusy = 'true';
        try {
            await create({ serverId: server.id, workspace: workspace.path });
        } finally {
            add.ariaBusy = 'false';
        }
    };
    head.append(names, add);
    card.append(head);
    const items = workspace.sessions?.session_items || [];
    if (!items.length) card.append(el('p', 'card-empty', t('noSessions')));
    for (const item of items) card.append(sessionRow(server.id, workspace.path, item, handlers));
    return card;
}

export function counts(navigation) {
    let waiting = 0, working = 0;
    for (const server of navigation.servers) {
        for (const workspace of server.workspaces) {
            for (const item of workspace.sessions?.session_items || []) {
                if (item.execution === 'waiting_for_user') waiting++;
                else if (item.execution === 'running' || item.execution === 'starting') working++;
            }
        }
    }
    return { waiting, working };
}

// handlers: open(selection), create(target), selected (the open session, marked in the lists, or null).
export function renderHome(root, navigation, handlers) {
    const parts = [];
    if (navigation.inbox?.length) {
        const inbox = el('section', 'card inbox');
        inbox.append(el('h2', 'label', t('inbox')));
        for (const entry of navigation.inbox) inbox.append(inboxRow(entry, handlers));
        parts.push(inbox);
    }
    const servers = navigation.servers.filter(entry => entry.connection?.state === 'connected');
    const folders = servers.flatMap(entry => entry.workspaces.map(workspace => ({ server: entry.server, workspace })));
    const solo = folders.length === 1;
    const projects = folders.map(({ server, workspace }) => projectCard(server, workspace, handlers, servers.length > 1));
    parts.push(el('h2', 'label', t(solo ? 'thisFolder' : 'projects')));
    if (projects.length) parts.push(...projects);
    else parts.push(el('p', 'card-empty', t('noProjects')));
    root.replaceChildren(...parts);
}

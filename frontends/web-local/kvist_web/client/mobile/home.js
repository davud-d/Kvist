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

function sessionRow(server, workspace, item, open) {
    const state = item.execution || 'idle';
    const row = el('button', `session${item.unread ? ' unread' : ''}`);
    row.type = 'button';
    row.dataset.state = state;
    row.append(
        el('span', `dot ${state}`),
        el('span', 'session-title', item.title || t('untitled')),
        el('span', 'session-state', stateWord(state)),
    );
    row.onclick = () => open({ serverId: server, workspace, session: item.name });
    return row;
}

function inboxRow(entry, open) {
    const row = el('button', 'session');
    row.type = 'button';
    row.dataset.state = entry.state;
    row.append(
        el('span', `dot ${entry.state}`),
        el('span', 'session-title', entry.title || entry.session),
        el('span', 'session-state', stateWord(entry.state)),
    );
    row.onclick = () => open({ serverId: entry.server_id, workspace: entry.workspace, session: entry.session });
    return row;
}

// `solo`: the computer shows one folder (the one Kvist runs in), so there is nothing to choose and the way
// to start a session is a large button under the list instead of a small "+".
function projectCard(server, workspace, { open, create }, showServer, solo) {
    const card = el('section', 'card');
    const head = el('div', 'card-head');
    const names = el('div', 'card-names');
    names.append(el('h3', 'card-title', folderName(workspace.path)), el('p', 'card-path', showServer ? `${server.name} · ${workspace.path}` : workspace.path));
    const add = el('button', solo ? 'btn primary new-session' : 'btn small', solo ? `+ ${t('newSession')}` : '+');
    add.type = 'button';
    add.title = add.ariaLabel = t('newSession');
    add.onclick = () => create({ serverId: server.id, workspace: workspace.path });
    head.append(names);
    if (!solo) head.append(add);
    card.append(head);
    const items = workspace.sessions?.session_items || [];
    if (!items.length) card.append(el('p', 'card-empty', t('noSessions')));
    for (const item of items) card.append(sessionRow(server.id, workspace.path, item, open));
    if (solo) card.append(add);
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

export function renderHome(root, navigation, handlers) {
    const parts = [];
    if (navigation.inbox?.length) {
        const inbox = el('section', 'card inbox');
        inbox.append(el('h2', 'label', t('inbox')));
        for (const entry of navigation.inbox) inbox.append(inboxRow(entry, handlers.open));
        parts.push(inbox);
    }
    const servers = navigation.servers.filter(entry => entry.connection?.state === 'connected');
    const folders = servers.flatMap(entry => entry.workspaces.map(workspace => ({ server: entry.server, workspace })));
    const solo = folders.length === 1;
    const projects = folders.map(({ server, workspace }) => projectCard(server, workspace, handlers, servers.length > 1, solo));
    parts.push(el('h2', 'label', t(solo ? 'thisFolder' : 'projects')));
    if (projects.length) parts.push(...projects);
    else parts.push(el('p', 'card-empty', t('noProjects')));
    root.replaceChildren(...parts);
}

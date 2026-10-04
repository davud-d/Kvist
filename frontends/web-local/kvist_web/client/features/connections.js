import { api } from '/assets/utils/api.js';
import { state, serverOf, stateLabel } from '/assets/state/store.js';
import { $, el, esc, modal, closeModal, toast } from '/assets/utils/ui.js';
let handlers = {};
export function configureConnections(value) { handlers = value; }
export function showServers() {
    const root = modal('Run with Kvist', '<p>Choose where to work. Each server keeps its own workspaces and sessions.</p><div id="server-list"></div><div class="menu-divider"></div><button id="connect-server" class="quiet-button">＋ Connect server</button>');
    for (const entry of state.navigation.servers) {
        const button = el('button', `server-row${(state.selected || state.workspace)?.serverId === entry.server.id ? ' selected' : ''}`);
        button.append(el('span', '', entry.server.id === 'local' ? 'Local' : entry.server.name), el('small', '', stateLabel(entry.connection.state)));
        button.onclick = async () => { if (entry.connection.state === 'connected') {
            closeModal();
            handlers.chooseServer(entry.server.id);
        }
        else
            showReconnect(entry); };
        root.querySelector('#server-list').append(button);
    }
    root.querySelector('#connect-server').onclick = () => showServerModal();
}
function showReconnect(entry) {
    const root = modal(entry.server.name, `<p>${esc(stateLabel(entry.connection.state))}. Reconnect to open this server’s workspaces.</p><div id="connection-progress"></div><div class="modal-actions"><button id="edit-server" class="quiet-button">Edit connection</button><button id="retry-server" class="primary-button">Connect</button></div>`);
    root.querySelector('#edit-server').onclick = () => showServerModal(entry.server);
    root.querySelector('#retry-server').onclick = () => connect(entry.server.id, entry.server.name, root);
}
let activeConnection = null;
function appendConnectionLog(entry) {
    const active = activeConnection;
    if (!active || !active.root.isConnected || active.seen.has(entry.entry_id)) return;
    active.seen.add(entry.entry_id);
    active.entries.push(entry);
    active.size += entry.text.length;
    while (active.entries.length > 300 || active.size > 65536) {
        const removed = active.entries.shift();
        active.seen.delete(removed.entry_id);
        active.size -= removed.text.length;
    }
    if (entry.stream === 'status' && !active.finished) active.status.textContent = entry.text;
    if (active.frame) return;
    active.frame = requestAnimationFrame(() => {
        active.frame = 0;
        if (!active.root.isConnected) return;
        const follow = active.output.scrollHeight - active.output.scrollTop - active.output.clientHeight < 36;
        const fragment = document.createDocumentFragment();
        for (const item of active.entries) {
            const line = el('span', `connection-log-line ${item.stream}`, item.text.replace(/\r/g, '') + (item.text.endsWith('\n') ? '' : '\n'));
            fragment.append(line);
        }
        active.output.replaceChildren(fragment);
        if (follow) active.output.scrollTop = active.output.scrollHeight;
    });
}
export function handleConnectionLog(envelope) {
    const active = activeConnection, entry = envelope.event;
    if (!active || !active.root.isConnected || active.id !== envelope.server_id || entry.attempt_id !== active.attemptId) return;
    if (typeof entry.text === 'string' && typeof entry.entry_id === 'string') appendConnectionLog(entry);
}
async function connect(id, name, root) {
    const progress = root.querySelector('#connection-progress');
    if (!progress) return;
    progress.className = 'connection-progress';
    progress.replaceChildren();
    const status = el('p', 'connection-stage', 'Connecting…');
    status.setAttribute('role', 'status');
    const output = el('pre', 'connection-log');
    output.setAttribute('aria-label', 'Connection logs');
    output.tabIndex = 0;
    const caption = el('div', 'connection-log-caption', 'Connection log · live');
    progress.append(status, caption, output);
    const attemptId = crypto.randomUUID();
    activeConnection = { id, attemptId, root, status, output, entries: [], seen: new Set(), size: 0, frame: 0 };
    root.querySelectorAll('button:not([data-close]),input,select').forEach(b => b.disabled = true);
    const attempt = async (fingerprint) => {
        try {
            const result = await api.connectServer(id, fingerprint, attemptId);
            if (!root.isConnected) { await handlers.refresh(); return; }
            for (const entry of result.logs || []) appendConnectionLog(entry);
            if (result.state === 'trust_required') {
                status.textContent = 'Confirm the server fingerprint';
                const trustView = el('div', 'connection-trust');
                trustView.append(el('p', '', 'Verify this fingerprint with the server administrator.'), el('pre', '', `${result.host}\n${result.key_type}\n${result.fingerprint}`));
                const trust = el('button', 'primary-button', 'Trust and connect'), cancel = el('button', 'quiet-button', 'Cancel');
                trust.onclick = () => { trustView.remove(); status.textContent = 'Connecting…'; attempt(result.fingerprint); };
                cancel.onclick = showServers;
                trustView.append(cancel, trust);
                progress.prepend(trustView);
                trust.focus();
                return;
            }
            if (result.state !== 'connected') throw new Error(result.error || 'The server could not be reached.');
            await handlers.refresh();
            if (!root.isConnected) return;
            activeConnection.finished = true;
            status.textContent = `Connected to ${name}`;
            status.classList.add('connected');
            caption.textContent = 'Connection log · complete';
            const actions = root.querySelector('.modal-actions');
            actions.replaceChildren();
            const open = el('button', 'primary-button', 'Open workspace');
            open.type = 'button';
            open.onclick = () => { closeModal(); handlers.chooseServer(id); };
            actions.append(open);
            open.focus();
        } catch (error) {
            if (!root.isConnected) { toast(error.message, true); return; }
            activeConnection.finished = true;
            status.className = 'connection-stage inline-error';
            status.textContent = /auth|password|key.*reject/i.test(error.message) ? 'SSH authentication failed. Check your credentials and try again.' : 'Could not connect. Check the address, setup log and connection settings.';
            caption.textContent = 'Connection log · failed';
            appendConnectionLog({ entry_id: crypto.randomUUID(), stream: 'stderr', text: error.message });
            root.querySelectorAll('button,input,select').forEach(b => b.disabled = false);
            await handlers.refresh().catch(() => {});
        }
    };
    await attempt();
}
export function showServerModal(existing = null) {
    const root = modal(existing ? 'Edit connection' : 'Connect server', `<div class="connection-tabs" role="tablist" aria-label="Authentication method"><button role="tab" data-auth="ssh_config">SSH config</button><button role="tab" data-auth="password">Manual</button><button role="tab" data-auth="key">SSH key</button></div><form id="server-form"><div id="server-fields" class="form-grid"></div><div id="connection-progress"></div><div class="modal-actions"><button type="button" class="quiet-button" id="cancel-server">Cancel</button><button class="primary-button">Connect</button></div></form>`);
    let savedId = existing?.id;
    let discoveredAliases = [];
    let aliasStatus = 'Reading ~/.ssh/config…';
    let method = existing?.auth_method === 'agent' ? 'password' : existing?.auth_method || 'ssh_config';
    const values = { name: existing?.name || '', alias: existing?.ssh_alias || '', host: existing?.host || '', port: existing?.port || 22, username: existing?.username || '', key: existing?.private_key_path || '' };
    const render = () => {
        for (const button of root.querySelectorAll('[data-auth]')) {
            button.setAttribute('aria-selected', String(button.dataset.auth === method));
            button.tabIndex = button.dataset.auth === method ? 0 : -1;
        }
        const aliases = [...new Set([...discoveredAliases, ...state.navigation.servers.map(e => e.server.ssh_alias).filter(Boolean)])];
        root.querySelector('#server-fields').innerHTML = `<label class="full">Connection name <input name="name" value="${esc(values.name)}" placeholder="Optional, e.g. gpu-box"></label>` + (method === 'ssh_config' ? ` ${aliases.length ? `<label class="full">Configured SSH host<select id="ssh-alias-picker"><option value="">Choose a configured host…</option>${aliases.map(a => `<option value="${esc(a)}">${esc(a)}</option>`).join('')}</select></label>` : ''}<label class="full">SSH alias<input name="alias" required list="saved-aliases" value="${esc(values.alias)}" placeholder="Alias from your SSH config"></label><datalist id="saved-aliases">${aliases.map(alias => `<option value="${esc(alias)}"></option>`).join('')}</datalist><p class="full">${esc(aliasStatus)}</p>` : `<label>Host<input name="host" required value="${esc(values.host)}" placeholder="server.example.com"></label><label>Port<input name="port" type="number" min="1" max="65535" value="${esc(values.port)}" required></label><label class="full">Username<input name="username" value="${esc(values.username)}" placeholder="Your SSH username"></label>` + (method === 'key' ? `<label class="full">Private key path<input name="key" value="${esc(values.key)}" required placeholder="/home/you/.ssh/id_ed25519"></label><p class="full">Path on the computer running Kvist Web.</p>` : '<label class="full">Password<input name="password" type="password" autocomplete="off" placeholder="Leave blank to use SSH agent"></label>'));
    };
    for (const button of root.querySelectorAll('[data-auth]')) {
        button.onclick = () => { for (const [key, value] of new FormData(root.querySelector('form')))
            if (key !== 'password')
                values[key] = value; method = button.dataset.auth; render(); };
        button.onkeydown = event => { if (!['ArrowLeft', 'ArrowRight'].includes(event.key))
            return; event.preventDefault(); const tabs = [...root.querySelectorAll('[data-auth]')], index = tabs.indexOf(button), next = tabs[(index + (event.key === 'ArrowRight' ? 1 : 2)) % 3]; next.click(); next.focus(); };
    }
    render();
    root.addEventListener('change', event => { if (event.target.id === 'ssh-alias-picker') { values.alias = event.target.value; root.querySelector('[name=alias]').value = values.alias; } });
    api.sshAliases().then(data => { discoveredAliases = data.aliases; aliasStatus = discoveredAliases.length ? 'Hosts from ~/.ssh/config on this computer. You can also enter an alias.' : 'No named hosts found in ~/.ssh/config. Enter an alias or choose Manual.'; }).catch(() => { aliasStatus = 'Could not read ~/.ssh/config. Enter an alias or choose Manual.'; }).finally(() => { if (!root.isConnected) return; for (const [key, value] of new FormData(root.querySelector('form'))) if (key !== 'password') values[key] = value; if (method === 'ssh_config') render(); });
    root.querySelector('#cancel-server').onclick = closeModal;
    root.querySelector('form').onsubmit = async (event) => {
        event.preventDefault();
        const form = event.currentTarget, data = new FormData(form), name = String(data.get('name') || data.get('alias') || data.get('host')).trim();
        const id = savedId || name.toLowerCase().replace(/[^a-z0-9._-]+/g, '-').replace(/^-|-$/g, '').slice(0, 52) + '-' + Math.random().toString(36).slice(2, 6);
        savedId = id;
        const payload = { ...(existing || {}), id, name, kind: 'remote', ssh_alias: String(data.get('alias') || ''), host: String(data.get('host') || ''), port: Number(data.get('port') || 22), username: String(data.get('username') || ''), auth_method: method === 'password' && !data.get('password') ? 'agent' : method, private_key_path: String(data.get('key') || ''), password: String(data.get('password') || '') };
        form.querySelectorAll('button').forEach(b => b.disabled = true);
        try {
            await api.addServer(payload);
            payload.password = '';
            const password = form.querySelector('[name="password"]');
            if (password)
                password.value = '';
            await connect(id, name, root);
        }
        catch (error) {
            root.querySelector('#connection-progress').textContent = error.message;
            form.querySelectorAll('button').forEach(b => b.disabled = false);
        }
    };
}
export function addWorkspace(serverId = (state.selected || state.workspace)?.serverId || 'local') {
    const name = serverOf(serverId)?.server.name || serverId;
    const root = modal('Add workspace', `<p>Open a directory on ${esc(name)}.</p><form id="workspace-form"><div class="form-grid"><label class="full">Absolute directory path<input name="path" required placeholder="/home/you/project" autocomplete="off"></label></div><p id="workspace-error" class="inline-error" role="alert"></p><div class="modal-actions"><button type="submit" class="primary-button">Open workspace</button></div></form>`);
    root.querySelector('form').onsubmit = async (event) => { event.preventDefault(); const button = root.querySelector('[type="submit"]'); button.disabled = true; try {
        const result = await api.addWorkspace(serverId, new FormData(event.currentTarget).get('path'));
        await handlers.refresh();
        closeModal();
        handlers.chooseWorkspace(serverId, result.workspace);
    }
    catch (error) {
        root.querySelector('#workspace-error').textContent = error.message;
        button.disabled = false;
    } };
}
export function showWorkspaces() {
    const root = modal('Switch workspace', '<div id="workspace-list"></div><div class="menu-divider"></div><button class="quiet-button" id="workspace-add">＋ Add workspace</button>');
    for (const entry of state.navigation.servers)
        for (const workspace of entry.workspaces) {
            const button = el('button', 'workspace-row'), label = el('span', '', workspace.path.split(/[\\/]/).filter(Boolean).at(-1));
            label.append(el('small', '', workspace.path));
            button.append(label, el('small', '', entry.server.name));
            button.onclick = () => { closeModal(); handlers.chooseWorkspace(entry.server.id, workspace.path); };
            root.querySelector('#workspace-list').append(button);
        }
    root.querySelector('#workspace-add').onclick = () => addWorkspace();
}
export { showModel } from '/assets/features/configuration.js';
export async function showSettings() {
    const root = modal('Settings', '<p>Configuration sync copies general preferences to a remote server. Credentials are shared only when explicitly selected.</p><div id="settings-content" role="status">Loading…</div>');
    const content = root.querySelector('#settings-content');
    try {
        const preview = await api.configSyncPreview();
        if (!content.isConnected)
            return;
        const remotes = state.navigation.servers.filter(e => e.server.kind === 'remote');
        if (!remotes.length) {
            content.textContent = 'Connect a server from the Local selector to enable configuration sync.';
            return;
        }
        content.innerHTML = `<form><label>Server <select name="server" aria-label="Server to synchronize">${remotes.map(e => `<option value="${esc(e.server.id)}">${esc(e.server.name)}</option>`).join('')}</select></label><div class="credential-list">${preview.credentials.map(c => `<label><input type="checkbox" name="credential" value="${esc(c.id)}" ${c.present ? '' : 'disabled'}>${esc(c.label)}</label>`).join('')}</div><div class="modal-actions"><button class="primary-button">Synchronize</button></div></form>`;
        content.querySelector('form').onsubmit = async (event) => { event.preventDefault(); const data = new FormData(event.currentTarget), button = content.querySelector('button'); button.disabled = true; try {
            await api.syncConfig(data.get('server'), data.getAll('credential'));
            closeModal();
            toast('Configuration synchronized.');
        }
        catch (error) {
            toast(error.message, true);
            button.disabled = false;
        } };
    }
    catch (error) {
        content.textContent = error.message;
    }
}

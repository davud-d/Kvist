import { api } from '/assets/utils/api.js';
import { state, serverOf } from '/assets/state/store.js';
import { $, esc, modal, closeModal, toast } from '/assets/utils/ui.js';

const configurations = new Map();
// A closed session takes the input out of the DOM, so the label is kept as a reference.
const modelLabel = document.querySelector('#model-label');
export const currentServer = () => (state.selected || state.workspace)?.serverId || 'local';
export const configurationOf = (serverId = currentServer()) => configurations.get(serverId);
// The status chips redraw from the configuration whenever it is loaded or saved.
const changed = info => { document.dispatchEvent(new CustomEvent('kvist:configuration')); return info; };
// Defaults for new sessions, saved by the core's own writers; credentials are left as they are.
export async function saveDefaults(change, serverId = currentServer()) {
    const info = configurations.get(serverId), p = [...info.providers, ...info.custom_providers].find(item => item.id === info.provider);
    const saved = await api.saveConfiguration(serverId, { provider: info.provider, base_url: '', models: { ...p.defaults }, effort: info.effort, role: info.role, credentials: {}, ...change });
    configurations.set(serverId, saved);
    return changed(saved);
}
export function configuredModel() {
    const info = configurations.get(currentServer());
    if (!info || info.available === false) return 'Choose model';
    if (info.setup_required) return 'Connect provider';
    return [...info.providers, ...info.custom_providers].find(p => p.id === info.provider)?.defaults.fast || 'Choose model';
}
export async function refreshConfiguration(serverId = currentServer(), offer = false) {
    const info = await api.configuration(serverId);
    configurations.set(serverId, info);
    if (serverId === currentServer()) {
        if (!state.model) modelLabel.textContent = configuredModel();
        if (offer && info.setup_required) await showModel(true, info, serverId);
    }
    return changed(info);
}
export async function ensureConfigured() {
    const serverId = currentServer();
    const info = await refreshConfiguration(serverId);
    if (!info.setup_required) return true;
    await showModel(true, info, serverId);
    return false;
}
export async function showModel(firstRun = false, cached = null, serverId = currentServer()) {
    // onclick passes a MouseEvent; only an explicit boolean means onboarding.
    firstRun = firstRun === true;
    const serverName = serverOf(serverId)?.server.name || serverId;
    const selection = state.selected?.serverId === serverId ? { ...state.selected } : null;
    const activeProvider = selection ? state.hello?.provider : null;
    const root = modal(firstRun ? 'Connect a model provider' : 'Model & provider', '<p role="status">Loading Python core configuration…</p>');
    try {
        const info = cached || await api.configuration(serverId);
        if (!root.isConnected) return;
        if (info.available === false) throw new Error(info.reason);
        configurations.set(serverId, info);
        const providers = [...info.providers, ...info.custom_providers];
        const options = providers.map(p => `<option value="${esc(p.id)}">${esc(p.label)}</option>`).join('');
        root.querySelector('p').outerHTML = `<p>Configure ${esc(serverName === 'local' ? 'this computer' : serverName)}. Keys stay in its Kvist configuration.</p>
        <form id="provider-form"><div class="form-grid">
        <label class="full">Provider<select name="provider">${options}</select></label>
        <div id="registration" class="full" hidden><p>Start with AnyModel: register, create an API key, then paste it below. You can also choose another provider above.</p><a id="register-anymodel" class="primary-button" target="_blank" rel="noopener noreferrer">Open AnyModel registration ↗</a></div>
        <div id="provider-credentials" class="full"></div>
        <label id="model-pick-field" class="full">Model<select id="model-pick"></select></label>
        <label id="model-id-field" class="full">Model ID<input name="fast" required autocomplete="off" placeholder="Enter the model ID from your provider"></label>
        <p id="model-note" class="full" role="status" hidden></p>
        <button id="load-models" type="button" class="quiet-button full">Load available models</button>
        <datalist id="provider-models"></datalist>
        <details class="full"><summary>Advanced · models & endpoint</summary><div class="form-grid"><label id="base-url-field" class="full" hidden>Provider address<input name="base_url" type="url" autocomplete="off"></label><label class="full">Smart model<input name="smart" list="provider-models" placeholder="Same as model"></label></div></details>
        </div><p id="configuration-status" role="status"></p>
        <p class="menu-caption">New sessions use these defaults. Existing sessions keep their current provider and models.</p>
        ${info.environment_overrides.length ? `<p class="inline-error">Environment overrides take priority: ${info.environment_overrides.map(esc).join(', ')}.</p>` : ''}
        <div class="modal-actions"><button id="save-provider" class="primary-button">${firstRun ? 'Save & continue' : 'Save defaults'}</button></div></form>`;
        const form = root.querySelector('form'), provider = form.elements.provider, status = $('#configuration-status');
        provider.value = activeProvider && providers.some(p => p.id === activeProvider) ? activeProvider : info.provider;
        const registration = root.querySelector('#register-anymodel');
        // This URL comes from the core's onboarding constant, not provider metadata.
        const url = new URL(info.registration_url);
        if (url.protocol === 'https:') registration.href = url.href;
        let catalogGeneration = 0;
        const selectedProvider = () => providers.find(p => p.id === provider.value);
        const pick = root.querySelector('#model-pick'), note = root.querySelector('#model-note'), idField = root.querySelector('#model-id-field');
        const builtIn = () => (selectedProvider().options || []).map(m => typeof m === 'string' ? { value: m } : m);
        // Like the terminal's /models picker: the live catalog when it loads, else the provider's
        // built-in options; typing an ID is the fallback when neither gives a list.
        const showModels = (models, text = '') => {
            const value = form.elements.fast.value;
            pick.innerHTML = models.map(m => `<option value="${esc(m.value)}">${esc(m.label || m.value)}</option>`).join('') + '<option value="">Other model ID…</option>';
            pick.value = models.some(m => m.value === value) ? value : '';
            root.querySelector('#model-pick-field').hidden = !models.length;
            idField.hidden = pick.value !== '';
            root.querySelector('#provider-models').innerHTML = models.map(m => `<option value="${esc(m.value)}"></option>`).join('');
            note.textContent = text;
            note.hidden = !text;
        };
        pick.onchange = () => {
            form.elements.fast.value = pick.value;
            idField.hidden = pick.value !== '';
            if (!pick.value) form.elements.fast.focus();
        };
        const loadModels = async () => {
            const generation = ++catalogGeneration, button = root.querySelector('#load-models');
            button.disabled = true;
            note.textContent = 'Loading models from the provider…';
            note.hidden = false;
            try {
                const { models } = await api.modelCatalog(serverId, payload());
                if (root.isConnected && generation === catalogGeneration) showModels(models.map(value => ({ value })), models.length ? `${models.length} models available.` : 'The provider returned no model list. Enter the model ID.');
            } catch (error) { if (root.isConnected && generation === catalogGeneration) showModels(builtIn(), `${error.message} You can also enter the model ID.`); }
            finally { button.disabled = false; }
        };
        const renderProvider = () => {
            catalogGeneration++;
            const p = selectedProvider();
            status.textContent = '';
            root.querySelector('#registration').hidden = p.id !== 'anymodel';
            root.querySelector('#provider-credentials').innerHTML = p.credential_fields.map(f => `<label class="credential-field">${esc(f.label)}<input type="password" data-credential="${esc(f.id)}" autocomplete="new-password" placeholder="${f.present ? 'Saved · leave blank to keep' : 'Paste API key'}"></label>`).join('');
            root.querySelector('#base-url-field').hidden = !p.can_edit_base_url;
            form.elements.base_url.value = p.can_edit_base_url ? p.base_url || '' : '';
            form.elements.base_url.placeholder = p.base_url_placeholder || '';
            const models = activeProvider === p.id && state.hello?.models ? state.hello.models : p.defaults;
            form.elements.fast.value = models.fast || '';
            form.elements.smart.value = models.smart === models.fast ? '' : models.smart || '';
            showModels(builtIn());
            if (p.credential_fields.length && p.credential_fields.every(f => f.present)) loadModels();
        };
        // Reasoning and role are the status chips' to change; the dialog keeps the saved ones.
        const payload = () => ({ provider: provider.value, base_url: form.elements.base_url.value, models: { fast: form.elements.fast.value.trim(), smart: form.elements.smart.value.trim() || form.elements.fast.value.trim() }, effort: configurations.get(serverId).effort, credentials: Object.fromEntries([...form.querySelectorAll('[data-credential]')].filter(n => n.value.trim()).map(n => [n.dataset.credential, n.value.trim()])) });
        provider.onchange = renderProvider;
        renderProvider();
        queueMicrotask(() => { if (root.isConnected) (firstRun ? root.querySelector('[data-credential]') : idField.hidden ? pick : form.elements.fast)?.focus(); });
        root.querySelector('#load-models').onclick = loadModels;
        root.querySelector('#provider-credentials').onchange = event => { if (event.target.value.trim()) loadModels(); };
        form.onsubmit = async event => {
            event.preventDefault();
            const buttons = [...form.querySelectorAll('button')];
            buttons.forEach(b => b.disabled = true);
            status.className = '';
            status.textContent = 'Checking credentials and saving…';
            try {
                const saved = await api.saveConfiguration(serverId, payload());
                configurations.set(serverId, saved);
                changed(saved);
                form.querySelectorAll('[data-credential]').forEach(n => n.value = '');
                if (root.isConnected) closeModal();
                if (!state.model && currentServer() === serverId) modelLabel.textContent = configuredModel();
                toast(firstRun ? 'Provider connected. You can send your first message.' : 'Defaults saved for new sessions.');
            } catch (error) {
                if (root.isConnected) { status.textContent = error.message; status.className = 'inline-error'; buttons.forEach(b => b.disabled = false); }
                else toast(error.message, true);
            }
        };
    } catch (error) {
        if (root.isConnected) root.querySelector('p').textContent = `Could not load configuration. ${error.message}`;
    }
}

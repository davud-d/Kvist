import { api } from '/assets/utils/api.js';
import { state, serverOf, busy } from '/assets/state/store.js';
import { $, esc, modal, closeModal, toast } from '/assets/utils/ui.js';

const configurations = new Map();
const currentServer = () => (state.selected || state.workspace)?.serverId || 'local';
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
        if (!state.model) $('#model-label').textContent = configuredModel();
        if (offer && info.setup_required) await showModel(true, info, serverId);
    }
    return info;
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
        <button id="load-models" type="button" class="quiet-button full">Load available models</button>
        <label class="full">Model<input name="fast" list="provider-models" required autocomplete="off" placeholder="Choose or enter a model ID"></label>
        <datalist id="provider-models"></datalist>
        <details class="full"><summary>Advanced · models, reasoning & endpoint</summary><div class="form-grid"><label id="base-url-field" class="full" hidden>Provider address<input name="base_url" type="url" autocomplete="off"></label><label>Smart model<input name="smart" list="provider-models" placeholder="Same as model"></label><label>Reasoning<select name="effort">${info.effort_levels.map(e => `<option value="${esc(e)}">${e === 'false' ? 'Off' : esc(e)}</option>`).join('')}</select></label></div></details>
        </div><p id="configuration-status" role="status"></p>
        <p class="menu-caption">New sessions use these defaults. Existing sessions keep their current provider and models.</p>
        ${info.environment_overrides.length ? `<p class="inline-error">Environment overrides take priority: ${info.environment_overrides.map(esc).join(', ')}.</p>` : ''}
        <div class="modal-actions"><button id="use-session-effort" type="button" class="quiet-button" hidden>Apply reasoning</button><button id="use-session-model" type="button" class="quiet-button" hidden>Use model in this session</button><button id="save-provider" class="primary-button">${firstRun ? 'Save & continue' : 'Save defaults'}</button></div></form>`;
        const form = root.querySelector('form'), provider = form.elements.provider, status = $('#configuration-status');
        provider.value = activeProvider && providers.some(p => p.id === activeProvider) ? activeProvider : info.provider;
        form.elements.effort.value = info.effort;
        const registration = root.querySelector('#register-anymodel');
        // This URL comes from the core's onboarding constant, not provider metadata.
        const url = new URL(info.registration_url);
        if (url.protocol === 'https:') registration.href = url.href;
        let catalogGeneration = 0;
        const selectedProvider = () => providers.find(p => p.id === provider.value);
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
            root.querySelector('#provider-models').innerHTML = (p.options || []).map(m => `<option value="${esc(typeof m === 'string' ? m : m.value || m.id || '')}"></option>`).join('');
            const current = root.querySelector('#use-session-model');
            current.hidden = !selection || p.id !== activeProvider;
            const effortButton = root.querySelector('#use-session-effort');
            effortButton.hidden = current.hidden;
            effortButton.disabled = busy(state.execution);
            current.disabled = busy(state.execution);
            current.title = current.disabled ? 'Wait for the current task to finish' : 'Uses the core’s model command; also saves model defaults';
        };
        const payload = () => ({ provider: provider.value, base_url: form.elements.base_url.value, models: { fast: form.elements.fast.value.trim(), smart: form.elements.smart.value.trim() || form.elements.fast.value.trim() }, effort: form.elements.effort.value, credentials: Object.fromEntries([...form.querySelectorAll('[data-credential]')].filter(n => n.value.trim()).map(n => [n.dataset.credential, n.value.trim()])) });
        provider.onchange = renderProvider;
        renderProvider();
        queueMicrotask(() => { if (root.isConnected) (firstRun ? root.querySelector('[data-credential]') : form.elements.fast)?.focus(); });
        root.querySelector('#load-models').onclick = async () => {
            const generation = ++catalogGeneration, button = root.querySelector('#load-models');
            button.disabled = true;
            status.className = '';
            status.textContent = 'Loading models from the provider…';
            try {
                const result = await api.modelCatalog(serverId, payload());
                if (!root.isConnected || generation !== catalogGeneration) return;
                root.querySelector('#provider-models').innerHTML = result.models.map(m => `<option value="${esc(m)}"></option>`).join('');
                status.textContent = result.models.length ? `${result.models.length} models available. Choose a model below.` : 'No catalog returned. Enter the model ID from your provider.';
                form.elements.fast.focus();
            } catch (error) { if (root.isConnected && generation === catalogGeneration) status.textContent = error.message; }
            finally { button.disabled = false; }
        };
        form.onsubmit = async event => {
            event.preventDefault();
            const buttons = [...form.querySelectorAll('button')];
            buttons.forEach(b => b.disabled = true);
            status.className = '';
            status.textContent = 'Checking credentials and saving…';
            try {
                const saved = await api.saveConfiguration(serverId, payload());
                configurations.set(serverId, saved);
                form.querySelectorAll('[data-credential]').forEach(n => n.value = '');
                if (root.isConnected) closeModal();
                if (!state.model && currentServer() === serverId) $('#model-label').textContent = configuredModel();
                toast(firstRun ? 'Provider connected. You can send your first message.' : 'Defaults saved for new sessions.');
            } catch (error) {
                if (root.isConnected) { status.textContent = error.message; status.className = 'inline-error'; buttons.forEach(b => b.disabled = false); }
                else toast(error.message, true);
            }
        };
        root.querySelector('#use-session-effort').onclick = async () => {
            const button = root.querySelector('#use-session-effort'); button.disabled = true;
            try { await api.command(selection, 'effort', [form.elements.effort.value]); if (root.isConnected) closeModal(); }
            catch (error) { if (root.isConnected) { status.textContent = error.message; button.disabled = false; } }
        };
        root.querySelector('#use-session-model').onclick = async () => {
            if (!form.reportValidity()) return;
            const data = payload(), button = root.querySelector('#use-session-model');
            button.disabled = true;
            try {
                await api.command(selection, 'models', info.model_roles.map(role => data.models[role]));
                if (root.isConnected) closeModal();
                toast('Changing model… The core will confirm the result.');
            } catch (error) { if (root.isConnected) { status.textContent = error.message; button.disabled = false; } }
        };
    } catch (error) {
        if (root.isConnected) root.querySelector('p').textContent = `Could not load configuration. ${error.message}`;
    }
}

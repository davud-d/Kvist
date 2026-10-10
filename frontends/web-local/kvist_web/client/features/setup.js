// The first run: while no provider works on a server, this screen takes the main column. AnyModel is the main
// path (account in a new tab, paste the key, it is checked at once, a model is offered, Start); another provider
// is the second. The key lives only in its password field and in the requests to this server: it is never in a
// URL (the field has no name, so even a native form submit could not carry it), in storage or in the DOM after Start.
import { chatgptControls } from '/assets/features/configuration.js';
import { api } from '/assets/utils/api.js';
import { state, subscribe } from '/assets/state/store.js';
import { esc } from '/assets/utils/ui.js';

const main = document.querySelector('.main');
let section = null;
const html = path => `<h2 id="setup-title" tabindex="-1">Connect a model</h2>
<p class="setup-lede">Kvist sends your tasks to a language model. AnyModel gives you one key for many models.</p>
<p class="setup-place">Kvist will work in <span class="setup-path">${esc(path)}</span> <button type="button" class="link-button" id="setup-folder">Choose another folder</button></p>
<form class="setup-form" autocomplete="off" novalidate>
<label class="setup-field" id="setup-provider-field" hidden>Provider<select id="setup-provider"></select></label>
<div id="setup-chatgpt" hidden></div>
<ol class="setup-steps">
<li id="setup-register-step"><p class="setup-step">Get a key</p><button type="button" class="quiet-button setup-register" id="setup-register">Create an AnyModel account</button><p class="setup-note" id="setup-register-note" role="status" hidden></p></li>
<li><label class="setup-step" for="setup-key" id="setup-key-label">Paste the key</label><input id="setup-key" class="setup-key" type="password" autocomplete="off" spellcheck="false" placeholder="Paste your AnyModel key"><p class="setup-note" id="setup-key-note" role="status" hidden></p></li>
</ol>
<label class="setup-field" id="setup-base-field" hidden>Provider address<input id="setup-base" type="url" autocomplete="off"></label>
<label class="setup-field" id="setup-model-field" hidden>Model<select id="setup-model"></select></label>
<label class="setup-field" id="setup-id-field" hidden>Model ID<input id="setup-id" autocomplete="off" placeholder="Enter the model ID from your provider"></label>
<div class="setup-actions"><button class="primary-button" id="setup-start" disabled>Start</button><button type="button" class="link-button" id="setup-switch">I have another provider</button></div>
</form>`;
// tone: 'ok', 'error', 'busy' or none; a link [text, href] may follow the text.
function note(node, text, tone, link) {
    node.hidden = !text;
    node.className = `setup-note ${tone || ''}`;
    node.textContent = text || '';
    if (!link)
        return;
    const a = document.createElement('a');
    a.href = link[1];
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    a.textContent = link[0];
    node.append(a, ' yourself, then come back here.');
}
export function hideSetup() {
    section?.remove();
    section = null;
    main.classList.remove('setup-open');
}
export function setupOpen() {
    return Boolean(section);
}
// done(saved) runs after the core saved the provider; folder() lets the person pick another workspace first.
export function showSetup(info, serverId, { done, folder }) {
    if (section)
        return;
    section = document.createElement('section');
    section.className = 'setup';
    section.setAttribute('aria-labelledby', 'setup-title');
    section.innerHTML = html(state.workspace?.workspace || '');
    main.querySelector('.session-head').after(section);
    main.classList.add('setup-open');
    const $ = selector => section.querySelector(selector);
    // Built-in providers only: a custom one keeps its key in its own block and is added in the provider dialog.
    const providers = info.providers, options = list => list.map(p => `<option value="${esc(p.id)}">${esc(p.label)}</option>`).join('');
    const key = $('#setup-key'), keyNote = $('#setup-key-note'), select = $('#setup-model'), id = $('#setup-id'), start = $('#setup-start'), providerSelect = $('#setup-provider');
    // The core's own order (config_info.py): its main providers first, the rest of the catalog after them.
    providerSelect.innerHTML = `${options(providers.slice(1, 7))}<optgroup label="More providers">${options(providers.slice(7))}</optgroup>`;
    let anymodel = true, generation = 0, checked = false;
    const provider = () => providers.find(p => p.id === (anymodel ? 'anymodel' : providerSelect.value));
    const model = () => select.closest('label').hidden || select.value === '*' ? id.value.trim() : select.value;
    let oauthConnected = false;
    const sync = () => { start.disabled = !(provider().auth_type === 'oauth' ? oauthConnected : key.value.trim()) || !model() || (!$('#setup-base-field').hidden && !$('#setup-base').value.trim()); };
    const unsubscribe = subscribe(() => { if (section) $('.setup-path').textContent = state.workspace?.workspace || ''; else unsubscribe?.(); });
    function showModels(models) {
        const p = provider(), list = models.map(m => typeof m === 'string' ? { value: m } : m);
        $('#setup-model-field').hidden = !list.length;
        const values = list.map(m => m.value), preferred = [p.defaults.fast, ...(info.recommended_models?.[p.id] || [])].find(v => values.includes(v)) || '';
        select.innerHTML = `${preferred ? '' : '<option value="" disabled>Choose a model</option>'}${list.map(m => `<option value="${esc(m.value)}">${esc(m.label || m.value)}</option>`).join('')}<option value="*">Other model ID…</option>`;
        select.value = list.length ? preferred : '*';
        $('#setup-id-field').hidden = select.value !== '*';
        sync();
    }
    async function check() {
        const value = key.value.trim(), p = provider();
        if (!value)
            return;
        const mine = ++generation;
        key.removeAttribute('aria-invalid');
        note(keyNote, 'Checking the key…', 'busy');
        start.disabled = true;
        try {
            const { models } = await api.modelCatalog(serverId, { provider: p.id, base_url: $('#setup-base').value, models: p.defaults, effort: info.effort, credentials: { [p.credential_fields[0]?.id]: value } });
            if (mine !== generation)
                return;
            checked = true;
            note(keyNote, models.length ? `The key works. ${models.length} models available.` : `The key works. ${p.label} returned no model list, so enter the model ID.`, 'ok');
            showModels(models);
            (models.length ? (select.value ? start : select) : id).focus();
        }
        catch (error) {
            if (mine !== generation)
                return;
            note(keyNote, error.message, 'error');
            key.setAttribute('aria-invalid', 'true');
            if (!anymodel)
                showModels(p.options || []);
            sync();
        }
    }
    function render() {
        const p = provider();
        generation++;
        checked = false;
        key.value = '';
        id.value = '';
        note(keyNote, '');
        const oauth = p.auth_type === 'oauth';
        oauthConnected = p.has_credentials;
        $('#setup-chatgpt').hidden = !oauth;
        section.querySelector('.setup-steps').hidden = oauth;
        if (oauth) chatgptControls($('#setup-chatgpt'), serverId, async ready => {
            oauthConnected = ready !== false;
            if (oauthConnected) {
                try { const { models } = await api.modelCatalog(serverId, { provider: p.id, credentials: {} }); showModels(models); }
                catch (error) { note(keyNote, error.message, 'error'); }
            }
            sync();
        });
        $('#setup-provider-field').hidden = anymodel;
        $('#setup-register-step').hidden = !anymodel;
        section.querySelector('.setup-steps').classList.toggle('single', !anymodel);
        $('#setup-key-label').textContent = anymodel ? 'Paste the key' : `${p.label} API key`;
        key.placeholder = anymodel ? 'Paste your AnyModel key' : 'Paste your key';
        $('#setup-switch').textContent = anymodel ? 'I have another provider' : 'Use AnyModel instead';
        $('.setup-lede').hidden = !anymodel;
        $('#setup-base-field').hidden = anymodel || p.base_url_placeholder !== 'Required provider endpoint';
        if (anymodel)
            showModels([]), $('#setup-model-field').hidden = $('#setup-id-field').hidden = true;
        else
            showModels(p.options || []);
    }
    $('#setup-register').onclick = () => {
        // The address is the core's ANYMODEL_REGISTER_URL, handed over as registration_url.
        const url = new URL(info.registration_url), registerNote = $('#setup-register-note');
        const tab = url.protocol === 'https:' ? window.open(url.href, '_blank') : null;
        if (tab)
            tab.opener = null;
        note(registerNote, tab ? 'Registration opened in a new tab. Create a key there and paste it below.' : 'Your browser blocked the new tab. Open ', tab ? '' : 'error', tab ? null : [url.host + url.pathname, url.href]);
        key.focus();
    };
    $('#setup-folder').onclick = folder;
    $('#setup-switch').onclick = () => { anymodel = !anymodel; render(); (anymodel ? key : providerSelect).focus(); };
    providerSelect.onchange = render;
    key.addEventListener('paste', () => setTimeout(check));
    key.addEventListener('change', () => { if (!checked) check(); });
    key.addEventListener('input', () => { checked = false; sync(); });
    key.addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); check(); } });
    select.onchange = () => { $('#setup-id-field').hidden = select.value !== '*'; if (select.value === '*') id.focus(); sync(); };
    id.oninput = sync;
    $('#setup-base').oninput = sync;
    section.querySelector('form').onsubmit = async event => {
        event.preventDefault();
        if (start.disabled)
            return;
        const p = provider(), chosen = model();
        start.disabled = true;
        start.textContent = 'Saving…';
        try {
            const saved = await api.saveConfiguration(serverId, { provider: p.id, base_url: $('#setup-base').value, models: { fast: chosen, smart: chosen }, effort: info.effort, credentials: p.auth_type === 'oauth' ? {} : { [p.credential_fields[0]?.id]: key.value.trim() } });
            key.value = '';
            hideSetup();
            done(saved);
        }
        catch (error) {
            note(keyNote, error.message, 'error');
            start.textContent = 'Start';
            sync();
        }
    };
    render();
    key.focus();
}

// The status chips in the input's bottom row: model, reasoning and role, each with a menu that opens above
// the input. A session with a running core changes through the core's own command (it also saves the choice as
// the default); without one the choice is saved as the default for new sessions.
import { api } from '/assets/utils/api.js';
import { state, busy, subscribe } from '/assets/state/store.js';
import { esc, modal, closeModal, toast } from '/assets/utils/ui.js';
import { configurationOf, configuredModel, currentServer, refreshConfiguration, saveDefaults, showModel } from '/assets/features/configuration.js';

const area = document.querySelector('.composer-area');
// A closed session takes the input out of the DOM, so the chips are kept as references.
const chips = { model: document.querySelector('#model-selector'), effort: document.querySelector('#effort-chip'), role: document.querySelector('#role-chip') };
const NAMES = { model: 'Model', effort: 'Reasoning', role: 'Role' };
const menu = document.createElement('div');
menu.className = 'popover chip-menu';
menu.setAttribute('role', 'menu');
menu.hidden = true;
let openKind = null, pending = null;
const catalogs = new Map();
const label = (kind, value) => kind === 'effort' && value === 'false' ? 'off' : value;
const live = () => Boolean(state.selected && state.hello);
const providerOf = info => [...info.providers, ...info.custom_providers].find(p => p.id === (live() && state.hello.provider || info.provider));
function values() {
    const info = configurationOf(), hello = live() ? state.hello : {};
    return { model: state.model || configuredModel(), effort: hello.effort || info?.effort || '', role: hello.role || info?.role || 'auto' };
}
function render() {
    const current = values(), disabled = busy(state.execution) || !configurationOf() || configurationOf().setup_required;
    for (const [kind, chip] of Object.entries(chips)) {
        const value = pending?.kind === kind ? pending.value : current[kind];
        chip.querySelector('.chip-value').textContent = label(kind, value);
        chip.hidden = !value;
        chip.setAttribute('aria-label', `${NAMES[kind]}: ${label(kind, value)}`);
        chip.classList.toggle('pending', pending?.kind === kind);
        chip.setAttribute('aria-disabled', String(disabled));
        chip.title = busy(state.execution) ? 'Available when Kvist finishes the current task' : '';
    }
}
function close(focusChip = true) {
    menu.hidden = true;
    menu.replaceChildren();
    const chip = chips[openKind];
    openKind = null;
    if (!chip)
        return;
    chip.setAttribute('aria-expanded', 'false');
    if (focusChip)
        chip.focus();
}
function item(text, description, onPick, checked = null) {
    const node = document.createElement('button');
    node.type = 'button';
    node.className = 'menu-item';
    node.tabIndex = -1;
    node.setAttribute('role', checked === null ? 'menuitem' : 'menuitemradio');
    if (checked !== null)
        node.setAttribute('aria-checked', String(checked));
    node.title = `${text}: ${description}`;
    node.innerHTML = `<span class="menu-check" aria-hidden="true">${checked ? '✓' : ''}</span><span class="menu-label">${esc(text)}</span><span class="menu-desc">${esc(description)}</span>`;
    node.onclick = onPick;
    return node;
}
const line = (cls, text) => { const node = document.createElement('p'); node.className = cls; node.textContent = text; return node; };
async function catalog(info, p) {
    const key = `${currentServer()}\u0000${p.id}`;
    if (!catalogs.has(key))
        catalogs.set(key, api.modelCatalog(currentServer(), { provider: p.id, base_url: '', models: p.defaults, effort: info.effort, credentials: {} }).then(result => result.models.map(value => ({ value }))));
    try {
        return await catalogs.get(key);
    }
    catch (error) {
        catalogs.delete(key);
        throw error;
    }
}
function modelMenu() {
    const info = configurationOf(), p = providerOf(info), current = values().model;
    menu.append(line('menu-head', `${p.label} models. ${live() ? 'Applies to this session and new ones.' : 'Applies to new sessions.'}`));
    const list = document.createElement('div');
    menu.append(list);
    const show = (models, problem = '') => {
        list.replaceChildren();
        if (problem) {
            const notice = line('menu-state error', `${problem} `), retry = document.createElement('button');
            retry.type = 'button';
            retry.className = 'link-button';
            retry.textContent = 'Try again';
            retry.onclick = () => { list.replaceChildren(line('menu-state busy', 'Loading models…')); catalog(info, p).then(show, error => show(builtIn, error.message)); };
            notice.append(retry);
            list.append(notice);
        }
        else if (!models.length)
            list.append(line('menu-state', `${p.label} returned no models.`));
        if (models.length > 8) {
            const filter = document.createElement('input');
            filter.className = 'menu-filter';
            filter.placeholder = 'Filter models';
            filter.setAttribute('aria-label', 'Filter models');
            filter.oninput = () => { for (const node of list.querySelectorAll('[data-model]')) node.hidden = !node.dataset.model.includes(filter.value.trim().toLowerCase()); };
            list.append(filter);
        }
        for (const m of models.some(m => m.value === current) || !current ? models : [{ value: current }, ...models]) {
            const node = item(m.label || m.value, m.value === current ? 'Current model' : m.description || `From your ${p.label} catalog`, () => apply('model', m.value), m.value === current);
            node.dataset.model = m.value.toLowerCase();
            list.append(node);
        }
        focusFirst();
    };
    const builtIn = (p.options || []).map(m => typeof m === 'string' ? { value: m } : m);
    if (p.credential_fields?.length && p.credential_fields.every(f => f.present)) {
        list.append(line('menu-state busy', 'Loading models…'));
        catalog(info, p).then(models => { if (openKind === 'model') show(models); }, error => { if (openKind === 'model') show(builtIn, error.message); });
    }
    else
        show(builtIn);
    menu.append(document.createElement('hr'));
    menu.append(item('Enter a model ID…', 'Use a model that is not in this list', () => { close(false); modelIdDialog(); }));
    menu.append(item('Providers and keys…', 'Add a provider, change a key or set a model per role', () => { close(false); showModel(); }));
}
function choiceMenu(kind) {
    const info = configurationOf(), current = values()[kind];
    const scope = live() ? 'Applies now and to new sessions.' : 'Applies to new sessions.';
    menu.append(line('menu-head', kind === 'effort' ? `How much the model thinks before it acts. ${scope}` : `What Kvist may do in your workspace. ${scope}`));
    for (const entry of kind === 'effort' ? info.effort_info : info.roles)
        menu.append(item(label(kind, entry.value), entry.description, () => apply(kind, entry.value), entry.value === current));
}
function focusFirst() {
    if (menu.hidden || menu.contains(document.activeElement))
        return;
    (menu.querySelector('.menu-filter') || menu.querySelector('[aria-checked="true"]') || menu.querySelector('.menu-item'))?.focus();
}
function open(kind) {
    const chip = chips[kind];
    if (chip.getAttribute('aria-disabled') === 'true') {
        if (busy(state.execution))
            toast('Kvist is working. Change this when the task finishes.');
        return;
    }
    if (openKind === kind)
        return close();
    close(false);
    openKind = kind;
    chip.setAttribute('aria-expanded', 'true');
    menu.setAttribute('aria-label', NAMES[kind]);
    if (kind === 'model')
        modelMenu();
    else
        choiceMenu(kind);
    menu.hidden = false;
    const left = chip.getBoundingClientRect().left - area.getBoundingClientRect().left;
    menu.style.left = innerWidth > 640 ? `${Math.max(0, Math.min(left, area.clientWidth - menu.offsetWidth))}px` : '';
    focusFirst();
}
function modelIdDialog() {
    const root = modal('Enter a model ID', '<form class="form-grid" autocomplete="off"><label class="full">Model ID<input name="model" required autocomplete="off" placeholder="Enter the model ID from your provider"></label><div class="modal-actions full"><button type="submit" class="primary-button">Use this model</button></div></form>');
    const form = root.querySelector('form');
    form.onsubmit = event => { event.preventDefault(); closeModal(); apply('model', form.elements.model.value.trim()); };
    queueMicrotask(() => form.elements.model.focus());
}
function smartFor(model) {
    const models = live() ? state.hello.models || {} : providerOf(configurationOf()).defaults;
    return models.smart && models.smart !== models.fast ? models.smart : model;
}
async function apply(kind, value) {
    close();
    if (!value || value === values()[kind])
        return;
    pending = { kind, value };
    render();
    try {
        if (live()) {
            await api.command(state.selected, kind === 'model' ? 'models' : kind, kind === 'model' ? [value, smartFor(value)] : [value]);
            return; // the core answers with command_result
        }
        await saveDefaults(kind === 'model' ? { models: { fast: value, smart: smartFor(value) } } : { [kind]: value });
        toast(`${NAMES[kind]} set to ${label(kind, value)} for new sessions.`);
    }
    catch (error) {
        toast(error.message, true);
    }
    pending = null;
    render();
}
// The "/model", "/reasoning" and "/role" commands open the same menus.
export const openChip = open;
export function mountChips() {
    area.append(menu);
    for (const [kind, chip] of Object.entries(chips)) {
        chip.onclick = () => open(kind);
        chip.addEventListener('keydown', event => { if (event.key === 'ArrowUp' || event.key === 'ArrowDown') { event.preventDefault(); open(kind); } });
    }
    menu.addEventListener('keydown', event => {
        const items = [...menu.querySelectorAll('.menu-item:not([hidden])')], at = items.indexOf(document.activeElement);
        if (event.target.classList.contains('menu-filter') && !['ArrowDown', 'Escape', 'Tab'].includes(event.key))
            return;
        const go = index => { event.preventDefault(); items[(index + items.length) % items.length]?.focus(); };
        if (event.key === 'ArrowDown')
            go(at + 1);
        else if (event.key === 'ArrowUp')
            go(at - 1);
        else if (event.key === 'Home')
            go(0);
        else if (event.key === 'End')
            go(items.length - 1);
        else if (event.key === 'Escape') {
            event.preventDefault();
            event.stopPropagation();
            close();
        }
        else if (event.key === 'Tab')
            close(false);
    });
    document.addEventListener('mousedown', event => { if (openKind && !event.target.closest('.chip-menu, .chip')) close(false); });
    document.addEventListener('kvist:configuration', render);
    document.addEventListener('kvist:command-result', event => {
        if (!pending)
            return;
        pending = null;
        render();
        if (event.detail.ok)
            refreshConfiguration(currentServer()).catch(() => { });
    });
    subscribe(render);
    render();
}

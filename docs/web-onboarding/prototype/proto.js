// Onboarding prototype. Static: no server, no network, no storage. Data comes from fixture.js (generated
// from the core by make_fixture.py); the model catalog and the key check are simulated:
//   key "qa-valid-key" works, "qa-empty-catalog-key" works with an empty catalog,
//   "qa-offline" cannot reach the provider, anything else is refused.
// ?s=<screen>&st=<state> opens a state directly (index.html lists them all); ?blocked=1 simulates a
// browser that blocks the registration tab.
'use strict';
const F = window.FIXTURE;
const q = new URLSearchParams(location.search);
const $ = sel => document.querySelector(sel);
const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
const wait = ms => new Promise(r => setTimeout(r, ms));
const PATHS = {
  plus: '<path d="M12 5v14 M5 12h14"/>', chevron: '<path d="m7 10 5 5 5-5"/>',
  sidebar: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/>',
  'arrow-up': '<path d="M12 20V4 m-6 6 6-6 6 6"/>', close: '<path d="m6 6 12 12 M6 18 18 6"/>',
  paperclip: '<path d="m21.4 11-9.2 9.2a6 6 0 0 1-8.5-8.5L13 2.5a4 4 0 0 1 5.7 5.7l-9.2 9.2a2 2 0 0 1-2.8-2.8l8.5-8.5"/>',
};
function icons(root = document) {
  for (const n of root.querySelectorAll('[data-icon]')) n.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${PATHS[n.dataset.icon] || ''}</svg>`;
}

// Sample catalog: the real one comes from /api/v1/configuration/models. The kr/ and ds-web/ ids are the
// examples the core itself gives for AnyModel (kernel/main.py:2789); the rest are ids from its built-in lists.
const ANYMODEL_CATALOG = ['kr/claude-sonnet-4.5', 'ds-web/deepseek-v4-pro', 'gpt-5.6-luna', 'gpt-5.6-terra', 'anthropic/claude-opus-5', 'deepseek-flash'];
const DEEPSEEK_CATALOG = ['deepseek-v4-pro', 'deepseek-flash'];
const provider = id => F.providers.find(p => p.id === id);
// The core's own values, as the TUI and /role, /effort show them; only "false" reads as "off".
const roleLabel = v => v;
const effortLabel = v => v === 'false' ? 'off' : v;
const state = { provider: 'anymodel', providerLabel: 'AnyModel', model: '', effort: 'medium', role: 'auto', catalog: ANYMODEL_CATALOG, messages: 0, busy: false };

/* ---------- toasts ---------- */
function toast(text, error = false) {
  const n = el('div', `toast ${error ? 'error' : ''}`), s = el('span', '', text), x = el('button', 'icon-button');
  x.dataset.icon = 'close'; x.setAttribute('aria-label', 'Dismiss'); x.onclick = () => n.remove();
  n.append(s, x); icons(n); $('#toasts').append(n);
  if (!q.has('st')) setTimeout(() => n.remove(), 6000);
}

/* ---------- screen 1: first run ---------- */
function note(node, text, tone) { node.hidden = !text; node.textContent = text || ''; if (tone) node.dataset.tone = tone; else delete node.dataset.tone; }
function fillSelect(select, models, current) {
  select.replaceChildren(...models.map(m => { const o = el('option', '', m.label || m.value); o.value = m.value; return o; }), Object.assign(el('option', '', 'Other model ID…'), { value: '' }));
  select.value = models.some(m => m.value === current) ? current : models[0]?.value ?? '';
}
async function checkKey(value, { status, onReady, label }) {
  status.hidden = false; note(status, 'Checking the key…', 'busy');
  await wait(q.get('st')?.includes('checking') ? 1e9 : 700);
  if (value === 'qa-offline') { note(status, `Couldn't reach ${label}. Check your connection, then `, 'err'); const b = el('button', '', 'try again'); b.type = 'button'; b.onclick = () => checkKey(value, { status, onReady, label }); status.append(b); return false; }
  if (value === 'qa-empty-catalog-key') { note(status, `The key works. ${label} returned no model list, so enter the model ID.`, 'ok'); onReady([]); return true; }
  if (value !== 'qa-valid-key') { note(status, `${label} did not accept this key. Copy it again from your ${label} account.`, 'err'); return false; }
  note(status, `The key works. ${state.catalog.length} models available.`, 'ok'); onReady(state.catalog.map(value => ({ value }))); return true;
}
function setupAnyModel() {
  const key = $('#anymodel-key'), status = $('#key-status'), field = $('#anymodel-model-field'), select = $('#anymodel-model'), start = $('#start');
  $('#register').onclick = () => {
    // The real address is registration_url from /api/v1/configuration, which is the core's ANYMODEL_REGISTER_URL.
    const tab = q.get('blocked') === '1' ? null : window.open(F.registration_url, '_blank', 'noopener');
    const n = $('#register-note'); n.replaceChildren(); n.hidden = false;
    if (tab === null && q.get('blocked') === '1') {
      n.dataset.tone = 'err';
      n.append('Your browser blocked the new tab. Open ', Object.assign(el('a', '', new URL(F.registration_url).host + new URL(F.registration_url).pathname), { href: F.registration_url, target: '_blank', rel: 'noopener noreferrer' }), ' yourself, then come back here.');
    } else { delete n.dataset.tone; n.textContent = `Registration opened in a new tab. Create a key there and paste it below.`; }
    key.focus();
  };
  let last = '';
  const run = async () => {
    const value = key.value.trim();
    if (!value || value === last) return; last = value;
    field.hidden = true; start.disabled = true; key.removeAttribute('aria-invalid');
    const ok = await checkKey(value, { status, label: 'AnyModel', onReady: models => {
      if (!models.length) { field.hidden = true; $('#anymodel-model-id-field')?.remove(); const f = Object.assign(el('label', 'setup-field', 'Model ID'), { id: 'anymodel-model-id-field' }); const i = Object.assign(el('input'), { id: 'anymodel-model-id', autocomplete: 'off', placeholder: 'Enter the model ID from AnyModel' }); f.append(i); status.after(f); i.oninput = () => { start.disabled = !i.value.trim(); }; i.focus(); return; }
      fillSelect(select, models, state.model || models[0].value); field.hidden = false; start.disabled = false; start.focus();
    } });
    if (!ok) { key.setAttribute('aria-invalid', 'true'); last = ''; }
  };
  key.addEventListener('paste', () => setTimeout(run, 0));
  key.addEventListener('change', run);
  key.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); run(); } });
  $('#setup-anymodel').onsubmit = async e => {
    e.preventDefault();
    const model = select.value || $('#anymodel-model-id')?.value.trim();
    if (!model) return;
    start.disabled = true; start.textContent = 'Saving…';
    await wait(500);
    // The key leaves the DOM as soon as the core has it: no value, no node, nothing in storage or the URL.
    key.value = ''; Object.assign(state, { model, provider: 'anymodel', providerLabel: 'AnyModel' });
    showSession();
  };
  $('#other-provider').onclick = () => showOther();
}
function showOther() {
  $('#setup-anymodel').hidden = true; $('#setup-other').hidden = false;
  const select = $('#other-provider-select');
  select.replaceChildren(...F.providers.filter(p => p.id !== 'anymodel').map(p => Object.assign(el('option', '', p.label), { value: p.id })));
  select.value = 'deepseek';
  bindProviderForm({ select, key: $('#other-key'), keyLabel: $('#other-key-label'), status: $('#other-key-status'), modelField: $('#other-model-field'), model: $('#other-model'), idField: $('#other-model-id-field'), id: $('#other-model-id'), submit: $('#other-start'), form: $('#setup-other'), onSaved: showSession });
  $('#back-anymodel').onclick = () => { $('#setup-other').hidden = true; $('#setup-anymodel').hidden = false; $('#anymodel-key').focus(); };
  select.focus();
}
// One provider form: screen 1 (other provider) and screen 5 (add a provider) share it.
function bindProviderForm(f) {
  const builtIn = () => (provider(f.select.value).options || []).map(m => typeof m === 'string' ? { value: m } : m);
  const show = (models, current) => {
    f.modelField.hidden = !models.length;
    if (models.length) fillSelect(f.model, models, current);
    f.idField.hidden = !!models.length && f.model.value !== '';
    f.submit.disabled = !(f.key.value.trim() && (f.model.value || f.id.value.trim()));
  };
  const render = () => { const p = provider(f.select.value); f.keyLabel.textContent = `${p.label} API key`; f.key.value = ''; note(f.status, ''); show(builtIn()); };
  f.select.onchange = render;
  f.model.onchange = () => { f.idField.hidden = f.model.value !== ''; if (!f.model.value) f.id.focus(); show2(); };
  const show2 = () => { f.submit.disabled = !(f.key.value.trim() && (f.model.value || f.id.value.trim())); };
  f.id.oninput = show2;
  f.key.oninput = show2;
  f.key.onchange = async () => {
    const value = f.key.value.trim(); if (!value) return;
    const label = provider(f.select.value).label;
    note(f.status, 'Loading models from the provider…', 'busy');
    await wait(q.get('st')?.includes('loading') ? 1e9 : 600);
    if (value === 'qa-valid-key') { note(f.status, `${DEEPSEEK_CATALOG.length} models available.`, 'ok'); show(DEEPSEEK_CATALOG.map(value => ({ value }))); }
    else if (value === 'qa-empty-catalog-key') { note(f.status, `${label} returned no model list. Enter the model ID.`, 'ok'); show([]); f.id.focus(); }
    else { note(f.status, `Couldn't load models from ${label}. Check the key, or pick a suggested model.`, 'err'); show(builtIn()); }
  };
  f.form.onsubmit = async e => {
    e.preventDefault();
    if (f.submit.disabled) return;
    f.submit.disabled = true; const text = f.submit.textContent; f.submit.textContent = 'Checking the key…';
    await wait(600);
    if (!['qa-valid-key', 'qa-empty-catalog-key'].includes(f.key.value.trim())) { note(f.status, `${provider(f.select.value).label} did not accept this key. Nothing was saved.`, 'err'); f.submit.textContent = text; f.submit.disabled = false; f.key.setAttribute('aria-invalid', 'true'); f.key.focus(); return; }
    const saved = { provider: f.select.value, providerLabel: provider(f.select.value).label, model: f.model.value || f.id.value.trim() };
    f.key.value = '';
    f.onSaved(saved);
  };
  render();
}

/* ---------- screen 2: the empty session ---------- */
function showSession(saved) {
  if (saved?.provider) Object.assign(state, saved, { catalog: DEEPSEEK_CATALOG });
  $('#setup').remove(); // the form and its key field leave the DOM
  $('#session').hidden = false; $('#composer-area').hidden = false;
  renderChips();
  if (!q.has('st')) $('#prompt').focus();
}
function sendPrompt() {
  const text = $('#prompt').value.trim(); if (!text || text.startsWith('/')) return;
  $('#empty').hidden = true;
  $('#conversation').append(el('div', 'proto-user', text), el('div', 'proto-reply', `(Kvist answers with ${state.model}.)`));
  $('#session-title').textContent = text.slice(0, 48); state.messages++;
  $('#prompt').value = ''; syncSend();
}
function syncSend() { const empty = !$('#prompt').value.trim(); $('#send-button').disabled = empty; $('#send-button').setAttribute('aria-disabled', String(empty)); }

/* ---------- screen 3: chips and their menus ---------- */
function renderChips() {
  const set = (id, value, name) => { const c = $(id); c.querySelector('.chip-value').textContent = value; c.setAttribute('aria-label', `${name}: ${value}`); if (!c.querySelector('svg')) { const s = el('span'); s.dataset.icon = 'chevron'; c.append(s); icons(c); } };
  set('#chip-model', state.model || 'Choose a model', 'Model'); set('#chip-effort', effortLabel(state.effort), 'Reasoning'); set('#chip-role', roleLabel(state.role), 'Role');
  for (const c of document.querySelectorAll('.chip')) { c.setAttribute('aria-disabled', String(state.busy)); c.title = state.busy ? 'Available when Kvist finishes the current task' : ''; }
}
let openChip = null;
function closeMenu(focusChip = true) {
  const menu = $('#chip-menu'); menu.hidden = true; menu.replaceChildren();
  if (openChip) { openChip.setAttribute('aria-expanded', 'false'); if (focusChip) openChip.focus(); }
  openChip = null;
}
function menuItem(label, desc, { checked = false, role = 'menuitemradio', onPick } = {}) {
  const b = el('button', 'menu-item'); b.type = 'button'; b.setAttribute('role', role); b.tabIndex = -1;
  if (role === 'menuitemradio') b.setAttribute('aria-checked', String(checked));
  b.append(el('span', 'menu-check', checked ? '✓' : ''), el('span', 'menu-label', label), el('span', 'menu-desc', desc));
  b.title = `${label}: ${desc}`; b.onclick = () => onPick();
  return b;
}
function moveFocus(menu, step) {
  const items = [...menu.querySelectorAll('.menu-item:not([hidden])')]; if (!items.length) return;
  const i = items.indexOf(document.activeElement);
  const next = step === 'first' ? 0 : step === 'last' ? items.length - 1 : (i + step + items.length) % items.length;
  items.forEach(n => n.classList.remove('active')); items[next].classList.add('active'); items[next].focus();
}
function openMenu(chip, build) {
  if (chip.getAttribute('aria-disabled') === 'true') { toast('Kvist is working. Change this when the task finishes.'); return; }
  if (openChip === chip) { closeMenu(); return; }
  closeMenu(false);
  const menu = $('#chip-menu'); openChip = chip; chip.setAttribute('aria-expanded', 'true');
  menu.setAttribute('aria-label', chip.getAttribute('aria-label'));
  build(menu); menu.hidden = false;
  if (innerWidth > 640) menu.style.left = `${Math.min(chip.getBoundingClientRect().left - $('#composer-area').getBoundingClientRect().left, $('#composer-area').clientWidth - menu.offsetWidth - 8)}px`; else menu.style.left = '';
  const current = menu.querySelector('[aria-checked="true"]') || menu.querySelector('.menu-filter') || menu.querySelector('.menu-item');
  current?.classList.add('active'); current?.focus();
}
function modelMenu(menu, mode = q.get('st')) {
  menu.append(el('p', 'menu-head', `${state.providerLabel} models. Applies to this session and new ones.`));
  if (mode === 'menu-model-loading') { const s = el('p', 'menu-state', 'Loading models…'); s.dataset.tone = 'busy'; menu.append(s); }
  else if (mode === 'menu-model-error') {
    const s = el('p', 'menu-state', `Couldn't load the model list from ${state.providerLabel}. `); s.dataset.tone = 'err';
    const b = el('button', '', 'Try again'); b.type = 'button'; s.append(b); menu.append(s);
    menu.append(menuItem(state.model, 'Current model', { checked: true, onPick: () => closeMenu() }));
  } else if (mode === 'menu-model-empty') {
    menu.append(el('p', 'menu-state', `${state.providerLabel} returned no models.`));
  } else {
    if (state.catalog.length > 8) { const f = Object.assign(el('input', 'menu-filter'), { placeholder: 'Filter models' }); f.setAttribute('aria-label', 'Filter models'); f.oninput = () => { for (const n of menu.querySelectorAll('.menu-item[data-model]')) n.hidden = !n.dataset.model.includes(f.value.trim().toLowerCase()); }; menu.append(f); }
    for (const id of state.catalog) { const i = menuItem(id, id === state.model ? 'Current model' : `From your ${state.providerLabel} catalog`, { checked: id === state.model, onPick: () => pickModel(id) }); i.dataset.model = id.toLowerCase(); menu.append(i); }
  }
  menu.append(el('div', 'menu-sep'));
  menu.append(menuItem('Enter a model ID…', 'Use a model that is not in this list', { role: 'menuitem', onPick: () => { closeMenu(false); enterModelId(); } }));
  menu.append(menuItem('Add a provider…', 'Connect another service with its own key', { role: 'menuitem', onPick: () => { closeMenu(false); openProviderDialog(); } }));
}
function effortMenu(menu) {
  menu.append(el('p', 'menu-head', 'How much the model thinks before it acts. Applies now and to new sessions.'));
  for (const e of F.effort_levels) menu.append(menuItem(effortLabel(e.value), e.description, { checked: e.value === state.effort, onPick: () => apply('effort', e.value) }));
}
function roleMenu(menu) {
  menu.append(el('p', 'menu-head', 'What Kvist may do in your workspace. Applies now and to new sessions.'));
  for (const r of F.roles) menu.append(menuItem(roleLabel(r.value), r.description, { checked: r.value === state.role, onPick: () => apply('role', r.value) }));
}
async function apply(key, value) {
  closeMenu();
  if (state[key] === value) return;
  const chip = $(`#chip-${key}`), before = state[key];
  state[key] = value; renderChips(); chip.classList.add('pending');
  await wait(q.get('st') === 'pending' ? 1e9 : 600);
  chip.classList.remove('pending');
  if (q.get('st') === 'switch-failed') { state[key] = before; renderChips(); toast('Could not change it: wait for the current task to finish.', true); return; }
  toast(`${key === 'effort' ? 'Reasoning' : 'Role'} set to ${key === 'effort' ? effortLabel(value) : roleLabel(value)}.`);
}
async function pickModel(id) {
  closeMenu();
  if (id === state.model) return;
  const before = state.model; state.model = id; renderChips(); $('#chip-model').classList.add('pending');
  await wait(q.get('st') === 'pending' ? 1e9 : 700);
  $('#chip-model').classList.remove('pending');
  if (q.get('st') === 'switch-failed') { state.model = before; renderChips(); toast('Could not change the model: wait for the current task to finish.', true); return; }
  toast(`Model changed to ${id}${state.messages ? ' for this session and new ones' : ''}.`);
}
function enterModelId() {
  const body = $('#modal-body'); $('#modal-title').textContent = 'Enter a model ID';
  body.innerHTML = '<form class="setup-form" autocomplete="off"><label class="setup-field">Model ID<input id="manual-model" autocomplete="off" placeholder="Enter the model ID from your provider"></label><div class="modal-actions"><button type="button" class="quiet-button" id="manual-cancel">Cancel</button><button class="primary-button" id="manual-use" disabled>Use this model</button></div></form>';
  const input = $('#manual-model'); input.oninput = () => { $('#manual-use').disabled = !input.value.trim(); };
  $('#manual-cancel').onclick = closeModal;
  body.querySelector('form').onsubmit = e => { e.preventDefault(); closeModal(); pickModel(input.value.trim()); };
  showModal(input);
}

/* ---------- screen 5: add or switch a provider ---------- */
let modalReturn = null;
function showModal(focus) { modalReturn = document.activeElement?.closest?.('.chip') ? document.activeElement : $('#chip-model'); $('#modal').hidden = false; icons($('#modal')); focus?.focus(); }
function closeModal() { $('#modal').hidden = true; $('#modal-body').replaceChildren(); (modalReturn || $('#prompt')).focus(); }
function openProviderDialog() {
  $('#modal-title').textContent = 'Add a provider';
  $('#modal-body').innerHTML = `<form class="setup-form" autocomplete="off" novalidate><label class="setup-field">Provider<select id="add-provider"></select></label><label class="setup-field"><span id="add-key-label">API key</span><input id="add-key" type="password" autocomplete="off" spellcheck="false" placeholder="Paste your key"></label><p id="add-status" class="setup-note" role="status" hidden></p><label id="add-model-field" class="setup-field">Model<select id="add-model"></select></label><label id="add-model-id-field" class="setup-field" hidden>Model ID<input id="add-model-id" autocomplete="off" placeholder="Enter the model ID from your provider"></label><div class="modal-actions"><button type="button" class="quiet-button" id="add-cancel">Cancel</button><button type="submit" class="primary-button" id="add-save" disabled>Save provider</button></div></form>`;
  const select = $('#add-provider');
  select.replaceChildren(...F.providers.filter(p => p.id !== state.provider).map(p => Object.assign(el('option', '', p.label), { value: p.id })));
  select.value = 'deepseek';
  $('#add-cancel').onclick = closeModal;
  bindProviderForm({ select, key: $('#add-key'), keyLabel: $('#add-key-label'), status: $('#add-status'), modelField: $('#add-model-field'), model: $('#add-model'), idField: $('#add-model-id-field'), id: $('#add-model-id'), submit: $('#add-save'), form: $('#modal-body form'), onSaved: saved => switchPrompt(saved) });
  showModal(select);
}
// A new provider needs a new core process: the open session keeps its provider (the core's models command
// switches models of the active provider only), so the dialog asks instead of changing it silently.
function switchPrompt(saved) {
  $('#modal-title').textContent = `${saved.providerLabel} is connected`;
  $('#modal-body').innerHTML = '';
  const p = el('p', '', state.messages ? `This session keeps ${state.providerLabel} with ${state.model}. New sessions use ${saved.providerLabel} with ${saved.model}.` : `New sessions use ${saved.providerLabel} with ${saved.model}.`);
  const actions = el('div', 'modal-actions');
  const keep = el('button', 'quiet-button', state.messages ? 'Keep this session' : 'Close'); keep.type = 'button'; keep.onclick = closeModal;
  const next = el('button', 'primary-button', `Start a session with ${saved.providerLabel}`); next.type = 'button';
  next.onclick = () => { Object.assign(state, saved, { catalog: DEEPSEEK_CATALOG, messages: 0 }); $('#conversation').replaceChildren(); $('#empty').hidden = false; $('#session-title').textContent = 'New session'; renderChips(); closeModal(); toast(`New session with ${saved.providerLabel}.`); };
  actions.append(keep, next); $('#modal-body').append(p, actions); next.focus();
}

/* ---------- screen 4: commands ---------- */
const COMMANDS = [
  { name: '/model', desc: 'Change the model for this session and new ones', run: () => openMenu($('#chip-model'), modelMenu) },
  { name: '/reasoning', desc: 'Change how much the model thinks before it acts', run: () => openMenu($('#chip-effort'), effortMenu), alias: 'effort' },
  { name: '/role', desc: 'Change what Kvist may do in your workspace', run: () => openMenu($('#chip-role'), roleMenu) },
  { name: '/new', desc: 'Start a new session; this one stays in the sidebar', run: () => toast('New session started.') },
  { name: '/provider', desc: 'Add a provider or switch to another one', run: () => openProviderDialog() },
  { name: '/diff', desc: 'Show the changes Kvist made in this session', run: () => toast('Opens Diff.') },
  { name: '/files', desc: 'Show the files Kvist read and changed', run: () => toast('Opens Files.') },
  { name: '/stop', desc: 'Stop the current task', run: () => toast('Nothing is running.') },
];
let slashIndex = 0;
function slashMatches() { const t = $('#prompt').value.slice(1).toLowerCase(); return COMMANDS.filter(c => c.name.slice(1).startsWith(t) || c.alias?.startsWith(t)); }
function renderSlash() {
  const prompt = $('#prompt'), menu = $('#slash-menu'), v = prompt.value;
  const open = /^\/\S*$/.test(v);
  menu.hidden = !open; prompt.setAttribute('aria-expanded', String(open));
  if (!open) { prompt.removeAttribute('aria-activedescendant'); return; }
  const items = slashMatches(); slashIndex = Math.min(slashIndex, Math.max(0, items.length - 1));
  menu.replaceChildren();
  if (!items.length) menu.append(el('p', 'menu-state', `No command starts with ${v}. Press Ctrl+Enter to send it as a message.`));
  items.forEach((c, i) => { const b = menuItem(c.name, c.desc, { role: 'option', onPick: () => runSlash(c) }); b.removeAttribute('aria-checked'); b.id = `slash-${i}`; b.setAttribute('aria-selected', String(i === slashIndex)); if (i === slashIndex) b.classList.add('active'); b.querySelector('.menu-check').remove(); menu.append(b); });
  if (items.length) prompt.setAttribute('aria-activedescendant', `slash-${slashIndex}`);
  const foot = el('div', 'menu-foot'); foot.append(el('span', '', '↑ ↓ to move, Enter to run, Esc to close'));
  const all = el('button', 'link-button', 'All commands, Ctrl+K'); all.type = 'button'; all.onclick = () => { prompt.value = ''; renderSlash(); openPalette(); }; foot.append(all);
  menu.append(foot);
}
function runSlash(c) { $('#prompt').value = ''; renderSlash(); syncSend(); c.run(); }

function openPalette() {
  const overlay = $('#palette'), input = $('#palette-input'), results = $('#palette-results');
  const items = [...COMMANDS.map(c => ({ label: { '/model': 'Change model', '/reasoning': 'Change reasoning', '/role': 'Change role', '/new': 'New session', '/provider': 'Add a provider', '/diff': 'Open diff', '/files': 'Open files', '/stop': 'Stop agent' }[c.name], detail: c.name, run: c.run })),
    ...['Switch session', 'Switch workspace', 'Add workspace', 'Open terminal', 'Open activity', 'Open context', 'Open usage', 'Show unread'].map(label => ({ label, detail: 'Command', run: () => toast(label) }))];
  let index = 0;
  const render = () => {
    const t = input.value.trim().toLowerCase(), f = items.filter(i => `${i.label} ${i.detail}`.toLowerCase().includes(t));
    index = Math.min(index, Math.max(0, f.length - 1)); results.replaceChildren();
    f.forEach((it, i) => { const b = el('button', `palette-item ${i === index ? 'selected' : ''}`); b.append(el('span', '', it.label), el('small', '', it.detail)); b.onclick = () => { overlay.hidden = true; it.run(); }; results.append(b); });
    results._f = f;
  };
  input.value = q.get('pq') || ''; input.oninput = () => { index = 0; render(); };
  input.onkeydown = e => { const f = results._f; if (e.key === 'ArrowDown') { e.preventDefault(); index = Math.min(index + 1, f.length - 1); render(); } else if (e.key === 'ArrowUp') { e.preventDefault(); index = Math.max(0, index - 1); render(); } else if (e.key === 'Enter' && f[index]) { overlay.hidden = true; f[index].run(); } else if (e.key === 'Escape') { overlay.hidden = true; $('#prompt').focus(); } };
  overlay.hidden = false; render(); input.focus();
}

/* ---------- wiring ---------- */
function wire() {
  icons();
  setupAnyModel();
  $('#chip-model').onclick = e => openMenu(e.currentTarget, modelMenu);
  $('#chip-effort').onclick = e => openMenu(e.currentTarget, effortMenu);
  $('#chip-role').onclick = e => openMenu(e.currentTarget, roleMenu);
  $('#slash-button').onclick = () => { const p = $('#prompt'); if (!p.value) p.value = '/'; p.focus(); slashIndex = 0; renderSlash(); };
  $('#sidebar-commands').onclick = openPalette;
  $('#toggle-sidebar').onclick = () => $('#app').classList.toggle('sidebar-open');
  $('#modal-close').onclick = closeModal;
  for (const s of document.querySelectorAll('.starter')) s.onclick = () => { $('#prompt').value = s.dataset.text; syncSend(); $('#prompt').focus(); };
  const prompt = $('#prompt');
  prompt.oninput = () => { slashIndex = 0; renderSlash(); syncSend(); };
  prompt.onkeydown = e => {
    if (!$('#slash-menu').hidden) {
      const items = slashMatches();
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); slashIndex = (slashIndex + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % Math.max(1, items.length); renderSlash(); return; }
      if ((e.key === 'Enter' && !e.ctrlKey && !e.metaKey) && items[slashIndex]) { e.preventDefault(); runSlash(items[slashIndex]); return; }
      if (e.key === 'Tab' && items[slashIndex]) { e.preventDefault(); prompt.value = items[slashIndex].name; renderSlash(); return; }
      if (e.key === 'Escape') { e.preventDefault(); $('#slash-menu').hidden = true; prompt.setAttribute('aria-expanded', 'false'); return; }
    }
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); sendPrompt(); }
  };
  $('#composer').onsubmit = e => { e.preventDefault(); sendPrompt(); };
  $('#chip-menu').addEventListener('keydown', e => {
    if (e.target.classList.contains('menu-filter') && !['ArrowDown', 'Escape', 'Tab'].includes(e.key)) return;
    if (e.key === 'ArrowDown') { e.preventDefault(); moveFocus($('#chip-menu'), 1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); moveFocus($('#chip-menu'), -1); }
    else if (e.key === 'Home') { e.preventDefault(); moveFocus($('#chip-menu'), 'first'); }
    else if (e.key === 'End') { e.preventDefault(); moveFocus($('#chip-menu'), 'last'); }
    else if (e.key === 'Escape') { e.preventDefault(); closeMenu(); }
    else if (e.key === 'Tab') closeMenu(false);
  });
  for (const c of document.querySelectorAll('.chip')) c.addEventListener('keydown', e => { if (e.key === 'ArrowUp' || e.key === 'ArrowDown') { e.preventDefault(); c.click(); } });
  document.addEventListener('mousedown', e => { if (openChip && !e.target.closest('#chip-menu, .chip')) closeMenu(false); });
  document.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); openPalette(); }
    if (e.key === 'Escape' && !$('#modal').hidden) closeModal();
  });
}

/* ---------- direct states for review and screenshots ---------- */
async function enter() {
  wire();
  const s = q.get('s') || 'first-run', st = q.get('st') || '';
  const keyed = async value => { const k = $('#anymodel-key'); k.value = value; k.dispatchEvent(new Event('change')); await wait(800); };
  if (s === 'first-run') {
    $('#setup').hidden = false; $('#anymodel-key').focus();
    if (st === 'tab-opened' || st === 'tab-blocked') $('#register').click();
    if (st === 'checking') keyed('qa-valid-key');
    if (st === 'invalid') await keyed('qa-wrong-key');
    if (st === 'offline') await keyed('qa-offline');
    if (st === 'ready') await keyed('qa-valid-key');
    if (st === 'empty-catalog') await keyed('qa-empty-catalog-key');
    if (st.startsWith('other')) { showOther(); if (st === 'other-ready' || st === 'other-loading' || st === 'other-empty' || st === 'other-error') { const k = $('#other-key'); k.value = { 'other-ready': 'qa-valid-key', 'other-loading': 'qa-valid-key', 'other-empty': 'qa-empty-catalog-key', 'other-error': 'qa-wrong-key' }[st]; k.dispatchEvent(new Event('input')); k.dispatchEvent(new Event('change')); await wait(800); } }
    return;
  }
  Object.assign(state, { model: 'kr/claude-sonnet-4.5' });
  if (q.get('many') === '1') state.catalog = [...ANYMODEL_CATALOG, ...Array.from({ length: 30 }, (_, i) => `vendor/model-${i + 1}`)];
  showSession();
  if (['menu-model', 'menu-model-loading', 'menu-model-error', 'menu-model-empty', 'pending', 'switched', 'switch-failed', 'modal-add', 'modal-switch', 'busy'].includes(st) || q.get('sent') === '1') { $('#prompt').value = 'Explain how this project is organized'; sendPrompt(); }
  if (st === 'busy') { state.busy = true; renderChips(); $('#chip-model').click(); }
  if (st.startsWith('menu-model')) $('#chip-model').click();
  if (st === 'menu-effort') $('#chip-effort').click();
  if (st === 'menu-role') $('#chip-role').click();
  if (st === 'pending' || st === 'switched' || st === 'switch-failed') { $('#chip-model').click(); await wait(50); pickModel('gpt-5.6-luna'); if (st !== 'pending') await wait(900); }
  if (st === 'slash' || st === 'slash-filter' || st === 'slash-none') { const p = $('#prompt'); p.value = { slash: '/', 'slash-filter': '/r', 'slash-none': '/xyz' }[st]; p.focus(); renderSlash(); }
  if (st === 'palette') openPalette();
  if (st === 'modal-add' || st === 'modal-switch') { openProviderDialog(); if (st === 'modal-switch') { const k = $('#add-key'); k.value = 'qa-valid-key'; k.dispatchEvent(new Event('input')); k.dispatchEvent(new Event('change')); await wait(800); $('#modal-body form').requestSubmit(); await wait(800); } }
  if (st === 'drawer') $('#app').classList.add('sidebar-open');
}
enter();

import { el } from '/assets/utils/ui.js';
import { t } from './i18n.js';

// A bottom sheet over the page: a title, rows to pick from, a way back to the previous sheet and out. Text
// of every row goes in through textContent.

const root = document.getElementById('menu');

export function closeMenu() {
    root.hidden = true;
    root.replaceChildren();
}

function row({ label, hint, checked, onPick }) {
    const node = el('button', `menu-row${checked ? ' checked' : ''}`);
    node.type = 'button';
    // Only a list to choose from has the mark column; a list of commands does not.
    if (checked !== undefined) node.append(el('span', 'menu-mark', checked ? '■' : ''));
    node.append(el('span', 'menu-label', label));
    if (hint) node.append(el('span', 'menu-hint', hint));
    node.onclick = onPick;
    return node;
}

// rows: [{label, hint?, checked?, onPick}]; back: a function when there is a sheet to return to; text: a
// block of preformatted text (a diff) shown instead of rows.
export function openMenu({ title, rows = [], back = null, text = null, note = '', filter = false }) {
    const scrim = el('button', 'menu-scrim');
    scrim.type = 'button';
    scrim.ariaLabel = t('cancel');
    scrim.onclick = closeMenu;
    const head = el('header', 'menu-head');
    if (back) {
        const previous = el('button', 'btn small', '◀');
        previous.type = 'button';
        previous.ariaLabel = t('back');
        previous.onclick = back;
        head.append(previous);
    }
    head.append(el('h3', 'menu-title', title));
    const close = el('button', 'btn small', '✕');
    close.type = 'button';
    close.ariaLabel = t('cancel');
    close.onclick = closeMenu;
    head.append(close);
    const body = el('div', 'menu-body');
    if (note) body.append(el('p', 'menu-note', note));
    if (text !== null) body.append(el('pre', 'menu-text', text));
    const nodes = rows.map(row);
    if (filter) {
        const field = el('input', 'field');
        field.type = 'search';
        field.placeholder = t('filter');
        field.autocomplete = 'off';
        field.oninput = () => {
            const needle = field.value.trim().toLowerCase();
            nodes.forEach((node, index) => { node.hidden = !rows[index].label.toLowerCase().includes(needle); });
        };
        body.append(field);
    }
    body.append(...nodes);
    const sheet = el('section', 'menu-sheet');
    sheet.append(head, body);
    root.replaceChildren(scrim, sheet);
    root.hidden = false;
}

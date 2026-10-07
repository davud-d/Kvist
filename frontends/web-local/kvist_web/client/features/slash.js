// The "/" menu above the input. The focus stays in the input (a combobox): arrows move, Enter runs, Tab
// completes, Escape closes and keeps the text. The text's meaning is commands.js.
import { matchCommands, slashQuery } from '/assets/features/commands.js';
const prompt = document.querySelector('#prompt');
const menu = document.createElement('div');
menu.id = 'slash-menu';
menu.className = 'popover slash-menu';
menu.setAttribute('role', 'listbox');
menu.setAttribute('aria-label', 'Commands');
menu.hidden = true;
let index = 0, dismissed = null, unknown = null, actions = { run: () => { }, all: () => { } };
const line = (cls, text) => { const node = document.createElement(cls === 'menu-foot' ? 'div' : 'p'); node.className = cls; node.textContent = text; return node; };
function close() {
    menu.hidden = true;
    prompt.setAttribute('aria-expanded', 'false');
    prompt.removeAttribute('aria-activedescendant');
}
function render() {
    const query = slashQuery(prompt.value);
    if (unknown === null && (query === null || dismissed === prompt.value))
        return close();
    const items = unknown === null ? matchCommands(query) : [];
    index = Math.min(index, Math.max(0, items.length - 1));
    menu.replaceChildren();
    if (unknown !== null)
        menu.append(line('menu-state', `Unknown command ${unknown}. Nothing was sent. Type / to see the commands.`));
    else if (!items.length)
        menu.append(line('menu-state', `No command starts with ${prompt.value}.`));
    items.forEach((command, i) => {
        const option = document.createElement('div');
        option.id = `slash-option-${i}`;
        option.className = `menu-item${i === index ? ' active' : ''}`;
        option.setAttribute('role', 'option');
        option.setAttribute('aria-selected', String(i === index));
        option.append(line('menu-label', `/${command.name}`), line('menu-desc', command.description));
        option.onmousedown = event => event.preventDefault();
        option.onclick = () => run(command);
        menu.append(option);
    });
    const foot = line('menu-foot', items.length ? '↑ ↓ to move, Enter to run, Esc to close' : 'Esc to close');
    const all = document.createElement('button');
    all.type = 'button';
    all.className = 'link-button';
    all.textContent = 'All commands, Ctrl+K';
    all.onmousedown = event => event.preventDefault();
    all.onclick = () => { close(); actions.all(); };
    foot.append(all);
    menu.append(foot);
    menu.hidden = false;
    prompt.setAttribute('aria-expanded', 'true');
    if (items.length)
        prompt.setAttribute('aria-activedescendant', `slash-option-${index}`);
    else
        prompt.removeAttribute('aria-activedescendant');
    menu.querySelector('.active')?.scrollIntoView({ block: 'nearest' });
}
function run(command) {
    prompt.value = '';
    prompt.dispatchEvent(new Event('input'));
    close();
    actions.run(command);
}
// A "/word" that is no command: the menu says so and the text stays for the person to fix.
export function showUnknown(name) {
    unknown = name;
    render();
}
export function openSlash() {
    if (!prompt.value)
        prompt.value = '/';
    dismissed = null;
    prompt.focus();
    prompt.dispatchEvent(new Event('input'));
}
export function mountSlash(host, handlers) {
    actions = handlers;
    host.prepend(menu);
    prompt.setAttribute('role', 'combobox');
    prompt.setAttribute('aria-controls', 'slash-menu');
    prompt.setAttribute('aria-autocomplete', 'list');
    prompt.setAttribute('aria-expanded', 'false');
    prompt.addEventListener('input', () => { unknown = null; index = 0; render(); });
    prompt.addEventListener('blur', close);
    prompt.addEventListener('focus', render);
    prompt.addEventListener('keydown', event => {
        if (menu.hidden || event.isComposing)
            return;
        const items = unknown === null ? matchCommands(slashQuery(prompt.value) ?? '') : [];
        if (event.key === 'Escape') {
            event.preventDefault();
            event.stopPropagation();
            dismissed = prompt.value;
            unknown = null;
            close();
        }
        else if ((event.key === 'ArrowDown' || event.key === 'ArrowUp') && items.length) {
            event.preventDefault();
            index = (index + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
            render();
        }
        else if (event.key === 'Enter' && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey && items[index]) {
            event.preventDefault();
            run(items[index]);
        }
        else if (event.key === 'Tab' && !event.shiftKey && items[index]) {
            event.preventDefault();
            prompt.value = `/${items[index].name}`;
            prompt.dispatchEvent(new Event('input'));
        }
    });
}

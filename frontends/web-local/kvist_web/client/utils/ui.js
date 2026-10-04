export const $ = selector => document.querySelector(selector);
export function el(tag, cls, text) { const node = document.createElement(tag); if (cls)
    node.className = cls; if (text != null)
    node.textContent = text; return node; }
export function esc(value) { return String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char])); }
const paths = {
    search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/>',
    folder: '<path d="M3 7V5h6l2 2h10v13H3Z"/>',
    'folder-plus': '<path d="M3 7V5h6l2 2h10v13H3Z M12 10v7 M8.5 13.5h7"/>',
    plus: '<path d="M12 5v14 M5 12h14"/>', chevron: '<path d="m7 10 5 5 5-5"/>',
    inbox: '<path d="m3 14 3-9h12l3 9v6H3Z M3 14h5l2 3h4l2-3h5"/>',
    settings: '<path d="m9 3-1 3-3 1-2 4 2 2v4l4 2 3-1 3 1 4-2v-4l2-2-2-4-3-1-1-3Z"/><circle cx="12" cy="11" r="3"/>',
    sidebar: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/>',
    monitor: '<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8 M12 17v4"/>',
    more: '<circle cx="12" cy="5" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="12" cy="19" r="1"/>',
    stop: '<rect x="6" y="6" width="12" height="12" rx="1"/>',
    'arrow-up': '<path d="M12 20V4 m-6 6 6-6 6 6"/>', close: '<path d="m6 6 12 12 M6 18 18 6"/>',
};
export function icons(root = document) { for (const node of root.querySelectorAll('[data-icon]'))
    node.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[node.dataset.icon] || ''}</svg>`; }
export function toast(message, error = false) {
    const node = el('div', `toast ${error ? 'error' : ''}`), close = el('button', 'icon-button');
    close.setAttribute('aria-label', 'Dismiss notification');
    close.dataset.icon = 'close';
    close.onclick = () => node.remove();
    node.append(el('span', '', message), close);
    icons(node);
    const stack = $('#toasts');
    while (stack.children.length >= 3)
        stack.firstElementChild.remove();
    stack.append(node);
    setTimeout(() => node.remove(), error ? 9000 : 4500);
}
const focusOwners = new Map();
export function showOverlay(node) { if (node.hidden)
    focusOwners.set(node, document.activeElement); node.hidden = false; document.dispatchEvent(new Event('kvist:overlay')); queueMicrotask(() => node.querySelector('input,button,select,[tabindex="0"]')?.focus()); }
export function hideOverlay(node) { if (node.hidden)
    return; node.hidden = true; const owner = focusOwners.get(node); if (owner?.isConnected)
    owner.focus(); focusOwners.delete(node); document.dispatchEvent(new Event('kvist:overlay')); }
export function closeModal() { hideOverlay($('#modal')); $('#modal').replaceChildren(); }
export function modal(title, content, actions = '') {
    $('#toasts').querySelectorAll('.toast:not(.error)').forEach(node => node.remove());
    const root = $('#modal');
    showOverlay(root);
    root.innerHTML = `<section class="modal-card" role="dialog" aria-modal="true" aria-labelledby="modal-title"><header class="modal-head"><h2 id="modal-title">${esc(title)}</h2><button class="icon-button" data-close aria-label="Close dialog" data-icon="close"></button></header>${content}${actions}</section>`;
    root.querySelector('[data-close]').onclick = closeModal;
    icons(root);
    return root.querySelector('.modal-card');
}
export function trapFocus(event, container) {
    const nodes = [...container.querySelectorAll('button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),a[href],[tabindex="0"]')].filter(n => n.getClientRects().length);
    if (!nodes.length)
        return;
    const first = nodes[0], last = nodes.at(-1);
    if (event.shiftKey && (document.activeElement === first || !container.contains(document.activeElement))) {
        event.preventDefault();
        last.focus();
    }
    if (!event.shiftKey && (document.activeElement === last || !container.contains(document.activeElement))) {
        event.preventDefault();
        first.focus();
    }
}
export function installGlow() {
    const glow = $('#cursor-glow'), media = matchMedia('(prefers-reduced-motion: reduce)'), fine = matchMedia('(pointer: fine)');
    let frame = 0, x = 0, y = 0, tx = 0, ty = 0, initialized = false;
    const stop = () => { cancelAnimationFrame(frame); frame = 0; glow.classList.remove('active'); };
    const paint = () => {
        x += (tx - x) * .18;
        y += (ty - y) * .18;
        glow.style.transform = `translate3d(${x}px,${y}px,0)`;
        frame = Math.abs(tx - x) + Math.abs(ty - y) > .5 ? requestAnimationFrame(paint) : 0;
    };
    window.addEventListener('pointermove', event => {
        if (media.matches || !fine.matches)
            return;
        tx = event.clientX;
        ty = event.clientY;
        if (!initialized) {
            x = tx;
            y = ty;
            initialized = true;
        }
        glow.classList.add('active');
        if (!frame)
            frame = requestAnimationFrame(paint);
    }, { passive: true });
    window.addEventListener('blur', stop);
    document.addEventListener('mouseleave', stop);
    media.addEventListener('change', stop);
    document.addEventListener('visibilitychange', () => { if (document.hidden)
        stop(); });
}
// Tiny deliberately bounded Markdown renderer. Untrusted HTML never enters the DOM.
export function markdown(node, text) {
    node.replaceChildren();
    const lines = String(text || '').split('\n');
    let code = null, list = null;
    const inline = (target, value) => {
        const pattern = /(`[^`]+`|\*\*[^*]+\*\*|\[[^\]]+\]\(https?:\/\/[^\s)]+\))/g;
        let last = 0;
        for (const match of value.matchAll(pattern)) {
            target.append(document.createTextNode(value.slice(last, match.index)));
            const token = match[0];
            let child;
            if (token[0] === '`')
                child = el('code', '', token.slice(1, -1));
            else if (token.startsWith('**'))
                child = el('strong', '', token.slice(2, -2));
            else {
                const cut = token.indexOf('](');
                child = el('a', '', token.slice(1, cut));
                child.href = token.slice(cut + 2, -1);
                child.target = '_blank';
                child.rel = 'noopener noreferrer';
            }
            target.append(child);
            last = match.index + token.length;
        }
        target.append(document.createTextNode(value.slice(last)));
    };
    for (const line of lines) {
        if (line.startsWith('```')) {
            if (code)
                code = null;
            else {
                const pre = el('pre');
                code = el('code');
                pre.append(code);
                node.append(pre);
            }
            list = null;
            continue;
        }
        if (code) {
            code.append(document.createTextNode(line + '\n'));
            continue;
        }
        if (!line.trim()) {
            list = null;
            continue;
        }
        const heading = line.match(/^(#{1,4})\s+(.*)/), item = line.match(/^\s*(?:[-*]|\d+\.)\s+(.*)/);
        let row;
        if (item) {
            if (!list) {
                list = el('ul');
                node.append(list);
            }
            row = el('li');
            inline(row, item[1]);
            list.append(row);
            continue;
        }
        list = null;
        row = el(heading ? `h${Math.min(heading[1].length + 1, 5)}` : 'p');
        inline(row, heading ? heading[2] : line);
        node.append(row);
    }
}

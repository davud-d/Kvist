import { state } from '/assets/state/store.js';
import { $, el, showOverlay, hideOverlay } from '/assets/utils/ui.js';
import { TOOL_NAMES } from '/assets/features/blocks.js';
export const actionLabel = event => {
    const names = Object.hasOwn(TOOL_NAMES, event.action_type) ? TOOL_NAMES[event.action_type] : null;
    return (event.type === 'action_result' ? names?.done : names?.doing) || ({ turn_end: event.stopped_by_user ? 'Agent stopped' : 'Turn finished', input_request: 'Waiting for your input', error: 'An error occurred', agent_process_exited: 'Agent stopped' }[event.type] || 'Agent activity');
};
// The header's own buttons (Files, Diff, Terminal) mark the view that is open; the other tabs mark none of them.
function markOpen(tab) {
    state.openView = tab;
    for (const button of document.querySelectorAll('[data-view]'))
        button.setAttribute('aria-selected', String(button.dataset.view === tab));
}
export function openView(tab) {
    state.inspectorTab = tab;
    markOpen(tab);
    showOverlay($('#workspace-view'));
    for (const button of document.querySelectorAll('[data-tab]')) {
        button.setAttribute('aria-selected', String(button.dataset.tab === tab));
        button.tabIndex = button.dataset.tab === tab ? 0 : -1;
    }
    renderInspector();
    queueMicrotask(() => document.querySelector(`[data-tab="${tab}"]`)?.focus());
    document.dispatchEvent(new CustomEvent('kvist:view-open', { detail: tab }));
}
export const closeView = () => { markOpen(''); hideOverlay($('#workspace-view')); };
let renderFrame = 0;
export function scheduleInspector() { if ($('#workspace-view').hidden || renderFrame)
    return; renderFrame = requestAnimationFrame(() => { renderFrame = 0; renderInspector(); }); }
function metrics(target, rows) { for (const [label, value] of rows) {
    const row = el('div', 'metric');
    row.append(el('span', '', label), el('span', '', value));
    target.append(row);
} }
function pagedLines(target, text, diff = false) {
    const lines = String(text).split('\n'), pre = el('pre', 'terminal-view'), more = el('button', 'load-more', 'Load more lines');
    let count = 0;
    const append = () => { const fragment = document.createDocumentFragment(); for (const line of lines.slice(count, count + 1000)) {
        const cls = diff ? line.startsWith('+') ? 'add' : line.startsWith('-') ? 'remove' : line.startsWith('@@') ? 'hunk' : '' : '';
        fragment.append(el('span', `diff-line ${cls}`, line || ' '));
    } count += 1000; pre.append(fragment); more.hidden = count >= lines.length; };
    append();
    more.onclick = append;
    target.append(pre, more);
}
export function renderInspector() {
    if ($('#workspace-view').hidden)
        return;
    const target = $('#inspector-content'), oldScroll = target.scrollTop, tab = state.inspectorTab;
    target.replaceChildren();
    if (!state.selected) {
        target.append(el('p', 'view-empty', 'Select a session to see its ' + tab + '.'));
        return;
    }
    if (tab === 'activity') {
        const events = state.events.filter(e => ['step', 'action_result', 'error', 'turn_end', 'input_request'].includes(e.type)).slice(-300).reverse();
        for (const event of events) {
            const row = el('div', 'timeline-item', actionLabel(event));
            if (event.summary || event.user_message || event.message)
                row.append(el('small', '', event.summary || event.user_message || event.message));
            target.append(row);
        }
        if (!events.length)
            target.append(el('p', 'view-empty', 'Agent activity will appear here.'));
    }
    else if (tab === 'files') {
        const files = new Map();
        for (const event of state.events) {
            const args = event.action_args || {}, paths = [event.path, args.path, ...(Array.isArray(args.paths) ? args.paths : [])].filter(p => typeof p === 'string');
            for (const path of paths)
                files.set(path, event.output || files.get(path) || '');
        }
        target.append(el('p', '', 'Files touched or inspected in this session.'));
        for (const [path, output] of files) {
            const row = el('details', 'file-row');
            row.append(el('summary', '', path), el('pre', '', output || 'No file contents were included in the session output.'));
            target.append(row);
        }
        if (!files.size)
            target.append(el('p', 'view-empty', 'No files have been reported yet.'));
    }
    else if (tab === 'diff') {
        if (state.diffLoading)
            target.append(el('p', 'view-empty', 'Loading changes…'));
        else if (state.diffError) {
            target.append(el('p', 'inline-error', state.diffError));
            const retry = el('button', 'quiet-button', 'Retry');
            retry.onclick = () => document.dispatchEvent(new CustomEvent('kvist:view-open', { detail: 'diff' }));
            target.append(retry);
        }
        else
            pagedLines(target, state.diffReport || 'No changes reported for this session.', true);
    }
    else if (tab === 'terminal') {
        const results = state.events.filter(e => e.action_type === 'run_shell' || e.type === 'action_output');
        const output = results.map(e => e.output || e.action_args?.command || e.summary).filter(Boolean).join('\n\n');
        target.append(el('p', '', 'Command output, read-only'));
        if (output)
            pagedLines(target, output);
        else
            target.append(el('p', 'view-empty', 'Command output will appear here when Kvist runs a command.'));
    }
    else if (tab === 'context') {
        target.append(el('h3', '', 'Conversation context'));
        const c = state.context;
        metrics(target, [['Used', c ? c.context_tokens.toLocaleString() + ' tokens' : 'Not reported'], ['Budget', c?.context_limit ? c.context_limit.toLocaleString() + ' tokens' : 'Not reported'], ['Model', state.model || 'Not reported']]);
        target.append(el('p', 'menu-caption', 'The latest measured conversation prompt. Planning and report calls do not reset it.'));
    }
    else {
        target.append(el('h3', '', 'Session usage'));
        metrics(target, [['Input tokens', state.usage.calls ? state.usage.input.toLocaleString() : 'Not reported'], ['Output tokens', state.usage.calls ? state.usage.output.toLocaleString() : 'Not reported'], ['Model calls', state.usage.calls || 'Not reported'], ['Cost', 'Not reported by Kvist']]);
    }
    target.scrollTop = oldScroll;
}
$('#close-view').onclick = closeView;
$('#workspace-view').addEventListener('mousedown', e => { if (e.target === e.currentTarget)
    closeView(); });
for (const button of document.querySelectorAll('[data-tab]')) {
    button.onclick = () => openView(button.dataset.tab);
    button.onkeydown = event => { if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key))
        return; event.preventDefault(); const tabs = [...document.querySelectorAll('[data-tab]')], index = tabs.indexOf(button); const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length; openView(tabs[next].dataset.tab); };
}

import { api } from '/assets/utils/api.js';
import { TOOL_NAMES } from '/assets/features/blocks.js';
import { callSeconds, formatDuration, toolStatus, toolTarget } from '/assets/features/format.js';
import { el } from '/assets/utils/ui.js';
import { t } from './i18n.js';
import { stateWord } from './home.js';

// The tablet's desk: what a phone has no room for. Beside the conversation it keeps the request that waits for
// the person (a command or a diff to read at full size, not in a sheet over the composer), the other
// sessions that wait, every tool call with its whole output, and what the agent changed in the folder.
// Medium windows get the same desk as a drawer; a phone has none.

const TABS = ['needs', 'activity', 'changes'];
// A call's output can be megabytes; the tail is what the person came to read.
const OUTPUT_TAIL = 20000;
const REPORT_LINES = 800;

const sessionKey = selection => (selection ? `${selection.serverId}\n${selection.workspace}\n${selection.session}` : '');
const sameSession = (entry, selection) => Boolean(selection) && entry.server_id === selection.serverId
    && entry.workspace === selection.workspace && entry.session === selection.session;

function tail(text) {
    return text.length > OUTPUT_TAIL ? `…${text.slice(-OUTPUT_TAIL)}` : text;
}

function drawCall(row, call) {
    const status = toolStatus(call);
    const seconds = callSeconds(call);
    row.replaceChildren(
        el('span', `dot ${status}`),
        el('span', 'call-name', TOOL_NAMES[call.actionType]?.label || call.actionType),
        el('span', 'call-target', toolTarget(call).name),
        el('span', 'call-time', status === 'running' || seconds == null ? '' : formatDuration(seconds)),
    );
}

function reportView(report) {
    const box = el('div', 'diff');
    const lines = String(report).replace(/\r\n?/g, '\n').split('\n');
    for (const line of lines.slice(0, REPORT_LINES)) {
        const kind = line.startsWith('+') && !line.startsWith('+++') ? 'add' : line.startsWith('-') && !line.startsWith('---') ? 'del' : 'ctx';
        box.append(el('div', `diff-row ${kind}`, line));
    }
    if (lines.length > REPORT_LINES) box.append(el('div', 'diff-more', t('moreLines', lines.length - REPORT_LINES)));
    return box;
}

// ctx: state() -> { selection, blocks, pending, inbox }; open(selection); toast(text); request (the sheet),
// chat and composer (where the sheet lives when the desk does not hold it); toggle (the drawer's button).
export function createDesk(root, ctx) {
    let mode = 'narrow', tab = 'activity', drawerOpen = false;
    // forSession starts as null, so the first update counts as arriving at a session (or at none).
    let pickedKey = null, forSession = null, seenRequest = null;
    let shownCalls = null, shownInbox = '', changesFor = '';
    const rows = new Map();

    const labels = {};
    const tabBar = el('div', 'tabs');
    tabBar.setAttribute('role', 'tablist');
    for (const name of TABS) {
        const button = el('button', 'tab');
        button.type = 'button';
        button.setAttribute('role', 'tab');
        button.onclick = () => select(name);
        labels[name] = button;
        tabBar.append(button);
    }
    const close = el('button', 'btn small desk-close', '✕');
    close.type = 'button';
    close.ariaLabel = t('cancel');
    close.onclick = () => setDrawer(false);
    const head = el('header', 'desk-head');
    head.append(tabBar, close);

    const requestSlot = el('div', 'desk-request');
    const calm = el('p', 'desk-empty', t('nothingNeeds'));
    const others = el('div', 'desk-others');
    const needs = el('div', 'pane');
    needs.append(requestSlot, calm, others);

    const callList = el('div', 'desk-calls');
    const output = el('pre', 'desk-output');
    const activity = el('div', 'pane activity');
    activity.append(callList, output);

    const refresh = el('button', 'btn small', t('refresh'));
    refresh.type = 'button';
    const report = el('div', 'desk-report');
    const changes = el('div', 'pane');
    changes.append(refresh, report);

    const panes = { needs, activity, changes };
    root.append(head, needs, activity, changes);
    labels.activity.textContent = t('tabActivity');
    labels.changes.textContent = t('tabChanges');
    ctx.toggle.ariaLabel = ctx.toggle.title = t('openDesk');
    ctx.toggle.onclick = () => setDrawer(!drawerOpen);
    refresh.onclick = () => loadChanges();

    function paintShell() {
        root.hidden = mode === 'narrow' || (mode === 'medium' && !drawerOpen);
        labels.needs.hidden = mode !== 'wide';
        for (const name of TABS) {
            labels[name].setAttribute('aria-selected', String(name === tab));
            panes[name].hidden = name !== tab;
        }
    }

    function setDrawer(value) {
        drawerOpen = value;
        paintShell();
    }

    function select(name) {
        tab = name;
        paintShell();
        if (name === 'changes' && changesFor !== sessionKey(ctx.state().selection)) loadChanges();
    }

    async function loadChanges() {
        const { selection } = ctx.state();
        if (!selection) return;
        const key = sessionKey(selection);
        report.replaceChildren(el('p', 'desk-empty', t('loading')));
        try {
            const result = await api.changes(selection);
            // The person may have opened another session while the computer worked it out.
            if (key !== sessionKey(ctx.state().selection)) return;
            changesFor = key;
            report.replaceChildren(result.report ? reportView(result.report) : el('p', 'desk-empty', t('noDiff')));
        } catch (error) {
            report.replaceChildren(el('p', 'desk-empty', t('loadFailed')));
            ctx.toast(error.message);
        }
    }

    function paintNeeds({ selection, pending, inbox }) {
        const waiting = inbox.filter(entry => !sameSession(entry, selection));
        calm.hidden = Boolean(pending) || waiting.length > 0;
        const count = (pending ? 1 : 0) + waiting.length;
        labels.needs.textContent = count ? `${t('inbox')} ${count}` : t('inbox');
        if (count) labels.needs.dataset.alert = ''; else delete labels.needs.dataset.alert;
        const signature = waiting.map(entry => `${entry.server_id}|${entry.workspace}|${entry.session}|${entry.state}|${entry.title}`).join('\n');
        if (signature === shownInbox) return;
        shownInbox = signature;
        others.replaceChildren(...(waiting.length ? [el('h3', 'label', t('otherSessions'))] : []), ...waiting.map(entry => {
            const row = el('button', 'session');
            row.type = 'button';
            row.dataset.state = entry.state;
            row.append(el('span', `dot ${entry.state}`), el('span', 'session-title', entry.title || entry.session), el('span', 'session-state', stateWord(entry.state)));
            row.onclick = () => ctx.open({ serverId: entry.server_id, workspace: entry.workspace, session: entry.session });
            return row;
        }));
    }

    // A request to answer takes the desk's first tab by itself, once per request; the person can leave it.
    function followRequest(pending) {
        const id = pending?.requestId ?? null;
        if (id && id !== seenRequest && mode === 'wide') select('needs');
        seenRequest = id;
    }

    const callsOf = () => ctx.state().blocks.filter(block => block.kind === 'tool').flatMap(block => block.calls);

    function pick(key) {
        pickedKey = key;
        shownCalls = null;
        paintCalls(callsOf());
    }

    // A row is made once per call and redrawn only when its call object changed, as the feed does.
    function paintCalls(calls) {
        if (shownCalls && calls.length === shownCalls.length && calls.every((call, index) => call === shownCalls[index])) return;
        shownCalls = calls;
        const picked = calls.find(call => call.key === pickedKey) || calls.at(-1) || null;
        const seen = new Set();
        let next = callList.firstChild;
        // Newest first: what the agent does now is at the top.
        for (const call of [...calls].reverse()) {
            let row = rows.get(call.key);
            if (!row) {
                const node = el('button', 'desk-call');
                node.type = 'button';
                node.onclick = () => pick(call.key);
                row = { node, call: null };
                rows.set(call.key, row);
            }
            if (row.call !== call) {
                row.call = call;
                drawCall(row.node, call);
            }
            row.node.classList.toggle('current', call === picked);
            seen.add(call.key);
            if (row.node === next) next = next.nextSibling;
            else callList.insertBefore(row.node, next);
        }
        for (const [key, row] of rows) {
            if (seen.has(key)) continue;
            row.node.remove();
            rows.delete(key);
        }
        const text = picked ? picked.output || picked.summary || '' : '';
        output.textContent = picked ? (text ? tail(text) : t('noOutput')) : t('noCalls');
        output.dataset.empty = String(!text);
    }

    return {
        setMode(next) {
            mode = next;
            if (mode !== 'wide' && tab === 'needs') tab = 'activity';
            // Turned wide again with a request still waiting (the tablet was rotated): it is the first thing to show.
            if (mode === 'wide' && ctx.state().pending) tab = 'needs';
            if (mode === 'narrow') drawerOpen = false;
            // Wide: the request is the desk's first thing. Otherwise it goes back to sit above the composer.
            if (mode === 'wide') requestSlot.append(ctx.request);
            else ctx.chat.insertBefore(ctx.request, ctx.composer);
            paintShell();
        },
        update() {
            // A phone has no desk; the work waits until the window becomes wide enough (setMode, then update).
            if (mode === 'narrow') return;
            const state = ctx.state();
            const key = sessionKey(state.selection);
            if (key !== forSession) {
                // Another session: what the desk showed belongs to the last one.
                forSession = key;
                pickedKey = null;
                shownCalls = null;
                changesFor = '';
                report.replaceChildren();
                rows.clear();
                callList.replaceChildren();
                if (tab === 'changes' && mode !== 'narrow' && state.selection) loadChanges();
                // Nothing open: the desk has no calls to show, so it offers what waits for the person.
                if (!state.selection && mode === 'wide') select('needs');
            }
            followRequest(state.pending);
            paintNeeds(state);
            paintCalls(callsOf());
            paintShell();
        },
    };
}

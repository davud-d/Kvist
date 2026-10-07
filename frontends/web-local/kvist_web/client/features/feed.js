import { chevron, el, fold, markdown } from '/assets/utils/ui.js';
import { appear, canAnimate } from '/assets/utils/motion.js';
import { TOOL_NAMES } from '/assets/features/blocks.js';
import { callSeconds, formatDuration, groupStatus, isDenied, toolStatus, toolTarget } from '/assets/features/format.js';

// Draws the blocks of a reducer state into one container. A row is created once per block and redrawn only
// when its block object is not the one drawn last: the reducer replaces an object whenever it changes it, so
// an unchanged block keeps its identity. Event text (tool output, reasoning, errors) always goes in through
// textContent. What the person opened or closed is kept here, by block key, never in the reducer.
// An input block has a row only once it is answered or closed: until then the request is the pinned panel
// above the input field (pending.js). The meta state has no row.

const now = () => Date.now() / 1000;

function messageRow(block) {
    const node = el('article', `message ${block.kind === 'user' ? 'user' : ''}`);
    const label = el('div', 'message-role');
    if (block.kind === 'text') {
        const mark = el('img');
        mark.src = '/assets/kvist-mark.svg';
        mark.alt = '';
        label.append(mark);
    }
    label.append(document.createTextNode(block.kind === 'user' ? 'You' : 'Kvist'));
    node.append(label);
    if (block.plan) node.append(el('div', 'message-label', 'plan'));
    const body = el('div', 'message-body');
    node.append(body);
    return { node, body, text: null, streaming: null };
}

function drawMessage(row, block) {
    const streaming = block.streaming === true;
    if (row.text === block.text && row.streaming === streaming) return;
    // The answer is plain text while it streams and markdown once it is final.
    if (streaming) row.body.textContent = block.text;
    else markdown(row.body, block.text);
    row.text = block.text;
    row.streaming = streaming;
}

// The text of a notice block, or null when it has no row: connection.* belong to the footer, and a closed
// session is the banner in the input's place (session.js).
function noticeText(block) {
    const { omitted_turns: turns, omitted_events: events } = block.data;
    switch (block.source) {
    case 'error': return block.text || 'Kvist failed';
    case 'sandbox_notice': return block.text || null;
    case 'session_ended': return null;
    case 'journal_notice': {
        const parts = [];
        if (turns) parts.push(`${turns} earlier ${turns === 1 ? 'turn is' : 'turns are'} not shown`);
        if (events) parts.push(`${events} ${events === 1 ? 'event' : 'events'} of this turn not recorded`);
        return parts.join(', ') || 'Part of the history was left out';
    }
    default: return null;
    }
}

function noticeRow() {
    const node = el('div', 'notice');
    const text = el('span', 'notice-text');
    node.append(text);
    return { node, text, drawn: null };
}

function drawNotice(row, block) {
    row.node.classList.toggle('notice-error', block.level === 'error');
    row.text.textContent = noticeText(block);
}

// A details element whose open state the person owns once they have toggled it: click covers the mouse,
// Enter and Space on the focused summary (they fire a click on it), and nothing else changes `open`.
// A person's opening grows the content from 0fr to 1fr: the fold gets .fold-enter before the browser opens the
// details, and loses it once the details has opened and the 0fr state is computed. Opening by the feed itself
// (a failed call, a group) has no click and shows at once; closing is always at once.
function expandable(feed, key, node, summary, fold, canToggle = () => true) {
    summary.addEventListener('click', event => {
        if (!canToggle()) {
            event.preventDefault();
            return;
        }
        feed.userOpen.set(key, !node.open);
        fold.classList.toggle('fold-enter', !node.open && canAnimate());
    });
    node.addEventListener('toggle', () => {
        if (!fold.classList.contains('fold-enter')) return;
        void fold.offsetHeight;
        fold.classList.remove('fold-enter');
    });
}

// The default applies only when it changes, so an event never closes what the person opened.
function applyOpen(feed, key, row, wanted) {
    if (feed.userOpen.has(key)) {
        if (row.node.open !== feed.userOpen.get(key)) row.node.open = feed.userOpen.get(key);
        return;
    }
    if (row.defaultOpen === wanted) return;
    row.defaultOpen = wanted;
    row.node.open = wanted;
}

const SLOW_CALL_MS = 2000;

// A time is shown for a call that took 2s or more and for one that failed; a fast success has nothing to add.
function durationText(seconds, failed) {
    if (seconds === null || (!failed && Math.round(seconds * 1000) < SLOW_CALL_MS)) return '';
    return formatDuration(seconds);
}

const callName = call => TOOL_NAMES[call.actionType]?.label || call.actionType;

// A call row starts static (no chevron, not focusable, not clickable); drawCall turns it into a details
// row once the call has an output or a summary to show.
function callRow(feed, key) {
    const node = el('details', 'tool tool-running tool-static');
    const summary = el('summary');
    summary.tabIndex = -1;
    const label = el('span', 'tool-label'), target = el('span', 'tool-target');
    const dir = el('span', 'tool-dir'), file = el('span', 'tool-file'), text = el('span', 'tool-text');
    // The directory sits in a bdi so its slashes keep their place while the row cuts it from the left.
    const dirText = el('bdi');
    dir.append(dirText);
    target.append(dir, file, text);
    const status = el('span', 'tool-status'), time = el('span', 'tool-time');
    summary.append(label, target, status, time);
    const output = el('pre'), content = fold(output);
    node.append(summary, content);
    const row = { node, summary, chevron: chevron(), label, target, dir, dirText, file, text, status, time, output, key, drawn: null, defaultOpen: false, canOpen: false };
    expandable(feed, key, node, summary, content, () => row.canOpen);
    return row;
}

function drawCall(feed, row, call) {
    const state = toolStatus(call), running = state === 'running', failed = state === 'failed';
    const seconds = callSeconds(call);
    row.label.textContent = callName(call);
    // A path shows its directory cut from the left and its file whole; any other target is cut from the end.
    const { dir, name, full } = toolTarget(call);
    row.dirText.textContent = dir;
    row.file.textContent = dir ? name : '';
    row.text.textContent = dir ? '' : name;
    row.target.title = full;
    row.node.classList.toggle('tool-running', running);
    row.node.classList.toggle('tool-failed', failed);
    row.status.textContent = failed ? (Number.isInteger(call.exitCode) && call.exitCode !== 0 ? `exit ${call.exitCode}` : 'failed') : '';
    row.time.textContent = running ? '' : durationText(seconds, failed);
    const output = running ? call.output : call.output || call.summary;
    row.output.textContent = output;
    const canOpen = output !== '';
    if (canOpen !== row.canOpen) {
        row.canOpen = canOpen;
        row.node.classList.toggle('tool-static', !canOpen);
        row.summary.tabIndex = canOpen ? 0 : -1;
        if (canOpen) row.summary.insertBefore(row.chevron, row.label);
        else row.chevron.remove();
    }
    applyOpen(feed, row.key, row, failed && call.output !== '');
}

// One block of calls: a lone call is a plain row, two or more sit under a "N calls" header.
function toolBlockRow(feed, blockId) {
    const node = el('div', 'tool-block');
    return { node, blockId, calls: new Map(), group: null, drawn: null };
}

function groupParts(feed, row) {
    const node = el('details', 'tool tool-group tool-running');
    const summary = el('summary');
    const label = el('span', 'tool-label'), target = el('span', 'tool-target'), names = el('span', 'tool-text'), time = el('span', 'tool-time');
    target.append(names);
    summary.append(chevron(), label, target, time);
    const body = el('div', 'tool-group-body'), content = fold(body);
    node.append(summary, content);
    expandable(feed, row.blockId, node, summary, content);
    return { node, label, target, names, time, body, defaultOpen: false };
}

// "3 calls" or "3 calls, 1 failed" with the failed count in the error color, then the distinct action names.
function drawGroupHeader(group, calls, failed) {
    group.label.textContent = `${calls.length} calls`;
    if (failed) group.label.append(', ', el('span', 'tool-failed-count', `${failed} failed`));
    const names = Array.from(new Set(calls.map(callName))).join(', ');
    group.names.textContent = names;
    group.target.title = names;
}

function drawToolBlock(feed, row, block) {
    if (block.calls.length > 1 && !row.group) {
        row.group = groupParts(feed, row);
        row.group.body.append(...Array.from(row.calls.values(), item => item.node));
        row.node.append(row.group.node);
    }
    const parent = row.group ? row.group.body : row.node;
    for (const call of block.calls) {
        let item = row.calls.get(call.key);
        if (!item) {
            item = callRow(feed, `${row.blockId}:${call.key}`);
            row.calls.set(call.key, item);
            parent.append(item.node);
        }
        if (item.drawn === call) continue;
        drawCall(feed, item, call);
        item.drawn = call;
    }
    if (!row.group) return;
    const group = row.group, state = groupStatus(block.calls), running = state === 'running';
    drawGroupHeader(group, block.calls, block.calls.filter(call => toolStatus(call) === 'failed').length);
    group.node.classList.toggle('tool-running', running);
    group.node.classList.toggle('tool-failed', state === 'failed');
    const starts = block.calls.map(call => call.startedAt).filter(Number.isFinite);
    const ends = block.calls.map(call => call.endedAt).filter(Number.isFinite);
    const seconds = !running && starts.length && ends.length ? callSeconds({ startedAt: Math.min(...starts), endedAt: Math.max(...ends) }) : null;
    group.time.textContent = durationText(seconds, state === 'failed');
    applyOpen(feed, row.blockId, group, !(state === 'ok' && block.turnEnded === true));
}

function reasoningRow(feed, key) {
    const node = el('details', 'reasoning');
    const summary = el('summary'), label = el('span', 'reasoning-label');
    summary.append(chevron(), label);
    const body = el('div', 'reasoning-body'), content = fold(body);
    node.append(summary, content);
    const row = { node, summary, label, body, text: null, block: null, seenAt: now(), drawn: null };
    expandable(feed, key, node, summary, content, () => row.text !== '');
    return row;
}

// "thinking 12s" while the thought streams, "thought for 12s" after. The elapsed time runs from the
// envelope ts of reasoning_start, or from the moment the row first appeared when the event had none.
function drawReasoningLabel(row) {
    const block = row.block;
    if (block.streaming) {
        const since = Number.isFinite(block.startedAt) ? block.startedAt : row.seenAt;
        row.label.textContent = `thinking ${formatDuration(Math.max(0, now() - since))}`;
        return;
    }
    row.label.textContent = block.seconds > 0 ? `thought for ${formatDuration(block.seconds)}` : 'thought';
}

function drawReasoning(row, block) {
    row.block = block;
    const text = block.text.trim() === '' ? '' : block.text;
    if (row.text !== text) {
        row.body.textContent = text;
        row.text = text;
        row.node.classList.toggle('reasoning-empty', text === '');
        // Nothing to open: the row leaves the tab order and stays closed.
        row.summary.tabIndex = text === '' ? -1 : 0;
        if (text === '') row.node.open = false;
    }
    drawReasoningLabel(row);
}

// The record of a request: "approved: run npm test" with a check or a cross, or "request closed" for a
// request that ended without an answer. The summary comes from session.js; a secret's value is not in it.
function answerRow() {
    const node = el('div', 'answer');
    const text = el('span', 'answer-text');
    node.append(text);
    return { node, text, drawn: null };
}

function drawAnswer(row, block) {
    const closed = block.answered.closed === true;
    const summary = closed ? 'request closed' : block.answered.summary || 'answered';
    row.node.classList.toggle('answer-closed', closed);
    row.node.classList.toggle('answer-denied', !closed && isDenied(summary));
    row.text.textContent = summary;
}

function messageOf(block) {
    return block.kind === 'user' || block.kind === 'text';
}

// A thought that ended in under a second with nothing to read says nothing ("thought for 0.0s").
const emptyThought = block => block.kind === 'reasoning' && !block.streaming && block.text.trim() === '' && !(block.seconds >= 1);

// The rows a state shows, in order: [key, kind, block].
function rowsOf(blocks) {
    const list = [];
    for (const block of blocks) {
        if (emptyThought(block)) continue;
        if (block.kind === 'tool' || block.kind === 'reasoning') list.push([block.id, block.kind, block]);
        else if (messageOf(block)) list.push([block.id, 'message', block]);
        else if (block.kind === 'notice' && noticeText(block) !== null) list.push([block.id, 'notice', block]);
        else if (block.kind === 'input' && block.answered !== null) list.push([block.id, 'input', block]);
    }
    return list;
}

const ROWS = {
    message: { create: (feed, key, block) => messageRow(block), draw: (feed, row, block) => drawMessage(row, block) },
    notice: { create: () => noticeRow(), draw: (feed, row, block) => drawNotice(row, block) },
    input: { create: () => answerRow(), draw: (feed, row, block) => drawAnswer(row, block) },
    tool: { create: (feed, key) => toolBlockRow(feed, key), draw: drawToolBlock },
    reasoning: { create: reasoningRow, draw: (feed, row, block) => drawReasoning(row, block) },
};

const MAX_APPEARING = 5;

export function createFeed(container) {
    const rows = new Map();
    const feed = { userOpen: new Map() };
    let timer = 0;
    // One timer per feed, alive only while a thought streams; it redraws the labels of those rows.
    const tick = () => {
        let streaming = false;
        for (const row of rows.values()) {
            if (row.block?.streaming !== true) continue;
            streaming = true;
            drawReasoningLabel(row);
        }
        if (!streaming || !container.isConnected) stop();
    };
    const stop = () => { clearInterval(timer); timer = 0; };
    return {
        // live: the blocks this render adds arrived after the first draw. They appear (a user message rises
        // 4px, the agent's blocks only fade in) unless more than MAX_APPEARING came in one frame.
        render(state, { live = false } = {}) {
            const seen = new Set(), added = [];
            let next = container.firstChild;
            let streaming = false;
            for (const [key, kind, block] of rowsOf(state.blocks)) {
                let row = rows.get(key);
                if (!row) {
                    row = ROWS[kind].create(feed, key, block);
                    rows.set(key, row);
                    added.push([row.node, block]);
                }
                seen.add(key);
                if (row.drawn !== block) {
                    ROWS[kind].draw(feed, row, block);
                    row.drawn = block;
                }
                if (kind === 'reasoning' && block.streaming) streaming = true;
                if (row.node === next) next = next.nextSibling;
                else container.insertBefore(row.node, next);
            }
            for (const [key, row] of rows) {
                if (seen.has(key)) continue;
                row.node.remove();
                rows.delete(key);
                for (const item of row.calls?.values() ?? []) feed.userOpen.delete(item.key);
                feed.userOpen.delete(key);
            }
            if (live && added.length <= MAX_APPEARING) {
                for (const [node, block] of added) appear(node, block.kind === 'user' ? { rise: 4 } : {});
            }
            if (streaming && !timer) timer = setInterval(tick, 1000);
            else if (!streaming && timer) stop();
        },
        // Called before the container is reused for another session.
        destroy: stop,
    };
}

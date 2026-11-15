import { el, markdown } from '/assets/utils/ui.js';
import { TOOL_NAMES } from '/assets/features/blocks.js';
import { callSeconds, formatDuration, isDenied, toolStatus, toolTarget } from '/assets/features/format.js';
import { t } from './i18n.js';

// Draws the blocks of the reducer into the phone's conversation. A row is made once per block and redrawn
// only when its block object changed: the reducer replaces an object whenever it changes it. What the
// person opened (a call's output, a thought) is kept here by key, never in the reducer.
// Everything that came off the wire goes in through textContent or the markdown helper, never as HTML.

const OUTPUT_TAIL = 3000;

function tail(text) {
    return text.length > OUTPUT_TAIL ? `…${text.slice(-OUTPUT_TAIL)}` : text;
}

function drawUser(feed, row, block) {
    const bubble = el('div', 'bubble', block.text);
    row.node.replaceChildren(el('div', 'who', t('you')), bubble);
    if (block.local !== 'failed') return;
    const retry = el('button', 'btn small', t('retry'));
    retry.type = 'button';
    retry.onclick = () => feed.onRetry?.(block);
    row.node.append(el('div', 'failed', t('notSent')), retry);
}

function drawText(_feed, row, block) {
    const bubble = el('div', `bubble${block.streaming ? ' streaming' : ''}`);
    markdown(bubble, block.text);
    row.node.replaceChildren(el('div', 'who', t('kvist')), bubble);
}

function drawReasoning(feed, row, block) {
    const open = feed.open.has(block.id);
    const label = block.streaming ? t('thinking') : block.seconds != null ? t('thought', formatDuration(block.seconds)) : '·';
    const head = el('button', 'thought-head', label);
    head.type = 'button';
    head.onclick = () => { feed.toggle(block.id); feed.redraw(row, block); };
    row.node.replaceChildren(head);
    if (open && block.text) row.node.append(el('pre', 'thought-text', block.text));
    row.node.dataset.streaming = String(block.streaming === true);
}

function callRow(feed, call) {
    const status = toolStatus(call);
    const name = TOOL_NAMES[call.actionType]?.label || call.actionType;
    const open = feed.open.has(call.key);
    const head = el('button', 'call-head');
    head.type = 'button';
    const seconds = callSeconds(call);
    head.append(
        el('span', `dot ${status}`),
        el('span', 'call-name', name),
        el('span', 'call-target', toolTarget(call).name),
        el('span', 'call-time', status === 'running' || seconds == null ? '' : formatDuration(seconds)),
    );
    const node = el('div', 'call');
    node.append(head);
    head.onclick = () => { feed.toggle(call.key); feed.redrawAll(); };
    if (open && (call.output || call.summary)) node.append(el('pre', 'call-out', tail(call.output || call.summary)));
    return node;
}

function drawTool(feed, row, block) {
    row.node.replaceChildren(...block.calls.map(call => callRow(feed, call)));
}

function drawInput(_feed, row, block) {
    const summary = block.answered?.summary || '';
    const denied = isDenied(summary);
    row.node.replaceChildren(el('span', `mark ${denied ? 'no' : 'yes'}`, denied ? '✕' : '✓'), el('span', 'answer-text', summary || block.title));
}

function drawNotice(_feed, row, block) {
    row.node.className = `row notice ${block.level || ''}`;
    row.node.replaceChildren(document.createTextNode(block.text));
}

const KINDS = {
    user: { tag: 'article', cls: 'msg user', draw: drawUser },
    text: { tag: 'article', cls: 'msg agent', draw: drawText },
    reasoning: { tag: 'div', cls: 'row thought', draw: drawReasoning },
    tool: { tag: 'div', cls: 'row tools', draw: drawTool },
    input: { tag: 'div', cls: 'row answer', draw: drawInput },
    notice: { tag: 'div', cls: 'row notice', draw: drawNotice },
};

// A request still waiting for the person is the sheet above the composer, so it has no row; a notice with
// no text has nothing to show.
function drawable(block) {
    if (!KINDS[block.kind]) return false;
    if (block.kind === 'input') return block.answered !== null;
    if (block.kind === 'notice') return Boolean(block.text);
    return true;
}

export function createFeed(container, { onRetry } = {}) {
    const rows = new Map();
    const feed = {
        onRetry, open: new Set(),
        toggle(key) { if (!feed.open.delete(key)) feed.open.add(key); },
        redraw(row, block) { KINDS[block.kind].draw(feed, row, block); },
        redrawAll() { for (const row of rows.values()) feed.redraw(row, row.drawn); },
    };
    return {
        render(blocks) {
            const seen = new Set();
            let next = container.firstChild;
            for (const block of blocks) {
                if (!drawable(block)) continue;
                let row = rows.get(block.id);
                if (!row) {
                    const kind = KINDS[block.kind];
                    row = { node: el(kind.tag, kind.cls), drawn: null };
                    rows.set(block.id, row);
                }
                seen.add(block.id);
                if (row.drawn !== block) {
                    row.drawn = block;
                    feed.redraw(row, block);
                }
                if (row.node === next) next = next.nextSibling;
                else container.insertBefore(row.node, next);
            }
            for (const [id, row] of rows) {
                if (seen.has(id)) continue;
                row.node.remove();
                rows.delete(id);
            }
        },
    };
}

import { el, markdown } from '/assets/utils/ui.js';
import { TOOL_NAMES } from '/assets/features/blocks.js';

// Draws the blocks of a reducer state into one container. A row is created once per block (per call for a
// tool block) and redrawn only when its block object is not the one drawn last: the reducer replaces an
// object whenever it changes it, so an unchanged block keeps its identity.
// Draws what the feed showed before the reducer: the person's messages, answers, tool calls and errors.
// Reasoning, input, notices other than errors and the meta state have no row yet.

// argv arrives as a list; a comma-joined default toString is not a command.
const TARGET_KEYS = ['path', 'command', 'query', 'pattern', 'url', 'dir', 'directory'];
function actionTarget(action, args) {
    if (action === 'git_status') return 'git status';
    if (action === 'git_diff') return `git diff ${args.path || ''}`.trim();
    for (const key of TARGET_KEYS) {
        const value = args[key];
        if (typeof value === 'string' && value) return value;
        if (Array.isArray(value) && value.length) return value.map(String).join(' ');
    }
    const paths = args.paths;
    if (Array.isArray(paths) && paths.length) return paths.map(String).join(', ');
    return '';
}

function messageRow(role, cls) {
    const node = el('article', `message ${role === 'You' ? 'user' : ''} ${cls}`);
    const label = el('div', 'message-role');
    if (role === 'Kvist') {
        const mark = el('img');
        mark.src = '/assets/kvist-mark.svg';
        mark.alt = '';
        label.append(mark);
    }
    label.append(document.createTextNode(role));
    const body = el('div', 'message-body');
    node.append(label, body);
    return { node, body, text: null, streaming: null };
}

// [role, class, text] of a block that is a message row, or null.
function messageOf(block) {
    if (block.kind === 'user') return ['You', '', block.text];
    if (block.kind === 'text') return ['Kvist', '', block.text];
    if (block.kind === 'notice' && block.source === 'error') return ['Error', 'error', block.text || 'Kvist failed'];
    return null;
}

function drawMessage(row, text, streaming) {
    if (row.text === text && row.streaming === streaming) return;
    // The answer is plain text while it streams and markdown once it is final.
    if (streaming) row.body.textContent = text;
    else markdown(row.body, text);
    row.text = text;
    row.streaming = streaming;
}

function toolRow() {
    const node = el('details', 'tool tool-running');
    const summary = el('summary');
    summary.append(el('span', 'tool-label'), el('span', 'tool-target'), el('span', 'tool-status'));
    node.append(summary, el('pre'));
    return {
        node, label: summary.children[0], target: summary.children[1], status: summary.children[2], output: node.lastChild,
    };
}

function drawTool(row, call) {
    const running = call.status === 'running', failed = call.status === 'failed';
    // The result event carries no arguments, so a finished row shows only the targets that need none.
    const target = actionTarget(call.actionType, running ? call.args : {});
    row.label.textContent = TOOL_NAMES[call.actionType]?.label || call.actionType;
    row.target.textContent = target ? `(${target})` : '';
    row.node.classList.toggle('tool-running', running);
    row.node.classList.toggle('tool-failed', failed);
    row.status.textContent = failed ? (typeof call.exitCode === 'number' && call.exitCode !== 0 ? `exit ${call.exitCode}` : 'failed') : '';
    row.output.textContent = running ? call.output : call.output || '(no output)';
}

// The rows a state shows, in order: [key, kind, drawn object].
function rowsOf(blocks) {
    const list = [];
    for (const block of blocks) {
        if (block.kind === 'tool') {
            for (const call of block.calls) list.push([`${block.id}:${call.key}`, 'tool', call]);
        } else if (messageOf(block)) list.push([block.id, 'message', block]);
    }
    return list;
}

export function createFeed(container) {
    const rows = new Map();
    return {
        render(state) {
            const seen = new Set();
            let next = container.firstChild;
            for (const [key, kind, drawn] of rowsOf(state.blocks)) {
                let row = rows.get(key);
                if (!row) {
                    row = { ...(kind === 'tool' ? toolRow() : messageRow(...messageOf(drawn))), drawn: null };
                    rows.set(key, row);
                }
                seen.add(key);
                if (row.drawn !== drawn) {
                    if (kind === 'tool') drawTool(row, drawn);
                    else drawMessage(row, messageOf(drawn)[2], drawn.streaming === true);
                    row.drawn = drawn;
                }
                if (row.node === next) next = next.nextSibling;
                else container.insertBefore(row.node, next);
            }
            for (const [key, row] of rows) {
                if (seen.has(key)) continue;
                row.node.remove();
                rows.delete(key);
            }
        },
    };
}

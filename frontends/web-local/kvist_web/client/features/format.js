// Pure text helpers for the feed rows: no DOM, no clock. Input is what blocks.js reduces.

const TARGET_LIMIT = 80;

// 0.4s under a second, 12s under a minute, then 1m 05s. Time is floored, so a row never shows time that
// has not passed yet. The input is rounded to milliseconds first: the difference of two epoch timestamps
// carries a float error of about 2e-7, which would turn 0.3 into 0.2.
export function formatDuration(seconds) {
    if (!Number.isFinite(seconds) || seconds < 0) return '';
    const ms = Math.round(seconds * 1000);
    if (ms < 1000) return `${(Math.floor(ms / 100) / 10).toFixed(1)}s`;
    const whole = Math.floor(ms / 1000);
    if (whole < 60) return `${whole}s`;
    return `${Math.floor(whole / 60)}m ${String(whole % 60).padStart(2, '0')}s`;
}

// argv arrives as a list; a comma-joined default toString is not a command.
const TARGET_KEYS = ['path', 'command', 'query', 'pattern', 'url', 'dir', 'directory'];
function rawTarget({ actionType, args = {}, path }) {
    if (actionType === 'git_status') return 'git status';
    if (actionType === 'git_diff') return `git diff ${args.path || ''}`.trim();
    for (const key of TARGET_KEYS) {
        const value = args[key];
        if (typeof value === 'string' && value) return value;
        if (Array.isArray(value) && value.length) return value.map(String).join(' ');
    }
    if (Array.isArray(args.paths) && args.paths.length) return args.paths.map(String).join(', ');
    return typeof path === 'string' ? path : '';
}

// One line of text: the first non-empty line, whitespace collapsed.
export function oneLine(text) {
    return (String(text).split(/\r?\n/).find(part => part.trim()) ?? '').replace(/\s+/g, ' ').trim();
}

// Tail cut to `limit` characters with an ellipsis; never splits a surrogate pair.
export function cutTail(text, limit = TARGET_LIMIT) {
    const chars = Array.from(text);
    return chars.length > limit ? `${chars.slice(0, limit - 1).join('')}…` : text;
}

// The target of a call as { dir, name, full }. A path (a "/" and no spaces) is split at its last "/": dir
// is the directories with their trailing "/", name the file (or the last directory), and neither is cut
// here, so the row can drop the head of the directory by width and keep the file and its parent. Anything
// else, a command or a query, has dir '' and name cut to 80 characters from the end. full is the whole line.
export function toolTarget(call) {
    const full = oneLine(rawTarget(call));
    const cut = full.slice(0, -1).lastIndexOf('/');
    if (!full.includes('/') || full.includes(' ') || cut < 0) return { dir: '', name: cutTail(full), full };
    return { dir: full.slice(0, cut + 1), name: full.slice(cut + 1), full };
}

// running, ok or failed. A result with ok:false or a non-zero exit code is failed whatever status says.
export function toolStatus(call) {
    if (call.status === 'running') return 'running';
    const failedExit = Number.isInteger(call.exitCode) && call.exitCode !== 0;
    return call.status === 'failed' || call.ok === false || failedExit ? 'failed' : 'ok';
}

// One status for a group of calls: any running call keeps it running, else any failure fails it.
export function groupStatus(calls) {
    const statuses = calls.map(toolStatus);
    if (statuses.includes('running')) return 'running';
    return statuses.includes('failed') ? 'failed' : 'ok';
}

// Seconds between the step and its result, or null when either timestamp is missing or out of order.
export function callSeconds({ startedAt, endedAt }) {
    const valid = value => Number.isFinite(value) && value > 0;
    return valid(startedAt) && valid(endedAt) && endedAt >= startedAt ? endedAt - startedAt : null;
}

// The options of a choice request as { value, label } strings; an option is a bare value or an object.
export function choiceOptions(request) {
    if (!Array.isArray(request.options)) return [];
    return request.options.map(option => {
        const value = typeof option === 'object' && option ? option.value : option;
        const label = typeof option === 'object' && option ? option.label || option.value : option;
        return { value: String(value ?? ''), label: String(label ?? value ?? '') };
    });
}

// What a command or an overwrite asks for, in the words of a sentence: "run npm test", "overwrite src/a.ts".
function askedFor(request) {
    const subject = request.subject;
    if (subject?.kind === 'command' && subject.command) return `run ${oneLine(subject.command)}`;
    if (subject?.kind === 'overwrite' && subject.path) return `overwrite ${oneLine(subject.path)}`;
    return oneLine(request.title || '') || 'request';
}

// One line for the row that replaces an answered request in the feed: "approved: run npm test",
// "denied: overwrite src/a.ts", "Pick a model: fast". A secret's value never enters it.
export function answerSummary(request, response) {
    if (request.kind === 'secret') return 'secret provided';
    if (typeof response.approved === 'boolean') return cutTail(`${response.approved ? 'approved' : 'denied'}: ${askedFor(request)}`);
    const value = String(response.value ?? '');
    const shown = choiceOptions(request).find(option => option.value === value)?.label ?? value;
    return cutTail(`${oneLine(request.title || '') || 'answered'}: ${oneLine(shown) || '(empty)'}`);
}

// A denied answer gets a cross instead of a check.
export const isDenied = summary => summary.startsWith('denied:');

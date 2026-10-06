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

// The path or the first line of the command, on one line and at most 80 characters.
export function toolTarget(call) {
    const line = rawTarget(call).split(/\r?\n/).find(part => part.trim()) ?? '';
    const text = line.replace(/\s+/g, ' ').trim();
    const chars = Array.from(text);
    return chars.length > TARGET_LIMIT ? `${chars.slice(0, TARGET_LIMIT - 1).join('')}…` : text;
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

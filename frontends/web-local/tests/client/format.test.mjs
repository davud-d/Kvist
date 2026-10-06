import test from 'node:test';
import assert from 'node:assert/strict';
import { callSeconds, formatDuration, groupStatus, toolStatus, toolTarget } from '../../kvist_web/client/features/format.js';

test('formatDuration: under a second shows tenths, floored', () => {
    assert.equal(formatDuration(0), '0.0s');
    assert.equal(formatDuration(0.04), '0.0s');
    assert.equal(formatDuration(0.4), '0.4s');
    assert.equal(formatDuration(0.999), '0.9s');
});

test('formatDuration: from one second to a minute shows whole seconds, floored', () => {
    assert.equal(formatDuration(1), '1s');
    assert.equal(formatDuration(1.999), '1s');
    assert.equal(formatDuration(12), '12s');
    assert.equal(formatDuration(12.7), '12s');
    assert.equal(formatDuration(59), '59s');
    assert.equal(formatDuration(59.999), '59s');
});

test('formatDuration: from a minute shows minutes and two-digit seconds', () => {
    assert.equal(formatDuration(60), '1m 00s');
    assert.equal(formatDuration(65), '1m 05s');
    assert.equal(formatDuration(125.9), '2m 05s');
    assert.equal(formatDuration(3599), '59m 59s');
    assert.equal(formatDuration(3600), '60m 00s');
});

test('formatDuration: the float error of subtracted epoch timestamps does not lose a tenth', () => {
    assert.equal(formatDuration(1780000000.7 - 1780000000.4), '0.3s');
    assert.equal(formatDuration(1780000012.0 - 1780000000.0), '12s');
});

test('formatDuration: a missing or negative duration is empty', () => {
    for (const value of [-1, -0.001, NaN, Infinity, undefined, null, '5']) assert.equal(formatDuration(value), '', String(value));
});

test('toolTarget: the path, the command or the query of the call', () => {
    assert.equal(toolTarget({ actionType: 'read_file', args: { path: 'src/a.ts' } }), 'src/a.ts');
    assert.equal(toolTarget({ actionType: 'run_shell', args: { command: 'npm test' } }), 'npm test');
    assert.equal(toolTarget({ actionType: 'search_code', args: { query: 'recover' } }), 'recover');
    assert.equal(toolTarget({ actionType: 'fetch_url', args: { url: 'https://example.test/a' } }), 'https://example.test/a');
});

test('toolTarget: argv lists are joined, several paths are comma separated', () => {
    assert.equal(toolTarget({ actionType: 'run_shell', args: { command: ['npm', 'run', 'test'] } }), 'npm run test');
    assert.equal(toolTarget({ actionType: 'read_multiple_files', args: { paths: ['a.ts', 'b.ts'] } }), 'a.ts, b.ts');
});

test('toolTarget: git calls name themselves', () => {
    assert.equal(toolTarget({ actionType: 'git_status', args: {} }), 'git status');
    assert.equal(toolTarget({ actionType: 'git_diff', args: {} }), 'git diff');
    assert.equal(toolTarget({ actionType: 'git_diff', args: { path: 'a.ts' } }), 'git diff a.ts');
});

test('toolTarget: a call that lost its arguments falls back to the path of its result', () => {
    assert.equal(toolTarget({ actionType: 'replace_in_file', args: {}, path: 'src/recovery.ts' }), 'src/recovery.ts');
    assert.equal(toolTarget({ actionType: 'run_shell', args: {} }), '');
});

test('toolTarget: only the first non-empty line, whitespace collapsed', () => {
    assert.equal(toolTarget({ actionType: 'run_shell', args: { command: 'python - <<EOF\nprint(1)\nEOF' } }), 'python - <<EOF');
    assert.equal(toolTarget({ actionType: 'run_shell', args: { command: '\n\n  ls   -la\t/tmp\r\nrm x' } }), 'ls -la /tmp');
});

test('toolTarget: at most 80 characters, the cut ends in an ellipsis', () => {
    const at80 = 'a'.repeat(80), at81 = 'a'.repeat(81);
    assert.equal(toolTarget({ actionType: 'read_file', args: { path: at80 } }), at80);
    const cut = toolTarget({ actionType: 'read_file', args: { path: at81 } });
    assert.equal(cut, `${'a'.repeat(79)}…`);
    assert.equal(Array.from(cut).length, 80);
});

test('toolTarget: the cut does not split a surrogate pair', () => {
    const cut = toolTarget({ actionType: 'run_shell', args: { command: '😀'.repeat(90) } });
    assert.equal(cut, `${'😀'.repeat(79)}…`);
});

test('toolStatus: running until a result arrives, then ok or failed', () => {
    assert.equal(toolStatus({ status: 'running', ok: null, exitCode: null }), 'running');
    assert.equal(toolStatus({ status: 'ok', ok: true, exitCode: 0 }), 'ok');
    assert.equal(toolStatus({ status: 'ok', ok: null, exitCode: null }), 'ok');
    assert.equal(toolStatus({ status: 'failed', ok: false, exitCode: null }), 'failed');
});

test('toolStatus: ok:false or a non-zero exit code is failed even if status says ok', () => {
    assert.equal(toolStatus({ status: 'ok', ok: false, exitCode: null }), 'failed');
    assert.equal(toolStatus({ status: 'ok', ok: true, exitCode: 2 }), 'failed');
});

test('groupStatus: running beats failed beats ok', () => {
    const ok = { status: 'ok', ok: true }, bad = { status: 'failed', ok: false }, run = { status: 'running' };
    assert.equal(groupStatus([ok, ok, ok]), 'ok');
    assert.equal(groupStatus([ok, bad, ok]), 'failed');
    assert.equal(groupStatus([ok, bad, run]), 'running');
});

test('callSeconds: needs both timestamps, in order', () => {
    assert.equal(callSeconds({ startedAt: 100, endedAt: 112 }), 12);
    assert.equal(callSeconds({ startedAt: 100, endedAt: 100 }), 0);
    assert.equal(callSeconds({ startedAt: null, endedAt: 112 }), null);
    assert.equal(callSeconds({ startedAt: 100, endedAt: null }), null);
    assert.equal(callSeconds({ startedAt: 0, endedAt: 112 }), null, 'ts 0 is the journal parse fallback, not a time');
    assert.equal(callSeconds({ startedAt: 112, endedAt: 100 }), null);
});

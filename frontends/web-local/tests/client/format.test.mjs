import test from 'node:test';
import assert from 'node:assert/strict';
import { answerSummary, callSeconds, choiceOptions, formatDuration, groupStatus, isDenied, toolStatus, toolTarget } from '../../kvist_web/client/features/format.js';

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

const target = (actionType, args = {}, extra = {}) => toolTarget({ actionType, args, ...extra });
const text = (actionType, args, extra) => { const t = target(actionType, args, extra); assert.equal(t.dir, ''); return t.name; };

test('toolTarget: a path splits at its last slash, nothing is cut', () => {
    assert.deepEqual(target('read_file', { path: 'src/a.ts' }), { dir: 'src/', name: 'a.ts', full: 'src/a.ts' });
    assert.deepEqual(target('read_file', { path: '/abs/dir/file.js' }), { dir: '/abs/dir/', name: 'file.js', full: '/abs/dir/file.js' });
    assert.deepEqual(target('read_file', { path: '/etc' }), { dir: '/', name: 'etc', full: '/etc' });
    assert.deepEqual(target('list_dir', { path: 'src/features/' }), { dir: 'src/', name: 'features/', full: 'src/features/' });
    assert.deepEqual(target('fetch_url', { url: 'https://example.test/a' }), { dir: 'https://example.test/', name: 'a', full: 'https://example.test/a' });
});

test('toolTarget: a long path keeps its file and its parent whole, the whole path is in full', () => {
    const path = `${'very-long-directory-name/'.repeat(8)}parent/module.ts`;
    const t = target('read_file', { path });
    assert.equal(t.dir + t.name, path);
    assert.equal(t.name, 'module.ts');
    assert(t.dir.endsWith('/parent/'));
    assert.equal(t.full, path);
    assert(path.length > 80, 'a path is not cut to 80 characters, the row cuts its head by width');
});

test('toolTarget: a command, a query or a bare name has no dir and is cut from the end', () => {
    assert.equal(text('run_shell', { command: 'npm test' }), 'npm test');
    assert.equal(text('run_shell', { command: 'ls /tmp' }), 'ls /tmp', 'a command that names a path is still a command');
    assert.equal(text('search_code', { query: 'recover' }), 'recover');
    assert.equal(text('read_file', { path: 'README' }), 'README');
    assert.equal(text('run_shell', { command: '/' }), '/');
    assert.equal(text('run_shell', {}), '');
});

test('toolTarget: argv lists are joined, several paths are comma separated', () => {
    assert.equal(text('run_shell', { command: ['npm', 'run', 'test'] }), 'npm run test');
    assert.equal(text('read_multiple_files', { paths: ['a.ts', 'b.ts'] }), 'a.ts, b.ts');
});

test('toolTarget: git calls name themselves', () => {
    assert.equal(text('git_status', {}), 'git status');
    assert.equal(text('git_diff', {}), 'git diff');
    assert.equal(text('git_diff', { path: 'a.ts' }), 'git diff a.ts');
});

test('toolTarget: a call that lost its arguments falls back to the path of its result', () => {
    assert.deepEqual(target('replace_in_file', {}, { path: 'src/recovery.ts' }), { dir: 'src/', name: 'recovery.ts', full: 'src/recovery.ts' });
    assert.equal(target('run_shell', {}).full, '');
});

test('toolTarget: only the first non-empty line, whitespace collapsed', () => {
    assert.equal(text('run_shell', { command: 'python - <<EOF\nprint(1)\nEOF' }), 'python - <<EOF');
    assert.equal(text('run_shell', { command: '\n\n  ls   -la\t/tmp\r\nrm x' }), 'ls -la /tmp');
});

test('toolTarget: a command is at most 80 characters, the cut ends in an ellipsis and keeps the full line', () => {
    const at80 = 'a b'.repeat(26) + 'ab', at81 = at80 + 'c';
    assert.equal(Array.from(at80).length, 80);
    assert.equal(text('run_shell', { command: at80 }), at80);
    const t = target('run_shell', { command: at81 });
    assert.equal(t.name, `${at81.slice(0, 79)}…`);
    assert.equal(Array.from(t.name).length, 80);
    assert.equal(t.full, at81);
});

test('toolTarget: the cut does not split a surrogate pair', () => {
    const cut = text('run_shell', { command: '😀 '.repeat(90) });
    assert.equal(Array.from(cut).length, 80);
    assert(cut.endsWith('…'));
    assert(!/[\ud800-\udbff]…$/.test(cut));
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

const command = { kind: 'confirm', title: 'Run the test suite?', subject: { kind: 'command', command: 'npm test', reason: 'Verify' } };

test('answerSummary: a command approval reads "approved: run npm test", a refusal "denied: ..."', () => {
    assert.equal(answerSummary(command, { approved: true }), 'approved: run npm test');
    assert.equal(answerSummary(command, { approved: true, remember: true }), 'approved: run npm test');
    assert.equal(answerSummary(command, { approved: false }), 'denied: run npm test');
    const overwrite = { kind: 'confirm', title: 'Overwrite?', subject: { kind: 'overwrite', path: 'src/a.ts', diff: '+x' } };
    assert.equal(answerSummary(overwrite, { approved: false }), 'denied: overwrite src/a.ts');
    assert.equal(answerSummary({ kind: 'confirm', title: 'Continue?' }, { approved: true }), 'approved: Continue?');
    assert.equal(answerSummary({ kind: 'confirm' }, { approved: true }), 'approved: request');
});

test('answerSummary: choice shows the label of the option, text shows the text', () => {
    const choice = { kind: 'choice', title: 'Pick a model', options: ['fast', { value: 's', label: 'smart' }] };
    assert.equal(answerSummary(choice, { value: 's' }), 'Pick a model: smart');
    assert.equal(answerSummary(choice, { value: 'fast' }), 'Pick a model: fast');
    assert.equal(answerSummary({ kind: 'text', title: 'Name' }, { value: 'Ada' }), 'Name: Ada');
    assert.equal(answerSummary({ kind: 'text', title: 'Name' }, { value: '' }), 'Name: (empty)');
    assert.equal(answerSummary({ kind: 'text' }, { value: 'x' }), 'answered: x');
});

test('answerSummary: a secret is never in the summary, whatever the response holds', () => {
    const summary = answerSummary({ kind: 'secret', title: 'API key' }, { value: 'hunter2-the-secret' });
    assert.equal(summary, 'API key: secret provided');
    assert(!summary.includes('hunter2'));
    assert.equal(answerSummary({ kind: 'secret', title: 'API key', current_value: 'old-secret' }, { approved: true }), 'API key: secret provided');
    assert.equal(answerSummary({ kind: 'secret' }, { value: 'x' }), 'Secret: secret provided');
});

test('answerSummary: one line of at most 80 characters', () => {
    const long = answerSummary({ kind: 'text', title: 'T' }, { value: `${'x'.repeat(200)}\nsecond line` });
    assert.equal(Array.from(long).length, 80);
    assert(!long.includes('\n'));
    const multi = { kind: 'confirm', subject: { kind: 'command', command: 'echo a\necho b' } };
    assert.equal(answerSummary(multi, { approved: true }), 'approved: run echo a');
});

test('isDenied: only a denied summary gets the cross', () => {
    assert(isDenied('denied: run npm test'));
    assert(!isDenied('approved: run npm test'));
    assert(!isDenied('Name: denied: x'));
});

test('choiceOptions: bare values and objects become { value, label } strings', () => {
    assert.deepEqual(choiceOptions({ options: ['a', { value: 'b', label: 'Bee' }, { value: 3 }, null] }),
        [{ value: 'a', label: 'a' }, { value: 'b', label: 'Bee' }, { value: '3', label: '3' }, { value: '', label: '' }]);
    assert.deepEqual(choiceOptions({}), []);
    assert.deepEqual(choiceOptions({ options: null }), []);
});

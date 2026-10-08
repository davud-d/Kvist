import assert from 'node:assert/strict';
import { test, TestContext } from 'node:test';
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import { spawn, ChildProcess } from 'node:child_process';
import type * as vscode from 'vscode';

import {
    buildCacheKey,
    buildCompletionRequest,
    CompletionCache,
    CompletionHistory,
    CompletionClient,
    responseCandidates,
    isDocumentTooLargeForCompletion,
    parseCompletionResponse
} from './completionClient';

test('buildCompletionRequest splits the buffer at the cursor offset', () => {
    const request = buildCompletionRequest('src/foo.py', 'Python', 'before|after', 'before'.length);
    assert.equal(request.prefix, 'before');
    assert.equal(request.suffix, '|after');
    assert.equal(request.language, 'python');
});

test('buildCompletionRequest normalizes backslashes to forward slashes', () => {
    const request = buildCompletionRequest('src\\foo\\bar.ts', 'typescript', '', 0);
    assert.equal(request.file_path, 'src/foo/bar.ts');
});

test('buildCompletionRequest never drops leading/trailing text at the boundaries', () => {
    const full = 'abcdef';
    const request = buildCompletionRequest('a.ts', 'typescript', full, 3);
    assert.equal(request.prefix + request.suffix, full);
});

test('isDocumentTooLargeForCompletion allows ordinary files', () => {
    assert.equal(isDocumentTooLargeForCompletion('x'.repeat(1_000)), false);
});

test('isDocumentTooLargeForCompletion rejects a buffer past the cap', () => {
    assert.equal(isDocumentTooLargeForCompletion('x'.repeat(600_000)), true);
});

test('buildCacheKey includes context far from the cursor', () => {
    // A different import/scope must invalidate even when the nearby text matches.
    const prefixA = 'A'.repeat(300) + 'x'.repeat(200) + 'TAIL';
    const prefixB = 'B'.repeat(300) + 'x'.repeat(200) + 'TAIL';
    const suffix = 'HEAD' + 'y'.repeat(500);
    assert.notEqual(buildCacheKey('a.py', prefixA, suffix), buildCacheKey('a.py', prefixB, suffix));
});

test('buildCacheKey changes when the file path changes', () => {
    const key1 = buildCacheKey('a.py', 'prefix', 'suffix');
    const key2 = buildCacheKey('b.py', 'prefix', 'suffix');
    assert.notEqual(key1, key2);
});

test('buildCacheKey changes when the near-cursor text changes', () => {
    const key1 = buildCacheKey('a.py', 'def foo(', 'suffix');
    const key2 = buildCacheKey('a.py', 'def bar(', 'suffix');
    assert.notEqual(key1, key2);
});

test('parseCompletionResponse accepts a well-formed ok reply', () => {
    const response = parseCompletionResponse('{"ok": true, "completion": "return 1"}\n');
    assert.deepEqual(response, { ok: true, completion: 'return 1' });
});

test('parseCompletionResponse accepts an empty completion as a valid "no suggestion" reply', () => {
    const response = parseCompletionResponse('{"ok": true, "completion": ""}');
    assert.deepEqual(response, { ok: true, completion: '' });
});

test('parseCompletionResponse reads only the last line, ignoring anything printed before it', () => {
    const response = parseCompletionResponse('some stray warning\n{"ok": true, "completion": "x"}');
    assert.deepEqual(response, { ok: true, completion: 'x' });
});

test('parseCompletionResponse reports a failure reply as-is', () => {
    const response = parseCompletionResponse('{"ok": false, "error": "No provider is configured."}');
    assert.deepEqual(response, { ok: false, error: 'No provider is configured.' });
});

test('parseCompletionResponse treats unparsable output as an unreadable reply, never a throw', () => {
    const response = parseCompletionResponse('not json at all');
    assert.equal(response.ok, false);
    assert.equal(typeof response.error, 'string');
});

test('parseCompletionResponse rejects a JSON array or a bare scalar', () => {
    assert.equal(parseCompletionResponse('[1, 2, 3]').ok, false);
    assert.equal(parseCompletionResponse('42').ok, false);
    assert.equal(parseCompletionResponse('null').ok, false);
});

test('CompletionCache returns what was set', () => {
    const cache = new CompletionCache(3);
    cache.set('k1', 'v1');
    assert.equal(cache.get('k1'), 'v1');
});

test('CompletionCache misses on an absent key', () => {
    const cache = new CompletionCache(3);
    assert.equal(cache.get('missing'), undefined);
});

test('CompletionCache evicts the least recently used entry once past its limit', () => {
    const cache = new CompletionCache(2);
    cache.set('k1', 'v1');
    cache.set('k2', 'v2');
    cache.set('k3', 'v3');
    assert.equal(cache.size, 2);
    assert.equal(cache.get('k1'), undefined);
    assert.equal(cache.get('k2'), 'v2');
    assert.equal(cache.get('k3'), 'v3');
});

test('CompletionCache reading an entry keeps it from being the next eviction', () => {
    const cache = new CompletionCache(2);
    cache.set('k1', 'v1');
    cache.set('k2', 'v2');
    cache.get('k1'); // k1 is now the most recently used
    cache.set('k3', 'v3'); // k2 should be evicted instead of k1
    assert.equal(cache.get('k1'), 'v1');
    assert.equal(cache.get('k2'), undefined);
    assert.equal(cache.get('k3'), 'v3');
});


test('history restores alternatives after a typo, consuming only matching forward input', () => {
    const history = new CompletionHistory();
    const anchor = buildCompletionRequest('a.py', 'python', 'pri', 3);
    history.set('project A', anchor, ['nt(foo)', 'nt(bar)']);
    assert.deepEqual(history.get('project A', { ...anchor, prefix: 'prin' }), ['t(foo)', 't(bar)']);
    assert.equal(history.get('project A', { ...anchor, prefix: 'prixxx' }), undefined);
    assert.deepEqual(history.get('project A', anchor), ['nt(foo)', 'nt(bar)']);
    assert.equal(history.get('project B', anchor), undefined);
    assert.equal(history.get('project A', { ...anchor, suffix: 'changed' }), undefined);
    assert.equal(history.get('project A', { ...anchor, language: 'javascript' }), undefined);
});

test('wire supports old cores and deduplicates candidate lists', () => {
    assert.deepEqual(responseCandidates({ ok: true, completion: 'old' }), ['old']);
    assert.deepEqual(responseCandidates({ ok: true, completions: ['a', 'b', 'a', ''] }), ['a', 'b']);
    assert.deepEqual(responseCandidates({ ok: true, completion: 'old', completions: [] }), []);
});

function mockTimeouts(t: TestContext): void {
    // Node 18's timer mock takes an array; Node 20+ takes an options object.
    // The extension still compiles against @types/node 18.
    const options = Number(process.versions.node.split('.')[0]) >= 20
        ? { apis: ['setTimeout'] } : ['setTimeout'];
    t.mock.timers.enable(options as Parameters<typeof t.mock.timers.enable>[0]);
}

function cancellation() {
    const listeners = new Set<() => void>();
    const token = {
        isCancellationRequested: false,
        onCancellationRequested: (fn: () => void) => {
            listeners.add(fn);
            return { dispose: () => listeners.delete(fn) };
        }
    } as unknown as vscode.CancellationToken;
    return { token, cancel: () => {
        (token as { isCancellationRequested: boolean }).isCancellationRequested = true;
        for (const fn of listeners) { fn(); }
    }, listeners };
}

function transport() {
    const children: Array<ChildProcess & { killedByTest: boolean }> = [];
    const fakeSpawn = (() => {
        const child = Object.assign(new EventEmitter(), {
            stdin: new PassThrough(), stdout: new PassThrough(), stderr: new PassThrough(),
            killedByTest: false,
            kill() { this.killedByTest = true; return true; }
        }) as unknown as ChildProcess & { killedByTest: boolean };
        children.push(child);
        return child;
    }) as typeof spawn;
    return { children, fakeSpawn, reply(index: number, completions: string[]) {
        children[index].stdout?.emit('data', Buffer.from(JSON.stringify({ ok: true, completions })));
        children[index].emit('close', 0);
    } };
}

test('two second idle pause resets on edits; cancelled keystrokes never spawn', async t => {
    mockTimeouts(t);
    const io = transport();
    const client = new CompletionClient(2000, io.fakeSpawn);
    const request = buildCompletionRequest('a.py', 'python', 'pri', 3);
    const first = cancellation();
    const pending = client.requestCompletion('binary', 'workspace', request, first.token);
    t.mock.timers.tick(1999);
    assert.equal(io.children.length, 0);
    first.cancel();
    assert.deepEqual(await pending, []);
    const second = cancellation();
    const next = client.requestCompletion('binary', 'workspace', { ...request, prefix: 'prin' }, second.token);
    t.mock.timers.tick(1999);
    assert.equal(io.children.length, 0);
    t.mock.timers.tick(1);
    assert.equal(io.children.length, 1);
    io.reply(0, ['t(foo)', 't(bar)']);
    assert.deepEqual(await next, ['t(foo)', 't(bar)']);
    assert.equal(second.listeners.size, 0);
    client.dispose();
});

test('backspace cache hit cancels stale work and late answers cannot replace it', async t => {
    mockTimeouts(t);
    const io = transport();
    const client = new CompletionClient(2000, io.fakeSpawn);
    const request = buildCompletionRequest('a.py', 'python', 'pri', 3);
    const first = client.requestCompletion('binary', 'workspace', request, cancellation().token);
    t.mock.timers.tick(2000);
    io.reply(0, ['nt(foo)', 'nt(bar)']);
    await first;
    const typo = client.requestCompletion('binary', 'workspace', { ...request, prefix: 'prix' }, cancellation().token);
    t.mock.timers.tick(2000);
    const restored = await client.requestCompletion('binary', 'workspace', request, cancellation().token);
    assert.deepEqual(restored, ['nt(foo)', 'nt(bar)']);
    assert.deepEqual(await typo, []);
    assert.equal(io.children[1].killedByTest, true);
    io.reply(1, ['stale']);
    assert.deepEqual(await client.requestCompletion('binary', 'workspace', request, cancellation().token), restored);
    assert.equal(io.children.length, 2);
    client.dispose();
});

test('hung child times out and cancellation before cache lookup returns no items', async t => {
    mockTimeouts(t);
    const io = transport();
    const client = new CompletionClient(2000, io.fakeSpawn);
    const request = buildCompletionRequest('a.py', 'python', 'pri', 3);
    const pending = client.requestCompletion('binary', 'workspace', request, cancellation().token);
    t.mock.timers.tick(2000);
    t.mock.timers.tick(4000);
    assert.deepEqual(await pending, []);
    assert.equal(io.children[0].killedByTest, true);
    const cancelled = cancellation(); cancelled.cancel();
    assert.deepEqual(await client.requestCompletion('binary', 'workspace', request, cancelled.token), []);
    assert.equal(io.children.length, 1);
    client.dispose();
});

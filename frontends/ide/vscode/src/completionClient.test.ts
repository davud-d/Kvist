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
    isDocumentTooLargeForCompletion,
    NO_COMPLETION,
    parseServerMessage
} from './completionClient';

const plain = (text: string) => ({ text, before: 0, after: 0 });

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

test('parseServerMessage reads deltas, completion and failures', () => {
    assert.deepEqual(parseServerMessage('{"id": 3, "delta": "ret"}'), { id: 3, delta: 'ret' });
    assert.deepEqual(parseServerMessage('{"id": 3, "done": true}'), { id: 3, done: true });
    assert.deepEqual(parseServerMessage('{"id": 4, "error": "No provider"}'), { id: 4, error: 'No provider' });
});

test('parseServerMessage ignores anything that is not a reply with an id, never throws', () => {
    assert.equal(parseServerMessage('some stray warning'), undefined);
    assert.equal(parseServerMessage('[1, 2]'), undefined);
    assert.equal(parseServerMessage('null'), undefined);
    assert.equal(parseServerMessage('{"delta": "x"}'), undefined);
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


test('history restores the suggestion after a typo, consuming only matching forward input', () => {
    const history = new CompletionHistory();
    const anchor = buildCompletionRequest('a.py', 'python', 'pri', 3);
    history.set('project A', anchor, plain('nt(foo)'));
    assert.equal(history.get('project A', { ...anchor, prefix: 'prin' })?.text, 't(foo)');
    assert.equal(history.get('project A', { ...anchor, prefix: 'print(foo)' })?.text, undefined);
    assert.equal(history.get('project A', { ...anchor, prefix: 'prixxx' })?.text, undefined);
    assert.equal(history.get('project A', anchor)?.text, 'nt(foo)');
    assert.equal(history.get('project B', anchor)?.text, undefined);
    assert.equal(history.get('project A', { ...anchor, suffix: 'changed' })?.text, undefined);
    assert.equal(history.get('project A', { ...anchor, language: 'javascript' })?.text, undefined);
});

test('history remembers that the model had nothing to add', () => {
    const history = new CompletionHistory();
    const anchor = buildCompletionRequest('a.py', 'python', 'x', 1);
    history.set('scope', anchor, NO_COMPLETION);
    assert.equal(history.get('scope', anchor)?.text, '');
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
    const children: Array<ChildProcess & { killedByTest: boolean; written: string[] }> = [];
    const fakeSpawn = (() => {
        const child = Object.assign(new EventEmitter(), {
            stdin: new PassThrough(), stdout: new PassThrough(), stderr: new PassThrough(),
            killedByTest: false, written: [] as string[],
            kill() { this.killedByTest = true; return true; }
        }) as unknown as ChildProcess & { killedByTest: boolean; written: string[] };
        child.stdin?.on('data', data => child.written.push(...data.toString().split('\n').filter(Boolean)));
        children.push(child);
        return child;
    }) as typeof spawn;
    const send = (index: number, ...messages: object[]) => {
        for (const message of messages) {
            children[index].stdout?.emit('data', Buffer.from(JSON.stringify(message) + '\n'));
        }
    };
    return { children, fakeSpawn, send, requests: (index: number) => children[index].written.map(line => JSON.parse(line)) };
}

const flush = () => new Promise(resolve => setImmediate(resolve));

test('pause before a request resets on edits; cancelled keystrokes send nothing', async t => {
    mockTimeouts(t);
    const io = transport();
    const client = new CompletionClient(300, io.fakeSpawn);
    const request = buildCompletionRequest('a.py', 'python', 'pri', 3);
    const first = cancellation();
    const pending = client.requestCompletion('binary', 'workspace', request, first.token);
    t.mock.timers.tick(299);
    assert.equal(io.children.length, 0);
    first.cancel();
    assert.equal((await pending).text, '');
    const second = cancellation();
    const next = client.requestCompletion('binary', 'workspace', { ...request, prefix: 'prin' }, second.token);
    t.mock.timers.tick(299);
    assert.equal(io.children.length, 0);
    t.mock.timers.tick(1);
    await flush();
    assert.equal(io.children.length, 1);
    const [sent] = io.requests(0);
    assert.equal(sent.prefix, 'prin');
    io.send(0, { id: sent.id, delta: 't(' }, { id: sent.id, delta: 'foo)' }, { id: sent.id, done: true });
    assert.equal((await next).text, 't(foo)');
    assert.equal(second.listeners.size, 0);
    client.dispose();
});

test('one process serves every request and is started with --complete-server', async t => {
    mockTimeouts(t);
    const io = transport();
    const spawned: string[][] = [];
    const client = new CompletionClient(0, ((bin: string, args: string[]) => {
        spawned.push(args);
        return io.fakeSpawn(bin, args);
    }) as typeof spawn);
    const request = buildCompletionRequest('a.py', 'python', 'a', 1);
    for (const [index, prefix] of ['a', 'ab'].entries()) {
        const pending = client.requestCompletion('binary', 'workspace', { ...request, prefix }, cancellation().token);
        t.mock.timers.tick(0);
        await flush();
        const sent = io.requests(0)[index];
        io.send(0, { id: sent.id, delta: 'x' }, { id: sent.id, done: true });
        assert.equal((await pending).text, 'x');
    }
    assert.equal(io.children.length, 1);
    assert.deepEqual(spawned, [['--workspace', 'workspace', '--complete-server']]);
    client.dispose();
    assert.equal(io.children[0].killedByTest, true);
});

test('replies split across chunks are reassembled and replies for other ids are ignored', async t => {
    mockTimeouts(t);
    const io = transport();
    const client = new CompletionClient(0, io.fakeSpawn);
    const pending = client.requestCompletion('binary', 'workspace', buildCompletionRequest('a.py', 'python', 'a', 1), cancellation().token);
    t.mock.timers.tick(0);
    await flush();
    const { id } = io.requests(0)[0];
    const line = JSON.stringify({ id, delta: 'whole' }) + '\n';
    io.children[0].stdout?.emit('data', Buffer.from(JSON.stringify({ id: id + 99, delta: 'other' }) + '\n' + line.slice(0, 7)));
    io.children[0].stdout?.emit('data', Buffer.from(line.slice(7)));
    io.send(0, { id, done: true });
    assert.equal((await pending).text, 'whole');
    client.dispose();
});

test('a failure reply or a dying process gives no suggestion and the next request restarts the process', async t => {
    mockTimeouts(t);
    const io = transport();
    const client = new CompletionClient(0, io.fakeSpawn);
    const request = buildCompletionRequest('a.py', 'python', 'a', 1);
    const failed = client.requestCompletion('binary', 'workspace', request, cancellation().token);
    t.mock.timers.tick(0);
    await flush();
    io.send(0, { id: io.requests(0)[0].id, error: 'No provider is configured.' });
    assert.equal((await failed).text, '');

    const crashed = client.requestCompletion('binary', 'workspace', { ...request, prefix: 'ab' }, cancellation().token);
    t.mock.timers.tick(0);
    await flush();
    io.children[0].emit('close', 1);
    assert.equal((await crashed).text, '');

    const again = client.requestCompletion('binary', 'workspace', { ...request, prefix: 'abc' }, cancellation().token);
    t.mock.timers.tick(0);
    await flush();
    assert.equal(io.children.length, 2);
    io.send(1, { id: io.requests(1)[0].id, done: true });
    assert.equal((await again).text, '');
    client.dispose();
});

test('cancelling a sent request tells the process to stop it', async t => {
    mockTimeouts(t);
    const io = transport();
    const client = new CompletionClient(0, io.fakeSpawn);
    const cancelled = cancellation();
    const pending = client.requestCompletion('binary', 'workspace', buildCompletionRequest('a.py', 'python', 'a', 1), cancelled.token);
    t.mock.timers.tick(0);
    await flush();
    const { id } = io.requests(0)[0];
    cancelled.cancel();
    assert.equal((await pending).text, '');
    await flush();
    assert.deepEqual(io.requests(0)[1], { cancel: id });
    io.send(0, { id, delta: 'late' }, { id, done: true });
    client.dispose();
});

test('backspace cache hit cancels stale work and late answers cannot replace it', async t => {
    mockTimeouts(t);
    const io = transport();
    const client = new CompletionClient(0, io.fakeSpawn);
    const request = buildCompletionRequest('a.py', 'python', 'pri', 3);
    const first = client.requestCompletion('binary', 'workspace', request, cancellation().token);
    t.mock.timers.tick(0);
    await flush();
    io.send(0, { id: io.requests(0)[0].id, delta: 'nt(foo)' }, { id: io.requests(0)[0].id, done: true });
    await first;
    const typo = client.requestCompletion('binary', 'workspace', { ...request, prefix: 'prix' }, cancellation().token);
    t.mock.timers.tick(0);
    await flush();
    const typoId = io.requests(0)[1].id;
    const restored = await client.requestCompletion('binary', 'workspace', request, cancellation().token);
    assert.equal(restored.text, 'nt(foo)');
    assert.equal((await typo).text, '');
    io.send(0, { id: typoId, delta: 'stale' }, { id: typoId, done: true });
    assert.deepEqual(await client.requestCompletion('binary', 'workspace', request, cancellation().token), restored);
    client.dispose();
});

test('a request that gets no answer times out and cancellation before cache lookup returns nothing', async t => {
    mockTimeouts(t);
    const io = transport();
    const client = new CompletionClient(300, io.fakeSpawn);
    const request = buildCompletionRequest('a.py', 'python', 'pri', 3);
    const pending = client.requestCompletion('binary', 'workspace', request, cancellation().token);
    t.mock.timers.tick(300);
    t.mock.timers.tick(6000);
    assert.equal((await pending).text, '');
    const cancelled = cancellation(); cancelled.cancel();
    assert.equal((await client.requestCompletion('binary', 'workspace', request, cancelled.token)).text, '');
    assert.equal(io.children.length, 1);
    client.dispose();
});

test('a different binary or workspace gets its own process and the old one is stopped', async t => {
    mockTimeouts(t);
    const io = transport();
    const client = new CompletionClient(0, io.fakeSpawn);
    const request = buildCompletionRequest('a.py', 'python', 'a', 1);
    void client.requestCompletion('binary', 'one', request, cancellation().token);
    t.mock.timers.tick(0);
    await flush();
    void client.requestCompletion('binary', 'two', { ...request, prefix: 'ab' }, cancellation().token);
    t.mock.timers.tick(0);
    await flush();
    assert.equal(io.children.length, 2);
    assert.equal(io.children[0].killedByTest, true);
    client.dispose();
});

test('a replacement is delivered whole, asked for with edits, and reused only at its anchor', async t => {
    mockTimeouts(t);
    const io = transport();
    const client = new CompletionClient(0, io.fakeSpawn);
    const request = buildCompletionRequest('a.txt', 'plaintext', '# Gradle saz', 12);
    const pending = client.requestCompletion('binary', 'workspace', request, cancellation().token, true);
    t.mock.timers.tick(0);
    await flush();
    const [sent] = io.requests(0);
    assert.equal(sent.edits, true);
    io.send(0, { id: sent.id, replace: { text: 'cache\n.gradle', before: 3, after: 0 } }, { id: sent.id, done: true });
    const edit = await pending;
    assert.deepEqual(edit, { text: 'cache\n.gradle', before: 3, after: 0 });
    const again = await client.requestCompletion('binary', 'workspace', request, cancellation().token);
    assert.deepEqual(again, edit);
    const typed = { ...request, prefix: request.prefix + 'c' };
    const history = new CompletionHistory();
    history.set('s', request, edit);
    assert.equal(history.get('s', typed), undefined);
});

test('a malformed replacement is an error, not a range', () => {
    const bad = parseServerMessage(JSON.stringify({ id: 1, replace: { text: 'x', before: -1.5, after: 0 } }));
    assert.equal(bad?.error, 'malformed replacement');
    assert.equal(parseServerMessage(JSON.stringify({ id: 1, replace: { text: 'x', before: 3, after: 0 } }))?.replace?.before, 3);
});

for (const file_path of ['.env', 'config/.ENV.local', '.env.example', '.env/data.py']) {
    test(`transport rejects ${file_path} even for explicit invocation`, async () => {
        const io = transport();
        const client = new CompletionClient(0, io.fakeSpawn);
        const request = { file_path, language: 'plaintext', prefix: 'synthetic', suffix: '' };
        assert.deepEqual(await client.requestCompletion('binary', 'workspace', request, cancellation().token, true), NO_COMPLETION);
        assert.equal(io.children.length, 0);
        client.dispose();
    });
}

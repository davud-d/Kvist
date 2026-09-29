import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
    buildCacheKey,
    buildCompletionRequest,
    CompletionCache,
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

test('buildCacheKey depends only on the tail of the prefix and head of the suffix', () => {
    // Only the region beyond the last 80 chars differs ('A' vs 'B'); the last 80
    // chars themselves (the 'x' block plus 'TAIL') are identical in both.
    const prefixA = 'A'.repeat(300) + 'x'.repeat(200) + 'TAIL';
    const prefixB = 'B'.repeat(300) + 'x'.repeat(200) + 'TAIL';
    const suffix = 'HEAD' + 'y'.repeat(500);
    assert.equal(buildCacheKey('a.py', prefixA, suffix), buildCacheKey('a.py', prefixB, suffix));
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

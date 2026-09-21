import assert from 'node:assert/strict';
import { test } from 'node:test';

import { splitEnvironment } from './environment';

test('plain names with text values are kept', () => {
    const { valid, rejected } = splitEnvironment({ AGENT_SANDBOX_MODE: 'workspace', _X1: '' });
    assert.deepEqual(valid, { AGENT_SANDBOX_MODE: 'workspace', _X1: '' });
    assert.deepEqual(rejected, []);
});

test('names that are not identifiers are refused and reported', () => {
    const { valid, rejected } = splitEnvironment({ 'A=B': '1', '1ABC': '1', 'HAS SPACE': '1', OK: '1' });
    assert.deepEqual(valid, { OK: '1' });
    assert.deepEqual(rejected.sort(), ['1ABC', 'A=B', 'HAS SPACE']);
});

test('values that are not text, or hold a NUL byte, are refused', () => {
    const { valid, rejected } = splitEnvironment({ A: 1, B: null, C: 'a\0b', D: 'fine' });
    assert.deepEqual(valid, { D: 'fine' });
    assert.deepEqual(rejected.sort(), ['A', 'B', 'C']);
});

test('anything that is not an object yields nothing', () => {
    for (const value of [undefined, null, 'x', 5, ['A']]) {
        assert.deepEqual(splitEnvironment(value), { valid: {}, rejected: [] });
    }
});

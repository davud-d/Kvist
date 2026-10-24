import assert from 'node:assert/strict';
import { test } from 'node:test';
import { canReadCompletionDocument, isEnvCompletionPath } from './completionSafety';

for (const path of ['.env', '/project/.env.local', 'C:\\project\\.ENV.production', '.env.example', '.envrc', '.env/data.py']) {
    test(`never reads ${path}`, () => {
        assert.equal(isEnvCompletionPath(path), true);
        assert.equal(canReadCompletionDocument(path, () => { throw new Error('buffer read'); }), undefined);
    });
}

test('canonical environment aliases and unresolved paths never read the buffer', () => {
    const read = () => { throw new Error('buffer read'); };
    assert.equal(canReadCompletionDocument('alias.py', read, () => '/project/.env'), undefined);
    assert.equal(canReadCompletionDocument('unknown.py', read, () => { throw new Error('missing'); }), undefined);
});

test('ordinary source buffers remain available', () => {
    assert.equal(canReadCompletionDocument('/project/environment.py', () => 'source', path => path), 'source');
});

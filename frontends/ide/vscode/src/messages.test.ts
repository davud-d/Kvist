import assert from 'node:assert/strict';
import { test } from 'node:test';

import { ConfigInfo, ProviderInfo, findProvider } from './messages';

const provider = (id: string, custom: boolean, hasCredentials = true): ProviderInfo => ({
    id, label: id, custom, has_credentials: hasCredentials, requires_manual_models: custom
});

const info: ConfigInfo = {
    saved_provider: 'custom:inception',
    saved_role: 'auto',
    providers: [provider('anymodel', false), provider('deepseek', false)],
    custom_providers: [provider('custom:inception', true)]
};

test('a provider the person defined is found beside the built-in ones', () => {
    assert.equal(findProvider(info, 'custom:inception')?.has_credentials, true);
    assert.equal(findProvider(info, 'deepseek')?.custom, false);
    assert.equal(findProvider(info, 'nobody'), undefined);
});

test('an older core that lists no custom providers still resolves the built-in ones', () => {
    const { custom_providers: _unused, ...older } = info;
    assert.equal(findProvider(older, 'anymodel')?.id, 'anymodel');
    assert.equal(findProvider(older, 'custom:inception'), undefined);
});

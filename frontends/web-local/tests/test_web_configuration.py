"""Provider setup reuses core storage and never exposes credential values."""
import json

import pytest

from kernel import user_config
from kernel.providers import anymodel_pricing
from kvist_web import configuration as service
from kvist_web.ssh.config import ssh_aliases
from kvist_web.events import EventBroker
from kvist_web.processes import SessionProcess
from kvist_web.protocol import ExecutionState, BRIDGE_PROTOCOL_VERSION, BridgeRequest
from kvist_web.bridge.service import BridgeApplication


@pytest.fixture(autouse=True)
def clean_provider_environment(monkeypatch):
    for key in ('AGENT_LLM_PROVIDER', 'AGENT_FAST_MODEL', 'AGENT_SMART_MODEL', 'AGENT_REASONING_EFFORT', 'ANYMODEL_API_KEY'):
        monkeypatch.delenv(key, raising=False)


def payload(**changes):
    value = dict(provider='anymodel', models={'fast': 'example/model', 'smart': 'example/model'}, effort='high', credentials={'anymodel_api_key': 'test-only-secret'})
    value.update(changes)
    return value


def test_blank_start_offers_anymodel_without_starting_a_core():
    info = service.configuration_info()
    assert info['setup_required']
    assert info['provider'] == 'anymodel'
    assert info['registration_url'] == 'https://anymodel.org/app/register'
    assert info['model_roles'] == ['fast', 'smart']
    assert 'max' in info['effort_levels']


def test_invalid_anymodel_key_is_not_saved_or_echoed(monkeypatch):
    def fail(*args, **kwargs):
        raise ValueError('provider echoed test-only-secret')
    monkeypatch.setattr(anymodel_pricing, 'fetch_model_coefficients', fail)
    with pytest.raises(ValueError, match='nothing was saved') as error:
        service.save_configuration(payload())
    assert 'test-only-secret' not in str(error.value)
    assert user_config.load_user_config() == {}


def test_setup_uses_existing_storage_and_returns_presence_only(monkeypatch):
    monkeypatch.setattr(anymodel_pricing, 'fetch_model_coefficients', lambda *a, **k: {})
    info = service.save_configuration(payload())
    assert info['setup_required'] is False
    assert 'test-only-secret' not in json.dumps(info)
    saved = user_config.load_user_config()
    assert saved['anymodel_api_key'] == 'test-only-secret'
    assert user_config.get_saved_models('anymodel')['fast'] == 'example/model'
    assert user_config.get_saved_reasoning_effort() == 'high'
    assert user_config.get_saved_provider_or_none() == 'anymodel'
    # An empty password means retain, never erase the saved credential.
    service.save_configuration(payload(credentials={}))
    assert user_config.load_user_config()['anymodel_api_key'] == 'test-only-secret'


def test_model_catalog_is_read_only_and_does_not_echo_provider_errors(monkeypatch):
    # A typed AnyModel key is checked before the catalog (the first run tells a refused key apart).
    monkeypatch.setattr(anymodel_pricing, 'fetch_model_coefficients', lambda *a, **k: {})
    def discover(provider, credentials, **kwargs):
        assert credentials['anymodel_api_key'] == 'test-only-secret'
        return ['example/model'], None
    monkeypatch.setattr(service.llm, 'fetch_available_models', discover)
    assert service.model_catalog(payload()) == {'models': ['example/model']}
    assert user_config.load_user_config() == {}
    monkeypatch.setattr(service.llm, 'fetch_available_models', lambda *a, **k: ([], 'test-only-secret'))
    with pytest.raises(ValueError) as error:
        service.model_catalog(payload())
    assert 'test-only-secret' not in str(error.value)


def test_other_provider_and_endpoint_use_core_catalog(monkeypatch):
    seen = []
    monkeypatch.setattr(service.llm, 'fetch_available_models', lambda provider, credentials, **kw: (seen.append((provider, credentials)) or ['deepseek-chat'], None))
    info = service.save_configuration(payload(provider='deepseek', credentials={'deepseek_api_key': 'other-secret'}, models={'fast':'deepseek-chat', 'smart':'deepseek-chat'}, base_url='https://example.test/v1'))
    assert seen[0][0] == 'deepseek'
    assert seen[0][1]['deepseek_base_url'] == 'https://example.test/v1'
    assert info['provider'] == 'deepseek'
    assert not info['setup_required']
    assert 'other-secret' not in json.dumps(info)


@pytest.mark.parametrize('changes', [dict(provider='unknown'), dict(effort='extreme'), dict(models={}), dict(credentials={'unrelated_api_key':'secret'})])
def test_invalid_configuration_does_not_write(changes):
    with pytest.raises(ValueError):
        service.save_configuration(payload(**changes))
    assert user_config.load_user_config() == {}


def test_ssh_aliases_are_concrete_and_do_not_execute_match(tmp_path):
    path = tmp_path / 'config'
    path.write_text('Host gpu-box build-box\n HostName example.test\nHost * !excluded staging-*\n User dev\nMatch exec "touch /tmp/do-not-execute"\n User other\nHost alpha\n HostName another.test\n')
    assert ssh_aliases(path) == {'aliases': ['alpha','build-box','gpu-box']}
    assert ssh_aliases(tmp_path / 'absent') == {'aliases': []}


def test_model_commands_are_allowlisted_and_wait_for_result(tmp_path, monkeypatch):
    process = SessionProcess(server_id='local', workspace=tmp_path, session='s', broker=EventBroker())
    sent = []
    monkeypatch.setattr(process, '_write', sent.append)
    process.command('models', ['one', 'two'])
    assert sent == [{'type': 'command', 'name': 'models', 'args': ['one', 'two']}]
    assert process.snapshot().state == ExecutionState.RUNNING
    monkeypatch.setattr(process, 'start', lambda: None)
    with pytest.raises(ValueError, match='model change'):
        process.send_goal('racing message')
    with pytest.raises(ValueError, match='Wait'):
        process.command('effort', ['high'])
    process._handle_event({'type':'command_result', 'name':'models', 'ok':True, 'effects':{'models':{'fast':'one', 'smart':'two'}}})
    assert process.snapshot().state == ExecutionState.IDLE
    with pytest.raises(ValueError, match='Only'):
        process.command('full-reset', [])


def test_role_command_is_allowed_with_a_known_role(tmp_path, monkeypatch):
    process = SessionProcess(server_id='local', workspace=tmp_path, session='s', broker=EventBroker())
    sent = []
    monkeypatch.setattr(process, '_write', sent.append)
    with pytest.raises(ValueError, match='Only'):
        process.command('role', ['admin'])
    process.command('role', ['reader'])
    assert sent == [{'type': 'command', 'name': 'role', 'args': ['reader']}]


def test_command_result_is_transient_because_the_journal_never_holds_it(tmp_path):
    broker = EventBroker()
    process = SessionProcess(server_id='local', workspace=tmp_path, session='s', broker=broker)
    process._handle_event({'type': 'command_result', 'name': 'role', 'ok': True, 'effects': {'role': 'reader'}})
    process._handle_event({'type': 'turn_end'})
    durable = {event.event['type']: event.durable for event in broker.after(0, timeout=0)}
    assert durable == {'command_result': False, 'turn_end': True}


def test_chips_get_roles_reasoning_descriptions_and_save_a_role(monkeypatch):
    info = service.configuration_info()
    assert info['role'] == 'auto'
    assert [r['value'] for r in info['roles']][:2] == ['auto', 'coder']
    assert all(r['description'] for r in info['roles'])
    assert [e['value'] for e in info['effort_info']] == info['effort_levels']
    assert info['recommended_models'] == {'anymodel': []}
    monkeypatch.setattr(anymodel_pricing, 'fetch_model_coefficients', lambda *a, **k: {})
    service.save_configuration(payload(role='reader'))
    assert user_config.get_saved_role() == 'reader'
    with pytest.raises(ValueError, match='Unknown role'):
        service.save_configuration(payload(role='admin'))


@pytest.mark.parametrize('error, message', [
    (anymodel_pricing.AnyModelAuthError('401'), 'did not accept this key'),
    (anymodel_pricing.AnyModelUnavailableError('timeout'), "Couldn't reach AnyModel"),
])
def test_first_run_catalog_tells_a_refused_key_from_an_unreachable_anymodel(monkeypatch, error, message):
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(anymodel_pricing, 'fetch_model_coefficients', fail)
    with pytest.raises(ValueError, match=message) as raised:
        service.model_catalog(payload())
    assert 'test-only-secret' not in str(raised.value)


def test_remote_configuration_dispatch_stays_on_remote_runtime(tmp_path):
    app = BridgeApplication(tmp_path / 'workspaces.json')
    calls = []
    class Remote:
        def configuration(self, operation, data):
            calls.append((operation, data))
            return {'provider': 'remote-provider'}
    app.runtime = Remote()
    response = app.dispatch(BridgeRequest('id', 'config.web', {'operation':'info', 'payload':{}}, BRIDGE_PROTOCOL_VERSION))
    assert response.ok
    assert response.result['provider'] == 'remote-provider'
    assert calls == [('info', {})]


def test_rejected_post_closes_connection_before_unread_body_becomes_request():
    from http import HTTPStatus
    from io import BytesIO
    from kvist_web.http_api import ControllerHandler

    handler = ControllerHandler.__new__(ControllerHandler)
    handler.command = 'POST'
    handler.requestline = 'POST /api/v1/configuration HTTP/1.1'
    handler.request_version = 'HTTP/1.1'
    handler.close_connection = False
    handler.wfile = BytesIO()
    handler._error(HTTPStatus.FORBIDDEN, 'Request origin or CSRF token was rejected.')
    assert handler.close_connection
    assert b'Connection: close\r\n' in handler.wfile.getvalue()


def test_old_remote_bridge_keeps_existing_sessions_available(monkeypatch):
    from kvist_web.runtime.remote import RemoteRuntime
    runtime = RemoteRuntime.__new__(RemoteRuntime)
    calls = []
    def rpc(method, **params):
        calls.append(method)
        return {'protocol_version': 1}
    monkeypatch.setattr(runtime, '_rpc', rpc)
    info = runtime.configuration('info')
    assert info['available'] is False
    assert info['setup_required'] is False
    assert calls == ['ping']
    with pytest.raises(ValueError, match='Update Kvist'):
        runtime.configuration('save', payload())


def test_bridge_rejects_model_command_after_shutdown_gate(tmp_path):
    app = BridgeApplication(tmp_path / 'workspaces.json')
    app._accepting_work = False
    response = app.dispatch(BridgeRequest('id', 'sessions.command', {
        'workspace': '/srv/project', 'session': 'test', 'name': 'models', 'args': ['one','two']
    }, BRIDGE_PROTOCOL_VERSION))
    assert not response.ok
    assert 'restarting' in response.error

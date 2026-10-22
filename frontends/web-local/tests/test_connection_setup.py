"""Custom setup is user-authored; these fixtures verify its remote execution hook."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from kvist_web.connection_logs import ConnectionLogs
from kvist_web.events import EventBroker
from kvist_web.protocol import ServerSpec
from kvist_web.security import SecretStore
from kvist_web.ssh.bootstrap import RemoteBootstrap
from kvist_web.ssh.transport import SSHTransport
from test_bridge_and_remote import _BootstrapTransport


class StreamTransport(_BootstrapTransport):
    def __init__(self, *, fail=False, spec=None, password=''):
        super().__init__()
        self.fail = fail
        self.spec = spec or ServerSpec(
            id='remote',
            name='server-gz8q',
            host='',
            ssh_alias='server-gz8q',
            auth_method='ssh_config',
        )
        self.secrets = SecretStore()
        if password:
            self.secrets.set(f'server:{self.spec.id}:password', password, persist=False)
        self.payloads = []

    def upload_file(self, source, *, remote_directory, permissions=0o600):
        self.uploads.append((source, remote_directory))
        if str(source).endswith('.json'):
            self.payloads.append(json.loads(Path(source).read_text()))
            return '/remote/connection payload.json'
        return '/remote/setup script;name.py'

    def execute_stream(self, command, *, on_output, timeout):
        self.commands.append(command)
        on_output('stdout', 'Setup started\n')
        on_output('stderr', 'Setup warning\n')
        if self.fail:
            raise RuntimeError('Setup exited 7')
        return 'Setup started\n'


def test_user_script_is_uploaded_and_run_before_install_check(tmp_path):
    script = tmp_path / 'setup_server.py'
    script.write_text('print("user-owned fixture")')
    transport = StreamTransport()
    logs = []
    result = RemoteBootstrap(transport, run_setup=True, setup_script=script, on_log=lambda stream, text: logs.append((stream, text))).run()
    assert result.installed
    assert len(transport.uploads) == 2
    assert transport.uploads[0] == (script, '.kvist/web-bridge/setup')
    assert transport.uploads[1][1] == '.kvist/web-bridge/setup'
    assert transport.commands[1] == "/usr/bin/python3 -u '/remote/setup script;name.py' --mode ssh-config --connection-json '/remote/connection payload.json'"
    assert 'from kernel.config.version' in transport.commands[2]
    assert transport.payloads == [{
        'auth_method': 'ssh_config',
        'host': '',
        'mode': 'ssh-config',
        'name': 'server-gz8q',
        'port': 22,
        'server_id': 'remote',
        'ssh_alias': 'server-gz8q',
        'username': '',
    }]
    assert ('stdout', 'Setup started\n') in logs
    assert ('stderr', 'Setup warning\n') in logs
    assert transport.removed == ['/remote/connection payload.json', '/remote/setup script;name.py']


def test_setup_script_receives_password_connection_payload(tmp_path):
    script = tmp_path / 'setup_server.py'
    script.write_text('print("user-owned fixture")')
    spec = ServerSpec(
        id='manual',
        name='manual-host',
        host='192.0.2.10',
        port=2222,
        username='dev',
        auth_method='password',
    )
    transport = StreamTransport(spec=spec, password='ssh-password')
    RemoteBootstrap(transport, run_setup=True, setup_script=script, on_log=lambda *args: None).run()
    assert "--mode password" in transport.commands[1]
    assert transport.payloads[0] == {
        'auth_method': 'password',
        'host': '192.0.2.10',
        'mode': 'password',
        'name': 'manual-host',
        'password': 'ssh-password',
        'port': 2222,
        'server_id': 'manual',
        'ssh_alias': '',
        'username': 'dev',
    }


def test_setup_script_receives_key_connection_payload(tmp_path):
    script = tmp_path / 'setup_server.py'
    script.write_text('print("user-owned fixture")')
    spec = ServerSpec(
        id='keyed',
        name='gpu-box',
        host='gpu.example',
        username='dev',
        auth_method='key',
        private_key_path='/home/user/.ssh/gpu',
    )
    transport = StreamTransport(spec=spec)
    RemoteBootstrap(transport, run_setup=True, setup_script=script, on_log=lambda *args: None).run()
    assert "--mode key" in transport.commands[1]
    assert transport.payloads[0]['private_key_path'] == '/home/user/.ssh/gpu'
    assert 'password' not in transport.payloads[0]


def test_missing_user_script_preserves_standard_bootstrap(tmp_path):
    transport = _BootstrapTransport()
    messages = []
    RemoteBootstrap(transport, run_setup=True, setup_script=tmp_path / 'later.py', on_log=lambda stream, text: messages.append(text)).run()
    assert transport.uploads == []
    assert any('No custom setup script yet' in text for text in messages)
    assert not (tmp_path / 'later.py').exists()


def test_automatic_reconnect_does_not_run_user_script(tmp_path):
    script = tmp_path / 'setup_server.py'
    script.write_text('print("never automatic")')
    transport = _BootstrapTransport()
    RemoteBootstrap(transport, setup_script=script).run()
    assert transport.uploads == []


def test_script_failure_is_reported_and_bridge_is_not_started(tmp_path):
    script = tmp_path / 'setup_server.py'
    script.write_text('raise SystemExit(7)')
    transport = StreamTransport(fail=True)
    with pytest.raises(RuntimeError, match='exited 7'):
        RemoteBootstrap(transport, run_setup=True, setup_script=script, on_log=lambda *args: None).run()
    assert not any('bridge_cli ensure' in command for command in transport.commands)
    assert transport.removed == ['/remote/connection payload.json', '/remote/setup script;name.py']


class Channel:
    def __init__(self, stdout, stderr, code=0):
        self.stdout = list(stdout)
        self.stderr = list(stderr)
        self.code = code
        self.closed = False
        self.status_read = False

    def recv_ready(self):
        return bool(self.stdout)

    def recv_stderr_ready(self):
        return bool(self.stderr)

    def recv(self, count):
        return self.stdout.pop(0)

    def recv_stderr(self, count):
        return self.stderr.pop(0)

    def exit_status_ready(self):
        return not self.stdout and not self.stderr

    def recv_exit_status(self):
        self.status_read = True
        return self.code

    def shutdown_write(self):
        pass

    def close(self):
        self.closed = True


def streaming_transport(channel, monkeypatch):
    transport = SSHTransport(ServerSpec(id='test', name='test', host='example'), SecretStore())
    output = SimpleNamespace(channel=channel)
    transport._client = SimpleNamespace(exec_command=lambda *args, **kwargs: (output, output, output))
    monkeypatch.setattr(transport, '_transport_active', lambda: True)
    return transport


def test_stream_drains_both_channels_and_decodes_split_utf8(monkeypatch):
    data = 'готово\n'.encode()
    channel = Channel([data[:3], data[3:]], [b'warning\n', b'last line'])
    transport = streaming_transport(channel, monkeypatch)
    logs = []
    def receive(stream, text):
        assert not channel.status_read
        logs.append((stream, text))
    assert transport.execute_stream('fixed-command', on_output=receive) == 'готово\n'
    assert ('stdout', 'готово\n') in logs
    assert ('stderr', 'warning\n') in logs
    assert ('stderr', 'last line') in logs
    assert channel.closed


def test_stream_output_tail_is_bounded(monkeypatch):
    channel = Channel([b'x' * 4096] * 100, [])
    transport = streaming_transport(channel, monkeypatch)
    received = []
    output = transport.execute_stream('fixed-command', on_output=lambda name, text: received.append(len(text)))
    assert len(output) == 262144
    assert sum(received) == 409600


def test_stream_failure_preserves_output_and_closes_channel(monkeypatch):
    channel = Channel([b'started\n'], [b'setup failed\n'], code=7)
    transport = streaming_transport(channel, monkeypatch)
    logs = []
    with pytest.raises(RuntimeError, match='setup failed'):
        transport.execute_stream('fixed-command', on_output=lambda *args: logs.append(args))
    assert ('stderr', 'setup failed\n') in logs
    assert channel.closed


def test_stream_timeout_closes_channel(monkeypatch):
    channel = Channel([], [])
    transport = streaming_transport(channel, monkeypatch)
    with pytest.raises(TimeoutError):
        transport.execute_stream('fixed-command', on_output=lambda *args: None, timeout=0)
    assert channel.closed


def test_logs_redact_secrets_are_bounded_and_not_durable():
    broker = EventBroker()
    logs = ConnectionLogs(broker, 'remote', 'attempt', password='plain-secret')
    logs.append('stdout', '\x1b[32mAPI_KEY=private-key password=secret plain-secret\x1b[0m\n')
    first = logs.result({})['logs'][0]['text']
    assert 'private-key' not in first and 'plain-secret' not in first and '\x1b' not in first
    assert '[REDACTED]' in first
    for i in range(500):
        logs.append('stdout', 'x' * 4096)
    assert logs.size <= 65536
    assert len(logs.entries) <= 300
    events = broker.after(0, timeout=0)
    assert not any(event.durable for event in events)
    assert all(event.workspace == event.session == '' for event in events)
    assert all(event.event['attempt_id'] == 'attempt' for event in events)


def test_manual_connect_route_enables_setup_and_correlates_browser_logs():
    from http.client import HTTPMessage
    from io import BytesIO
    import json

    from kvist_web.http_api import ControllerHandler
    from kvist_web.security import BrowserAuth

    auth = BrowserAuth()
    browser = auth.create()
    calls = []
    def connect(*args, **kwargs):
        calls.append((args, kwargs))
        return {'state':'connected', 'logs':[]}
    handler = ControllerHandler.__new__(ControllerHandler)
    handler.server = SimpleNamespace(server_address=('127.0.0.1',9672), allowed_hosts={'127.0.0.1'}, external_token='', auth=auth, controller=SimpleNamespace(connect_server=connect))
    handler.command = 'POST'
    handler.path = '/api/v1/servers/connect'
    handler.request_version = 'HTTP/1.1'
    handler.requestline = 'POST /api/v1/servers/connect HTTP/1.1'
    handler.close_connection = False
    body = json.dumps({'server_id':'remote', 'attempt_id':'browser-attempt', 'script':'not-a-browser-controlled-path'}).encode()
    handler.headers = HTTPMessage()
    for key, value in {
        'Host':'127.0.0.1:9672', 'Origin':'http://127.0.0.1:9672',
        'Cookie':f'kvist_session={browser.cookie}', 'X-Kvist-CSRF':browser.csrf,
        'Content-Length':str(len(body)),
    }.items():
        handler.headers[key] = value
    handler.rfile = BytesIO(body)
    handler.wfile = BytesIO()
    handler.do_POST()
    assert calls == [(('remote', None), {'run_setup':True, 'attempt_id':'browser-attempt'})]
    assert b'200 OK' in handler.wfile.getvalue()

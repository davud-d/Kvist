from __future__ import annotations

import json
import zipfile

import pytest

from kernel.session_store import SessionStore

from kvist_web.bridge.service import BridgeApplication
from kvist_web import bridge_cli
from kvist_web.events import EventBroker
from kvist_web.protocol import BRIDGE_PROTOCOL_VERSION, BridgeRequest, ServerSpec
from kvist_web.runtime.remote import RemoteRuntime
from kvist_web.security import SecretStore
from kvist_web.ssh import bootstrap as bootstrap_module
from kvist_web.ssh.bootstrap import RemoteBootstrap
from kvist_web.ssh.transport import SSHTransport
from kvist_web.storage import WebStateStore

from test_local_runtime import make_fake_core, wait_for


class _Runtime:
    def __init__(self):
        self.running = True

    def list_workspaces(self):
        return ["/srv/project"]

    def processes(self):
        return [{"session": "remote", "state": "running" if self.running else "finished"}]

    def history(self, workspace, session, after=0, limit=1000):
        return {"events": [{"seq": after + 1, "event": {"type": "final"}}]}


def _request(method, **params):
    return BridgeRequest("r1", method, params, BRIDGE_PROTOCOL_VERSION)


def test_bridge_keeps_process_state_when_a_client_disappears(tmp_path):
    app = BridgeApplication(tmp_path / "workspaces.json")
    app.runtime = _Runtime()

    before = app.dispatch(_request("processes.list"))
    # No client object is retained by the bridge; a later independent request
    # sees the same supervisor-owned process.
    after = app.dispatch(_request("processes.list"))

    assert before.ok and after.ok
    assert after.result["processes"][0]["state"] == "running"


def test_bridge_recovers_missing_durable_events(tmp_path):
    app = BridgeApplication(tmp_path / "workspaces.json")
    app.runtime = _Runtime()
    response = app.dispatch(
        _request(
            "sessions.history",
            workspace="/srv/project",
            session="remote",
            after=1842,
            limit=1000,
        )
    )
    assert response.ok is True
    assert response.result["events"][0]["seq"] == 1843


def test_bridge_live_stream_reports_epoch_gap_instead_of_silent_loss(tmp_path):
    app = BridgeApplication(tmp_path / "workspaces.json")
    app.broker = EventBroker(capacity=1)
    app.broker.publish("remote", "/srv/project", "remote", {"type": "status"})
    app.broker.publish("remote", "/srv/project", "remote", {"type": "stream_chunk"})

    response = app.dispatch(
        _request("events.live", after=0, epoch=app.broker.epoch, timeout=0)
    )

    assert response.ok is True
    assert response.result["reset"] is True
    assert response.result["reason"] == "history_gap"
    assert response.result["events"] == []


class _BootstrapTransport:
    def __init__(self, version="0.1.0b1", python_version=(3, 11, 0)):
        self.version = version
        self.python_version = python_version
        self.commands = []
        self.uploads = []
        self.removed = []

    def execute(self, command, stdin="", timeout=0):
        self.commands.append(command)
        if "platform.system" in command:
            return json.dumps(
                {
                    "os": "linux",
                    "python": "/usr/bin/python3",
                    "version": list(self.python_version),
                }
            )
        if "from kernel.version" in command:
            if self.version is None:
                raise RuntimeError("missing")
            return json.dumps({"version": self.version})
        if "pip install" in command:
            self.version = "0.1.0b1"
            return "ok"
        if "bridge_cli ensure" in command:
            return json.dumps(
                {
                    "ok": True,
                    "protocol_version": BRIDGE_PROTOCOL_VERSION,
                    "kvist_version": self.version,
                }
            )
        raise AssertionError(command)

    def upload_file(self, source, *, remote_directory):
        self.uploads.append((source, remote_directory))
        return ".kvist/web-bridge/artifacts/remote-kvist.whl"

    def remove_uploaded_file(self, remote_path):
        self.removed.append(remote_path)


def _write_wheel(path, version="0.1.0b1", name="kvist"):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            f"{name}-{version}.dist-info/METADATA",
            "Metadata-Version: 2.1\n"
            f"Name: {name}\n"
            f"Version: {version}\n",
        )
    return path


def test_bootstrap_detects_existing_install_without_reinstall(monkeypatch):
    monkeypatch.setattr("kvist_web.ssh.bootstrap.kvist_version", lambda: "0.1.0b1")
    transport = _BootstrapTransport()
    result = RemoteBootstrap(transport).run()
    assert result.installed is True
    assert not any("pip install" in command for command in transport.commands)


def test_bootstrap_updates_version_mismatch_from_explicit_artifact(tmp_path, monkeypatch):
    monkeypatch.setattr("kvist_web.ssh.bootstrap.kvist_version", lambda: "0.1.0b1")
    wheel = tmp_path / "kvist-0.1.0b1-py3-none-any.whl"
    _write_wheel(wheel)
    monkeypatch.setenv("KVIST_REMOTE_WHEEL", str(wheel))
    transport = _BootstrapTransport("0.0.9")
    result = RemoteBootstrap(transport).run()
    assert result.updated is True
    assert sum("pip install" in command for command in transport.commands) == 1
    assert transport.uploads == [(wheel.resolve(), ".kvist/web-bridge/artifacts")]
    assert transport.removed == [".kvist/web-bridge/artifacts/remote-kvist.whl"]
    assert "remote-kvist.whl" in next(
        command for command in transport.commands if "pip install" in command
    )


def test_bootstrap_rejects_artifact_for_a_different_version(tmp_path, monkeypatch):
    monkeypatch.setattr("kvist_web.ssh.bootstrap.kvist_version", lambda: "0.1.0b1")
    wheel = _write_wheel(tmp_path / "kvist-old.whl", version="0.0.9")
    monkeypatch.setenv("KVIST_REMOTE_WHEEL", str(wheel))
    transport = _BootstrapTransport("0.0.9")

    with pytest.raises(RuntimeError, match="version does not match"):
        RemoteBootstrap(transport).run()

    assert transport.uploads == []
    assert not any("pip install" in command for command in transport.commands)


def test_bootstrap_builds_source_wheel_when_running_from_checkout(tmp_path, monkeypatch):
    monkeypatch.setattr("kvist_web.ssh.bootstrap.kvist_version", lambda: "0.1.0b1")
    monkeypatch.delenv("KVIST_REMOTE_WHEEL", raising=False)
    source = tmp_path / "source"
    source.mkdir()
    calls = []

    def build(root, staging):
        calls.append((root, staging))
        return _write_wheel(staging / "kvist-0.1.0b1-py3-none-any.whl")

    monkeypatch.setattr(bootstrap_module, "_source_checkout_root", lambda: source)
    monkeypatch.setattr(bootstrap_module, "_build_source_wheel", build)
    transport = _BootstrapTransport(version=None)

    result = RemoteBootstrap(transport).run()

    assert result.updated is True
    assert calls and calls[0][0] == source
    assert len(transport.uploads) == 1
    assert transport.removed == [".kvist/web-bridge/artifacts/remote-kvist.whl"]
    assert not transport.uploads[0][0].exists()


class _BridgeMismatchTransport(_BootstrapTransport):
    def __init__(self):
        super().__init__()
        self.ensure_calls = 0

    def execute(self, command, stdin="", timeout=0):
        if "bridge_cli ensure" in command:
            self.commands.append(command)
            self.ensure_calls += 1
            return json.dumps(
                {
                    "ok": True,
                    "protocol_version": 0 if self.ensure_calls == 1 else BRIDGE_PROTOCOL_VERSION,
                    "kvist_version": self.version,
                }
            )
        return super().execute(command, stdin=stdin, timeout=timeout)


def test_bootstrap_refuses_protocol_mismatch_without_reinstall(monkeypatch):
    monkeypatch.setattr("kvist_web.ssh.bootstrap.kvist_version", lambda: "0.1.0b1")
    transport = _BridgeMismatchTransport()

    with pytest.raises(RuntimeError, match="protocol is incompatible"):
        RemoteBootstrap(transport).run()

    assert transport.ensure_calls == 1
    assert not any("pip install" in command for command in transport.commands)


def test_bootstrap_rejects_python_older_than_311(monkeypatch):
    monkeypatch.setattr("kvist_web.ssh.bootstrap.kvist_version", lambda: "0.1.0b1")
    transport = _BootstrapTransport(python_version=(3, 10, 14))

    with pytest.raises(RuntimeError, match="Python 3.10.14"):
        RemoteBootstrap(transport).run()

    assert not any("pip install" in command for command in transport.commands)


def test_bootstrap_requires_artifact_instead_of_registry_install(monkeypatch):
    monkeypatch.setattr("kvist_web.ssh.bootstrap.kvist_version", lambda: "0.1.0b1")
    monkeypatch.delenv("KVIST_REMOTE_WHEEL", raising=False)
    monkeypatch.setattr(bootstrap_module, "_source_checkout_root", lambda: None)
    transport = _BootstrapTransport(version=None)

    with pytest.raises(RuntimeError, match="KVIST_REMOTE_WHEEL"):
        RemoteBootstrap(transport).run()

    assert not any("pip install" in command for command in transport.commands)


def test_ensure_defers_version_restart_while_active_task_runs(monkeypatch):
    monkeypatch.setattr(
        bridge_cli,
        "_existing_bridge",
        lambda: ({"pid": 7, "protocol_version": BRIDGE_PROTOCOL_VERSION}, {"protocol_version": BRIDGE_PROTOCOL_VERSION}),
    )
    monkeypatch.setattr(
        bridge_cli,
        "bridge_status",
        lambda: {
            "protocol_version": BRIDGE_PROTOCOL_VERSION,
            "kvist_version": "old-version",
            "processes": [{"session": "working", "state": "running"}],
        },
    )
    stopped = []
    monkeypatch.setattr(bridge_cli, "request_idle_shutdown", lambda: stopped.append(True))

    value = bridge_cli.ensure(
        expected_protocol=BRIDGE_PROTOCOL_VERSION, expected_version="new-version"
    )

    assert value["reused"] is True
    assert value["version_deferred"] is True
    assert value["active_processes"] == 1
    assert stopped == []


def test_ensure_never_stops_active_protocol_mismatch(monkeypatch):
    monkeypatch.setattr(
        bridge_cli,
        "_existing_bridge",
        lambda: ({"pid": 7, "protocol_version": 0}, {"protocol_version": 0}),
    )
    stopped = []
    monkeypatch.setattr(bridge_cli, "request_idle_shutdown", lambda: stopped.append(True))

    with pytest.raises(RuntimeError, match="No task was stopped"):
        bridge_cli.ensure(
            expected_protocol=BRIDGE_PROTOCOL_VERSION, expected_version="new-version"
        )

    assert stopped == []


def test_ensure_never_overwrites_a_responding_legacy_bridge(tmp_path, monkeypatch):
    descriptor = {
        "pid": 7,
        "host": "127.0.0.1",
        "port": 33123,
        "token": "legacy-token",
        "protocol_version": 0,
    }
    path = tmp_path / "bridge.json"
    path.write_text(json.dumps(descriptor), encoding="utf-8")
    monkeypatch.setattr(bridge_cli, "descriptor_path", lambda: path)
    monkeypatch.setattr(bridge_cli, "read_descriptor", lambda: descriptor)
    monkeypatch.setattr(
        bridge_cli,
        "probe_local_bridge",
        lambda _descriptor: {"ok": False, "protocol_version": 0, "error": "legacy"},
    )
    starts = []
    monkeypatch.setattr(bridge_cli, "_start_bridge", lambda: starts.append(True))

    with pytest.raises(RuntimeError, match="No task was stopped and no replacement"):
        bridge_cli.ensure(expected_protocol=BRIDGE_PROTOCOL_VERSION, expected_version="new")

    assert starts == []


def test_bridge_shutdown_refuses_active_work_and_closes_idle_send_gate(tmp_path):
    app = BridgeApplication(tmp_path / "workspaces.json")
    app.runtime = _Runtime()

    active = app.dispatch(_request("bridge.shutdown"))
    assert active.ok is False
    assert "active tasks" in active.error

    app.runtime.running = False
    idle = app.dispatch(_request("bridge.shutdown"))
    assert idle.ok is True
    assert idle.result == {"stopping": True}
    rejected = app.dispatch(
        _request(
            "sessions.send",
            workspace="/srv/project",
            session="remote",
            text="too late",
        )
    )
    assert rejected.ok is False
    assert "restarting" in rejected.error


class _RpcTransport:
    def __init__(self):
        self.calls = 0

    def execute(self, command, stdin="", timeout=0):
        self.calls += 1
        if self.calls == 1:
            raise OSError("server offline")
        request = json.loads(stdin)
        return json.dumps({"id": request["id"], "ok": True, "result": {"workspaces": ["/w"]}})

    def close(self):
        pass


def test_remote_runtime_reconnects_with_bounded_retry(monkeypatch):
    runtime = RemoteRuntime(
        ServerSpec(id="remote", name="remote", host="host"), SecretStore(), EventBroker()
    )
    runtime._ready = True
    runtime.transport = _RpcTransport()
    monkeypatch.setattr("kvist_web.runtime.remote.time.sleep", lambda _delay: None)
    assert runtime.list_workspaces() == ["/w"]
    assert runtime.transport.calls == 2


class _OfflineTransport:
    def __init__(self):
        self.calls = 0

    def execute(self, command, stdin="", timeout=0):
        self.calls += 1
        raise OSError("network unreachable")

    def close(self):
        pass


def test_remote_runtime_reports_server_offline_after_bounded_retry(monkeypatch):
    runtime = RemoteRuntime(
        ServerSpec(id="offline", name="offline", host="host"), SecretStore(), EventBroker()
    )
    runtime._ready = True
    runtime.transport = _OfflineTransport()
    monkeypatch.setattr("kvist_web.runtime.remote.time.sleep", lambda _delay: None)

    with pytest.raises(RuntimeError, match="network unreachable"):
        runtime.list_workspaces()

    assert runtime.transport.calls == 5


class _AuthParamiko:
    class AuthenticationException(Exception):
        pass

    class BadAuthenticationType(Exception):
        pass

    class SSHException(Exception):
        pass

    class SSHClient:
        def load_system_host_keys(self):
            pass

        def load_host_keys(self, _path):
            pass

        def set_missing_host_key_policy(self, _policy):
            pass

        def connect(self, **_values):
            raise _AuthParamiko.AuthenticationException("denied")

        def close(self):
            pass


def test_ssh_authentication_failure_is_clear_and_redacted(monkeypatch):
    secrets = SecretStore()
    secrets.set("server:locked:password", "do-not-leak", persist=False)
    transport = SSHTransport(
        ServerSpec(
            id="locked",
            name="locked server",
            host="example.invalid",
            auth_method="password",
        ),
        secrets,
    )
    monkeypatch.setattr("kvist_web.ssh.transport._paramiko", lambda: _AuthParamiko)

    with pytest.raises(RuntimeError, match="SSH authentication failed") as failure:
        transport.connect()

    assert "do-not-leak" not in str(failure.value)


class _SFTP:
    def __init__(self):
        self.directories = set()
        self.puts = []
        self.permissions = []
        self.renames = []
        self.removed = []

    def stat(self, path):
        if path not in self.directories:
            raise OSError("not found")

    def mkdir(self, path, mode=0o777):
        self.directories.add(path)

    def put(self, source, target, confirm=True):
        self.puts.append((source, target, confirm))

    def chmod(self, path, mode):
        self.permissions.append((path, mode))

    def rename(self, source, target):
        self.renames.append((source, target))

    def normalize(self, path):
        return "/home/remote/" + path

    def remove(self, path):
        self.removed.append(path)

    def close(self):
        pass


class _SFTPClient:
    def __init__(self, sftp):
        self.sftp = sftp

    def open_sftp(self):
        return self.sftp


def test_sftp_upload_uses_safe_relative_directory_and_private_artifact(tmp_path, monkeypatch):
    wheel = tmp_path / "kvist.whl"
    wheel.write_bytes(b"wheel")
    sftp = _SFTP()
    transport = SSHTransport(
        ServerSpec(id="upload", name="upload", host="host"), SecretStore()
    )
    transport._client = _SFTPClient(sftp)
    monkeypatch.setattr(transport, "_transport_active", lambda: True)

    remote = transport.upload_file(
        wheel, remote_directory=".kvist/web-bridge/artifacts"
    )

    assert remote.startswith("/home/remote/.kvist/web-bridge/artifacts/")
    assert sftp.directories == {".kvist", ".kvist/web-bridge", ".kvist/web-bridge/artifacts"}
    assert sftp.puts and sftp.puts[0][1].endswith(".part")
    assert all(mode == 0o600 for _path, mode in sftp.permissions)
    transport.remove_uploaded_file(remote)
    assert sftp.removed == [remote]
    with pytest.raises(ValueError, match="not a bootstrap artifact"):
        transport.remove_uploaded_file(remote)
    with pytest.raises(ValueError, match="relative safe path"):
        transport.upload_file(wheel, remote_directory="../elsewhere")


def test_remote_poller_reports_reconnect_and_recovery(monkeypatch):
    states = []
    broker = EventBroker()
    runtime = RemoteRuntime(
        ServerSpec(id="remote", name="remote", host="host"),
        SecretStore(),
        broker,
        on_connection=lambda state, details: states.append((state, details)),
    )
    runtime._ready = True
    calls = 0

    def rpc(_method, **_params):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("connection reset")
        runtime._ready = False
        return {
            "events": [
                {
                    "id": 9,
                    "workspace": "/srv/project",
                    "session": "work",
                    "event": {"type": "turn_end"},
                }
            ]
        }

    monkeypatch.setattr(runtime, "_rpc", rpc)
    monkeypatch.setattr("kvist_web.runtime.remote.time.sleep", lambda _delay: None)
    runtime._poll_live()

    assert [state for state, _details in states] == ["reconnecting", "connected"]
    assert states[-1][1]["recovered_events"] == 1
    assert [item.event["type"] for item in broker.after(0, timeout=0)] == [
        "connection.lost",
        "turn_end",
        "connection.restored",
    ]


def test_remote_poller_forwards_durable_marker_and_recovers_remote_broker_reset(monkeypatch):
    broker = EventBroker()
    runtime = RemoteRuntime(
        ServerSpec(id="remote", name="remote", host="host"), SecretStore(), broker
    )
    runtime._ready = True
    replies = iter(
        [
            {"reset": True, "cursor": 4, "epoch": "new", "reason": "epoch_changed"},
            {
                "events": [
                    {
                        "id": 5,
                        "workspace": "/srv/project",
                        "session": "work",
                        "durable": True,
                        "event": {"type": "final", "message": "done"},
                    }
                ],
                "epoch": "new",
            },
        ]
    )

    def rpc(_method, **_params):
        value = next(replies)
        if value.get("events"):
            runtime._ready = False
        return value

    monkeypatch.setattr(runtime, "_rpc", rpc)
    runtime._poll_live()

    events = broker.after(0, timeout=0)
    assert events[0].event["type"] == "stream.reset"
    assert events[1].durable is True
    assert runtime._live_cursor == 5
    assert runtime._live_epoch == "new"


def _bridge_call(app, method, **params):
    response = app.dispatch(
        BridgeRequest("integration", method, params, BRIDGE_PROTOCOL_VERSION)
    )
    assert response.ok is True, response
    return response.result


def test_detached_bridge_survives_client_disconnect_and_recovers_events(
    tmp_path, monkeypatch
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    SessionStore(workspace).create("remote-task")
    core = make_fake_core(tmp_path)
    monkeypatch.setenv("KVIST_CORE_COMMAND", str(core))
    monkeypatch.setenv("KVIST_FAKE_DELAY", "0.45")

    app = BridgeApplication(tmp_path / "bridge-workspaces.json")
    app.runtime.add_workspace(str(workspace))
    web_state = WebStateStore(tmp_path / "web-state.json")
    web_state.initialize_session("remote", str(workspace), "remote-task", 0)
    try:
        sent = _bridge_call(
            app,
            "sessions.send",
            workspace=str(workspace),
            session="remote-task",
            text="finish while the client is absent",
        )
        assert sent["process"]["state"] == "running"
        remote_pid = sent["process"]["pid"]

        # Dispatch has returned and retains no client request object: there is
        # no local client left, but the detached bridge still owns the core.
        running = _bridge_call(app, "processes.list")["processes"]
        assert running[0]["pid"] == remote_pid
        assert running[0]["state"] == "running"

        wait_for(
            lambda: _bridge_call(app, "processes.list")["processes"][0][
                "state"
            ]
            == "finished"
        )
        recovered = _bridge_call(
            app,
            "sessions.history",
            workspace=str(workspace),
            session="remote-task",
            after=0,
            limit=100,
        )
        assert [item["event"]["type"] for item in recovered["events"]] == [
            "user",
            "final",
            "turn_end",
        ]

        summary = _bridge_call(app, "sessions.list", workspace=str(workspace))[
            "session_items"
        ][0]
        assert summary["execution"] == "finished"
        assert summary["last_event_id"] == recovered["events"][-1]["seq"]
        assert web_state.read_cursor("remote", str(workspace), "remote-task") == 0
        assert summary["last_event_id"] > web_state.read_cursor(
            "remote", str(workspace), "remote-task"
        )
    finally:
        app.runtime.supervisor.close()

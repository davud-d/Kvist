from __future__ import annotations

from dataclasses import dataclass

from kernel.session_events import SessionJournal
from kernel.session_store import SessionStore

from kvist_web.controller import WebController
from kvist_web.events import EventBroker
from kvist_web.protocol import ConnectionState, ServerSpec
from kvist_web.storage import WebStateStore


@dataclass
class _Event:
    value: dict

    def to_dict(self):
        return self.value


def _record_finished(workspace, session: str) -> int:
    store = SessionStore(workspace)
    path = workspace / ".kvist" / "sessions" / f"{session}.events.jsonl"
    journal = SessionJournal(workspace, lambda: path, lambda _message: None)
    journal.begin_turn("do work")
    journal.emit(_Event({"type": "message", "markdown": "done"}))
    journal.end_turn()
    return store.history(session, after=0)["events"][-1]["seq"]


def test_event_broker_recovers_live_events_after_cursor():
    broker = EventBroker(capacity=10)
    first = broker.publish("local", "/w", "a", {"type": "status"})
    broker.publish("local", "/w", "b", {"type": "turn_end"})

    recovered = broker.after(first.id, timeout=0)

    assert [event.event["type"] for event in recovered] == ["turn_end"]


def test_event_broker_reports_epoch_and_capacity_gaps_explicitly():
    broker = EventBroker(capacity=2)
    epoch = broker.epoch
    broker.publish("local", "/w", "a", {"type": "status"})
    broker.publish("local", "/w", "a", {"type": "stream_chunk"})
    broker.publish("local", "/w", "a", {"type": "stream_chunk"})

    evicted = broker.read_after(0, epoch=epoch, timeout=0)
    assert evicted.reset is True
    assert evicted.reason == "history_gap"
    assert evicted.cursor == broker.cursor

    stale = broker.read_after(broker.cursor, epoch="old-controller", timeout=0)
    assert stale.reset is True
    assert stale.reason == "epoch_changed"
    assert stale.epoch == epoch


def test_bootstrap_exposes_transient_stream_epoch(tmp_path):
    controller = WebController(str(tmp_path), state=WebStateStore(tmp_path / "web-state.json"))
    stream = controller.bootstrap()["event_stream"]

    assert stream == {"cursor": controller.events.cursor, "epoch": controller.events.epoch}


def test_finished_session_is_unread_until_view_cursor_advances(tmp_path):
    SessionStore(tmp_path).create("finished")
    controller = WebController(
        str(tmp_path), state=WebStateStore(tmp_path / "web-state.json")
    )
    # Existing history is a baseline on first discovery, avoiding a first-run
    # inbox full of years-old sessions.
    controller.navigation()
    last_seq = _record_finished(tmp_path, "finished")

    item = controller.navigation()["servers"][0]["workspaces"][0]["sessions"][
        "session_items"
    ][0]
    assert item["unread"] is True
    assert item["last_event_id"] == last_seq

    controller.mark_read("local", str(tmp_path), "finished", last_seq)
    item = controller.navigation()["servers"][0]["workspaces"][0]["sessions"][
        "session_items"
    ][0]
    assert item["unread"] is False

    newer = _record_finished(tmp_path, "finished")
    assert newer > last_seq
    item = controller.navigation()["servers"][0]["workspaces"][0]["sessions"][
        "session_items"
    ][0]
    assert item["unread"] is True


def test_opening_unrelated_session_does_not_change_cursor(tmp_path):
    store = SessionStore(tmp_path)
    store.create("alpha")
    alpha_seq = _record_finished(tmp_path, "alpha")
    store.create("beta")
    _record_finished(tmp_path, "beta")
    state = WebStateStore(tmp_path / "web-state.json")
    controller = WebController(str(tmp_path), state=state)

    controller.mark_read("local", str(tmp_path), "beta", 99)

    assert state.read_cursor("local", str(tmp_path), "alpha") == 0
    assert state.read_cursor("local", str(tmp_path), "beta") == 99
    assert alpha_seq > 0


def test_navigation_persists_remote_session_failure_and_reconnects(tmp_path, monkeypatch):
    controller = WebController(str(tmp_path), state=WebStateStore(tmp_path / "web-state.json"))
    spec = ServerSpec(id="remote", name="remote", host="example", workspaces=[str(tmp_path)])

    class FailingRuntime:
        def sessions(self, _workspace):
            raise RuntimeError("SSH connection dropped")

    controller._servers[spec.id] = spec
    controller._runtimes[spec.id] = FailingRuntime()
    controller._connections[spec.id] = {"state": ConnectionState.CONNECTED}
    scheduled = []
    monkeypatch.setattr(controller, "_ensure_reconnect_loop", scheduled.append)

    controller.navigation()

    assert controller._connections[spec.id]["state"] == ConnectionState.FAILED
    assert scheduled == [spec.id]


def test_background_reconnect_retries_offline_remote_then_restores(tmp_path, monkeypatch):
    controller = WebController(str(tmp_path), state=WebStateStore(tmp_path / "web-state.json"))
    spec = ServerSpec(id="remote", name="remote", host="example")
    controller._servers[spec.id] = spec
    controller._connections[spec.id] = {"state": ConnectionState.OFFLINE}

    class Runtime:
        calls = 0

        def connect(self, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("network unavailable")
            return {"bridge": {"protocol_version": 1}}

        def list_workspaces(self):
            return []

    runtime = Runtime()
    monkeypatch.setattr(controller, "_remote", lambda _server_id: runtime)
    monkeypatch.setattr(controller, "sync_config", lambda _server_id, _selected: {"ok": True})

    class FastEvent:
        def is_set(self):
            return False

        def wait(self, _delay):
            return False

        def set(self):
            pass

    controller._closed = FastEvent()
    controller._background_reconnect(spec.id)

    assert runtime.calls == 2
    assert controller._connections[spec.id]["state"] == ConnectionState.CONNECTED

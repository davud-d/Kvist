import re
from pathlib import Path

from kvist_web.http_api import _api_route, client_root


CLIENT = client_root()


def test_client_has_keyboard_reconnect_and_accessibility_contracts():
    assert CLIENT.parent.name == "kvist_web"
    app = (CLIENT / "app.js").read_text(encoding="utf-8")
    api = (CLIENT / "utils" / "api.js").read_text(encoding="utf-8")
    html = (CLIENT / "index.html").read_text(encoding="utf-8")
    css = (CLIENT / "styles.css").read_text(encoding="utf-8")
    assert re.search(r'key\.toLowerCase\(\)\s*===\s*"k"', app)
    assert re.search(r'key\.toLowerCase\(\)\s*===\s*"p"', app)
    assert "new EventSource" in api and "recoverSelected" in app
    assert 'aria-live="polite"' in html and 'role="dialog"' in html
    assert "prefers-reduced-motion" in css


def test_ui_uses_one_versioned_api_adapter_without_backend_internals():
    javascript = {
        path.relative_to(CLIENT): path.read_text(encoding="utf-8")
        for path in CLIENT.rglob("*.js")
    }
    adapter = javascript[Path("utils/api.js")]
    other_modules = "\n".join(
        source for path, source in javascript.items() if path != Path("utils/api.js")
    )

    assert 'const API_ROOT = "/api/v1"' in adapter
    assert "/api/" not in other_modules
    for backend_internal in (
        "subprocess",
        "paramiko",
        "SSHTransport",
        "SessionStore",
        "WebStateStore",
        ".kvist/",
    ):
        assert backend_internal not in "\n".join(javascript.values())

    assert _api_route("/api/v1/history") == "/api/history"
    assert _api_route("/api/history") == "/api/history"
    assert _api_route("/api/v2/history") == "/api/v2/history"


def test_client_recovers_durable_events_without_second_transcript_or_stale_sse_cursor():
    app = (CLIENT / "app.js").read_text(encoding="utf-8")
    session = (CLIENT / "features" / "session.js").read_text(encoding="utf-8")
    api = (CLIENT / "utils" / "api.js").read_text(encoding="utf-8")
    docs = (CLIENT.parents[1] / "API.md").read_text(encoding="utf-8")

    # A journal-backed event wakes recovery rather than being rendered as a
    # second transcript. stream_start is deliberately idempotent so cumulative
    # transient chunks still have an immediate target.
    assert "const durable = envelope.durable === true" in session
    assert 'if (!durable || event.type === "stream_start") applyEvent(event)' in session
    assert "activeRecovery.dirty = true" in session
    assert "while (recovery.dirty && currentSelectionMatches(selection, generation))" in session
    assert "streamNodes.has(event.stream_id)" in session
    assert "agent.process_exited" in session and "agent.state_changed" in session

    # Live IDs have an epoch separate from the durable per-session sequence.
    assert "event_stream" in app and '"stream.reset"' in app
    assert "api.events(state.liveCursor, state.liveEpoch)" in app
    assert "event.remote !== true" in app
    assert "window.location.reload()" in app
    assert 'events: (after, epoch = "")' in api
    assert "durable: true" in docs and "stream.reset" in docs


def test_client_pending_input_and_navigation_stay_protocol_driven_and_session_scoped():
    app = (CLIENT / "app.js").read_text(encoding="utf-8")
    session = (CLIENT / "features" / "session.js").read_text(encoding="utf-8")
    navigation = (CLIENT / "features" / "navigation.js").read_text(encoding="utf-8")
    palette = (CLIENT / "features" / "palette.js").read_text(encoding="utf-8")

    assert 'input.type = event.kind === "secret" ? "password" : "text"' in session
    assert "event.remember_pattern" in session and "appendSubject(event.subject)" in session
    assert 'event.kind === "choice"' in session
    assert "selectionSnapshot()" in session and "isCurrentSelection(snapshot)" in session
    assert "inboxItems" in navigation and 'openPalette("inbox", paletteChoice)' in app
    assert 'mode === "inbox" ? inboxItems()' in palette
    assert "const generation = ++navigationGeneration" in app

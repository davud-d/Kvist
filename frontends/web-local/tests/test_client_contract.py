import http.client
import re
import threading
from pathlib import Path
from types import SimpleNamespace

from kvist_web.http_api import ControllerHTTPServer, _api_route, client_root


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
    blocks = (CLIENT / "features" / "blocks.js").read_text(encoding="utf-8")
    api = (CLIENT / "utils" / "api.js").read_text(encoding="utf-8")
    docs = (CLIENT.parents[1] / "API.md").read_text(encoding="utf-8")

    # A journal-backed event wakes recovery rather than being rendered as a
    # second transcript. stream_start is deliberately idempotent so cumulative
    # transient chunks still have an immediate target; reasoning_* are taken live
    # too, because the journal writes a thought only once it has ended.
    assert "const durable = envelope.durable === true" in session
    assert re.search(r"if \(reducesLive\(envelope\)\)\s+applyEvent\(event, \{ ts: envelope.ts \}\)", session)
    assert "const LIVE_DURABLE = new Set(['stream_start', 'reasoning_start', 'reasoning_chunk', 'reasoning_commit', 'hello'])" in blocks
    assert "activeRecovery.dirty = true" in session
    assert "while (recovery.dirty && currentSelectionMatches(selection, generation))" in session
    assert "look(s, 'streams', key)" in blocks and "env.seq == null" in blocks
    assert "agent.process_exited" in session and "agent.state_changed" in session

    # Live IDs have an epoch separate from the durable per-session sequence.
    assert "event_stream" in app and '"stream.reset"' in app
    assert "api.events(state.liveCursor, state.liveEpoch)" in app
    assert "event.remote !== true" in app
    assert "window.location.reload()" not in app
    assert "api.renewSession()" in app
    assert "DOMParser" in api
    assert 'events: (after, epoch = "")' in api
    assert "durable: true" in docs and "stream.reset" in docs


def test_client_pending_input_and_navigation_stay_protocol_driven_and_session_scoped():
    app = (CLIENT / "app.js").read_text(encoding="utf-8")
    session = (CLIENT / "features" / "session.js").read_text(encoding="utf-8")
    pending = (CLIENT / "features" / "pending.js").read_text(encoding="utf-8")
    navigation = (CLIENT / "features" / "navigation.js").read_text(encoding="utf-8")
    palette = (CLIENT / "features" / "palette.js").read_text(encoding="utf-8")

    # The panel of a request is built in pending.js; session.js sends the answer.
    assert "input.type = event.kind === 'secret' ? 'password' : 'text'" in pending
    assert "event.remember_pattern" in pending and "subjectDetails(event.subject)" in pending
    assert "event.kind === 'choice'" in pending
    assert "renderPending(pending, event" in session and "api.respond(snapshot.selection" in session
    assert "selectionSnapshot()" in session and "isCurrentSelection(snapshot)" in session
    assert "inboxItems" in navigation and 'openPalette("inbox", paletteChoice)' in app
    assert 'mode === "inbox" ? inboxItems()' in palette
    assert "const generation = ++navigationGeneration" in app


def test_mono_font_is_self_hosted_and_served_as_woff2():
    css = (CLIENT / "styles.css").read_text(encoding="utf-8")
    assert "font-display: swap" in css and '"JetBrains Mono"' in css
    assert not re.search(r"https?:|//", css.replace("/*", "").replace("*/", ""))

    server = ControllerHTTPServer(("127.0.0.1", 0), SimpleNamespace(), admin_token="test")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        connection.request("GET", "/assets/fonts/JetBrainsMono.woff2")
        font = connection.getresponse()
        body = font.read()
        assert font.status == 200
        assert font.getheader("Content-Type") == "font/woff2"
        assert body[:4] == b"wOF2"
        # The license ships in the wheel but only whitelisted suffixes are served.
        connection.request("GET", "/assets/fonts/OFL.txt")
        license_response = connection.getresponse()
        license_response.read()
        assert license_response.status == 404
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

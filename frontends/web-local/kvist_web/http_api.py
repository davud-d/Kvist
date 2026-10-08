"""HTTP/SSE boundary. OS processes and SSH stay behind WebController."""
from __future__ import annotations

import json
import mimetypes
import os
import threading
import time
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .controller import WebController
from .protocol import WEB_PROTOCOL_VERSION
from .security import BrowserAuth, BrowserSession, redact


MAX_BODY = 30 * 1024 * 1024
API_PREFIX = f"/api/v{WEB_PROTOCOL_VERSION}"
# Python before 3.14 knows .woff2 only through the system mime.types, which may be absent.
mimetypes.add_type("font/woff2", ".woff2")


def client_root() -> Path:
    """Return browser assets owned by the installed ``kvist_web`` package.

    Keeping the files next to this module avoids the platform-dependent
    ``data_files`` scheme: a ``pip --user`` installation places data below the
    user base while ``sys.prefix`` remains the system interpreter. PyInstaller
    extracts this same package-relative path from its bundled data.
    """
    root = Path(__file__).resolve().parent / "client"
    if not root.is_dir():
        raise RuntimeError("Kvist web client assets are missing from this installation.")
    return root


class ControllerHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address,
        controller: WebController,
        *,
        admin_token: str,
        external_token: str = "",
    ) -> None:
        self.controller = controller
        self.auth = BrowserAuth()
        self.admin_token = admin_token
        self.external_token = external_token
        self.public_origin = f"http://{address[0]}:{address[1]}"
        self.allowed_hosts = (
            {"127.0.0.1", "localhost", "::1"}
            if address[0] in {"127.0.0.1", "localhost", "::1"}
            else {address[0]}
        )
        super().__init__(address, ControllerHandler)


class ControllerHandler(BaseHTTPRequestHandler):
    server: ControllerHTTPServer
    protocol_version = "HTTP/1.1"
    server_version = "KvistWeb/1"
    sys_version = ""

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._json({"ok": True, "service": "kvist-web"})
            return
        if not self._valid_host():
            self._error(HTTPStatus.MISDIRECTED_REQUEST, "Request host was rejected.")
            return
        if not self._external_authorized():
            return
        if parsed.path in {"/", "/index.html"}:
            self._index()
            return
        if parsed.path.startswith("/assets/"):
            self._asset(parsed.path.removeprefix("/assets/"))
            return
        session = self._browser_session()
        if session is None:
            self._error(HTTPStatus.UNAUTHORIZED, "Browser session is missing.")
            return
        try:
            route = _api_route(parsed.path)
            if route == "/api/bootstrap":
                self._json(self.server.controller.bootstrap())
            elif route == "/api/navigation":
                self._json(self.server.controller.navigation())
            elif route == "/api/ssh-aliases":
                from .ssh.config import ssh_aliases
                self._json(ssh_aliases())
            elif route == "/api/configuration":
                self._json(self.server.controller.configuration(_one(parse_qs(parsed.query), "server", "local")))
            elif route == "/api/sessions":
                query = parse_qs(parsed.query)
                self._json(
                    self.server.controller.sessions(_one(query, "server"), _one(query, "workspace"))
                )
            elif route == "/api/history":
                query = parse_qs(parsed.query)
                self._json(
                    self.server.controller.history(
                        _one(query, "server"),
                        _one(query, "workspace"),
                        _one(query, "session"),
                        int(_one(query, "after", "0")),
                        min(5_000, max(1, int(_one(query, "limit", "1000")))),
                    )
                )
            elif route == "/api/changes":
                query = parse_qs(parsed.query)
                self._json(
                    self.server.controller.changes(
                        _one(query, "server"),
                        _one(query, "workspace"),
                        _one(query, "session", "") or None,
                    )
                )
            elif route == "/api/config-sync":
                self._json(self.server.controller.config_sync_preview())
            elif route == "/api/events":
                self._sse(parsed)
            else:
                self._error(HTTPStatus.NOT_FOUND, "Not found.")
        except Exception as error:
            self._error(HTTPStatus.BAD_REQUEST, redact(error))

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if not self._valid_host():
            self._error(HTTPStatus.MISDIRECTED_REQUEST, "Request host was rejected.")
            return
        if parsed.path == "/api/admin/stop":
            if self.headers.get("Authorization") != f"Bearer {self.server.admin_token}":
                self._error(HTTPStatus.UNAUTHORIZED, "Not authorized.")
                return
            self._json({"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        if not self._external_authorized():
            return
        session = self._browser_session()
        if session is None or not self._valid_origin() or not self.server.auth.verify_csrf(
            session, self.headers.get("X-Kvist-CSRF")
        ):
            self._error(HTTPStatus.FORBIDDEN, "Request origin or CSRF token was rejected.")
            return
        try:
            body = self._read_json()
            route = _api_route(parsed.path)
            controller = self.server.controller
            if route == "/api/servers":
                result = controller.add_server(body)
            elif route in {"/api/configuration", "/api/configuration/models"}:
                result = controller.configuration(_text(body, "server_id"), "models" if route.endswith("/models") else "save", body)
            elif route == "/api/command":
                result = controller.command(_text(body, "server_id"), _text(body, "workspace"),
                                            _text(body, "session"), _text(body, "name"), body.get("args", []))
            elif route == "/api/servers/connect":
                result = controller.connect_server(
                    _text(body, "server_id"),
                    str(body.get("fingerprint") or "") or None,
                    run_setup=True,
                    attempt_id=body.get("attempt_id", ""),
                )
            elif route == "/api/workspaces":
                result = controller.add_workspace(
                    _text(body, "server_id"), _text(body, "path")
                )
            elif route == "/api/sessions":
                name = body.get("name")
                result = controller.create_session(
                    _text(body, "server_id"),
                    _text(body, "workspace"),
                    str(name).strip() if isinstance(name, str) and name.strip() else None,
                )
            elif route == "/api/send":
                result = controller.send(
                    _text(body, "server_id"),
                    _text(body, "workspace"),
                    _text(body, "session"),
                    _text(body, "text"),
                )
            elif route == "/api/stop":
                result = controller.stop(
                    _text(body, "server_id"),
                    _text(body, "workspace"),
                    _text(body, "session"),
                )
            elif route == "/api/respond":
                response = body.get("response")
                if not isinstance(response, dict):
                    raise ValueError("Response payload is required.")
                result = controller.respond(
                    _text(body, "server_id"),
                    _text(body, "workspace"),
                    _text(body, "session"),
                    response,
                )
            elif route == "/api/read":
                controller.mark_read(
                    _text(body, "server_id"),
                    _text(body, "workspace"),
                    _text(body, "session"),
                    int(body.get("seq", 0)),
                )
                result = {"ok": True}
            elif route == "/api/config-sync":
                selected = body.get("credentials", [])
                if not isinstance(selected, list) or not all(
                    isinstance(item, str) for item in selected
                ):
                    raise ValueError("credentials must be a list of ids")
                result = controller.sync_config(_text(body, "server_id"), selected)
            else:
                self._error(HTTPStatus.NOT_FOUND, "Not found.")
                return
            self._json(result)
        except Exception as error:
            self._error(HTTPStatus.BAD_REQUEST, redact(error))

    def _index(self) -> None:
        session = self.server.auth.create()
        template = (client_root() / "index.html").read_text(encoding="utf-8")
        content = template.replace("{{KVIST_CSRF}}", session.csrf).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header(
            "Set-Cookie",
            f"kvist_session={session.cookie}; HttpOnly; SameSite=Strict; Path=/",
        )
        self._security_headers()
        self.end_headers()
        self.wfile.write(content)

    def _asset(self, name: str) -> None:
        root = client_root().resolve()
        if name == "utils/image-attachments.js" and not (root / name).exists():
            import kernel
            root = Path(kernel.__file__).resolve().parent.parent / "frontends/gui/electron/renderer"
            name = "image-attachments.js"
        try:
            target = (root / unquote(name)).resolve(strict=True)
        except OSError:
            self._error(HTTPStatus.NOT_FOUND, "Not found.")
            return
        if root not in target.parents or target.suffix not in {".js", ".css", ".svg", ".png", ".woff2"}:
            self._error(HTTPStatus.NOT_FOUND, "Not found.")
            return
        content = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mimetypes.guess_type(name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache")
        self._security_headers()
        self.end_headers()
        self.wfile.write(content)

    def _sse(self, parsed) -> None:
        query = parse_qs(parsed.query)
        cursor = int(self.headers.get("Last-Event-ID") or _one(query, "after", "0"))
        epoch = _one(query, "epoch", "") or None
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self._security_headers()
        self.end_headers()
        try:
            while True:
                replay = self.server.controller.events.read_after(
                    cursor, epoch=epoch, timeout=20
                )
                if replay.reset:
                    # Broker ids are intentionally transient and bounded.  A
                    # stale/evicted cursor must trigger durable journal
                    # recovery, never a silent wait or drop.
                    cursor = replay.cursor
                    epoch = replay.epoch
                    reset = {
                        "id": cursor,
                        "server_id": "",
                        "workspace": "",
                        "session": "",
                        "event": {
                            "type": "stream.reset",
                            "epoch": epoch,
                            "reason": replay.reason,
                        },
                        "ts": time.time(),
                        "durable": False,
                    }
                    encoded = json.dumps(reset, ensure_ascii=False, separators=(",", ":"))
                    self.wfile.write(f"id: {cursor}\ndata: {encoded}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    continue
                if not replay.events:
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
                    continue
                for envelope in replay.events:
                    cursor = envelope.id
                    encoded = json.dumps(
                        envelope.to_dict(), ensure_ascii=False, separators=(",", ":")
                    )
                    self.wfile.write(f"id: {cursor}\ndata: {encoded}\n\n".encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("Invalid request length.") from error
        if length < 0 or length > MAX_BODY:
            raise ValueError("Request body is too large.")
        value = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(value, dict):
            raise ValueError("Request body must be a JSON object.")
        return value

    def _browser_session(self) -> BrowserSession | None:
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        morsel = cookie.get("kvist_session")
        return self.server.auth.resolve(morsel.value if morsel else None)

    def _valid_origin(self) -> bool:
        origin = self.headers.get("Origin")
        try:
            parsed = urlparse(origin or "")
        except ValueError:
            return False
        return (
            parsed.scheme == "http"
            and parsed.hostname in self.server.allowed_hosts
            and parsed.port == self.server.server_address[1]
        )

    def _valid_host(self) -> bool:
        raw = self.headers.get("Host", "")
        try:
            parsed = urlparse(f"//{raw}")
        except ValueError:
            return False
        port = parsed.port or 80
        return (
            parsed.hostname in self.server.allowed_hosts
            and port == self.server.server_address[1]
        )

    def _external_authorized(self) -> bool:
        expected = self.server.external_token
        if not expected:
            return True
        header = self.headers.get("Authorization", "")
        supplied = ""
        if header.startswith("Bearer "):
            supplied = header[7:]
        elif header.startswith("Basic "):
            try:
                import base64

                decoded = base64.b64decode(header[6:]).decode("utf-8")
                supplied = decoded.partition(":")[2]
            except Exception:
                supplied = ""
        if __import__("hmac").compare_digest(supplied, expected):
            return True
        body = b'{"ok":false,"error":"Access token required."}'
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("WWW-Authenticate", 'Basic realm="Kvist Web", charset="UTF-8"')
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)
        return False

    def _json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if self.close_connection:
            self.send_header("Connection", "close")
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: HTTPStatus, message: str) -> None:
        # A rejected POST may still have an unread body (e.g. CSRF or origin
        # failure). It cannot become the next request line on a keep-alive socket.
        if self.command == "POST":
            self.close_connection = True
        self._json({"ok": False, "error": message}, status)

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        )

    def log_message(self, format: str, *args) -> None:
        return


def _one(query: dict[str, list[str]], key: str, default: str | None = None) -> str:
    values = query.get(key)
    if values and values[0]:
        return unquote(values[0])
    if default is not None:
        return default
    raise ValueError(f"{key} is required.")


def _text(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"{key} is required.")
    return item.strip()


def _api_route(path: str) -> str:
    """Map the stable versioned browser API onto the v1 controller routes.

    The original unversioned paths remain accepted for compatibility with an
    already-open page during an upgrade. Unknown future versions do not fall
    through to v1.
    """
    if path.startswith(API_PREFIX + "/"):
        return "/api/" + path.removeprefix(API_PREFIX + "/")
    return path

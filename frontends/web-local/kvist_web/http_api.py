"""HTTP/SSE boundary. OS processes and SSH stay behind WebController."""
from __future__ import annotations

import base64
import hmac
import json
import logging
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
from .devices import DeviceStore
from .lan import is_private_host
from .pairing import Pairing, PairingError
from .protocol import WEB_PROTOCOL_VERSION
from .security import BrowserAuth, BrowserSession, redact


_LOG = logging.getLogger("kvist.web.http")
MAX_BODY = 30 * 1024 * 1024
# A phone that is not trusted yet may send this much, no more.
MAX_PAIR_BODY = 4096
PRESENCE_BEAT_SECONDS = 5.0
# A phone works inside the folders where an agent runs, nowhere else: it cannot add a folder or another
# computer, and it cannot reach the settings that copy credentials to one.
PHONE_FORBIDDEN = frozenset({"/api/workspaces", "/api/servers", "/api/servers/connect", "/api/config-sync", "/api/ssh-aliases"})
SCOPED_ROUTES = frozenset({
    "/api/sessions", "/api/history", "/api/changes", "/api/command", "/api/send", "/api/stop", "/api/control", "/api/respond", "/api/read",
})


def resolve_folder(path: object) -> str:
    return str(Path(str(path)).expanduser().resolve())


def restrict_navigation(navigation: dict[str, Any], scope: set[str]) -> dict[str, Any]:
    """The navigation a phone may see: this computer, and only the folders in scope."""
    servers = []
    for entry in navigation["servers"]:
        if entry["server"]["id"] != "local":
            continue
        folders = [folder for folder in entry["workspaces"] if resolve_folder(folder["path"]) in scope]
        servers.append({**entry, "workspaces": folders})
    inbox = [item for item in navigation.get("inbox", []) if item["server_id"] == "local" and resolve_folder(item["workspace"]) in scope]
    return {**navigation, "servers": servers, "inbox": inbox}
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


class Presence:
    """Cores that keep a connection open to say "an agent runs here"; the controller exits when none is left."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._count = 0
        self._idle_since = time.monotonic()
        self._folders: dict[str, int] = {}

    def enter(self, folder: str = "") -> None:
        with self._lock:
            self._count += 1
            if folder:
                self._folders[folder] = self._folders.get(folder, 0) + 1

    def leave(self, folder: str = "") -> None:
        with self._lock:
            self._count -= 1
            if folder:
                self._folders[folder] -= 1
                if self._folders[folder] <= 0:
                    del self._folders[folder]
            if self._count == 0:
                self._idle_since = time.monotonic()

    def folders(self) -> set[str]:
        with self._lock:
            return set(self._folders)

    def idle_seconds(self) -> float:
        with self._lock:
            return time.monotonic() - self._idle_since if self._count == 0 else 0.0


class ControllerHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address,
        controller: WebController,
        *,
        admin_token: str,
        external_token: str = "",
        lan: bool = False,
        devices: DeviceStore | None = None,
        pairing: Pairing | None = None,
    ) -> None:
        self.controller = controller
        self.auth = BrowserAuth()
        self.admin_token = admin_token
        self.external_token = external_token
        self.lan = lan
        self.devices = devices
        self.pairing = pairing
        self.presence = Presence()
        # Folders open to phones whatever the agents do: the one a person started `kvist-web --lan` in.
        self.fixed_folders: set[str] = set()
        self.public_origin = f"http://{address[0]}:{address[1]}"
        self.allowed_hosts = (
            {"127.0.0.1", "localhost", "::1"}
            if address[0] in {"127.0.0.1", "localhost", "::1"}
            else {address[0]}
        )
        super().__init__(address, ControllerHandler)

    def phone_scope(self) -> set[str] | None:
        """The folders a phone may use, or None when this server is not serving phones."""
        if not self.lan:
            return None
        return self.fixed_folders | self.presence.folders()

    def host_allowed(self, hostname: str | None) -> bool:
        # On a local network the computer's address is not known in advance (DHCP, several adapters), so
        # any private IP literal is accepted; a name never is, which is what a rebinding page would use.
        if self.lan and is_private_host(hostname):
            return True
        return hostname in self.allowed_hosts


class ControllerHandler(BaseHTTPRequestHandler):
    server: ControllerHTTPServer
    protocol_version = "HTTP/1.1"
    server_version = "KvistWeb/1"
    sys_version = ""

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._json({"ok": True, "service": "kvist-web", "lan": self.server.lan})
            return
        if not self._valid_host():
            self._refuse_host()
            return
        if parsed.path == "/pair/status" and self.server.pairing is not None:
            self._pair_status(parsed)
            return
        if parsed.path == "/api/admin/presence":
            self._presence()
            return
        if not self._external_authorized():
            return
        if parsed.path in {"/", "/index.html"}:
            self._serve_files(self._index)
            return
        if parsed.path in {"/m", "/m/"}:
            self._serve_files(self._index, "mobile/index.html")
            return
        if parsed.path.startswith("/assets/"):
            self._serve_files(self._asset, parsed.path.removeprefix("/assets/"))
            return
        session = self._browser_session()
        if session is None:
            self._error(HTTPStatus.UNAUTHORIZED, "Browser session is missing.")
            return
        try:
            route = _api_route(parsed.path)
            self._enforce_scope(route, parse_qs(parsed.query))
            if route == "/api/bootstrap":
                value = self.server.controller.bootstrap()
                scope = self.server.phone_scope()
                if scope is not None:
                    value = {**value, "navigation": restrict_navigation(value["navigation"], scope)}
                self._json(value)
            elif route == "/api/navigation":
                value = self.server.controller.navigation()
                scope = self.server.phone_scope()
                self._json(value if scope is None else restrict_navigation(value, scope))
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
        except PermissionError as error:
            self._error(HTTPStatus.FORBIDDEN, str(error))
        except Exception as error:
            self._error(HTTPStatus.BAD_REQUEST, redact(error))

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if not self._valid_host():
            self._refuse_host()
            return
        if parsed.path == "/pair/request" and self.server.pairing is not None:
            self._pair_request()
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
            self._enforce_scope(route, body)
            controller = self.server.controller
            if route == "/api/servers":
                result = controller.add_server(body)
            elif route in {"/api/configuration", "/api/configuration/models"}:
                result = controller.configuration(_text(body, "server_id"), "models" if route.endswith("/models") else "save", body)
            elif route == "/api/configuration/chatgpt-auth":
                if _text(body, "server_id") != "local":
                    raise ValueError("Sign in on the remote computer itself; the browser callback uses its loopback address.")
                result = controller.configuration("local", "chatgpt-auth", body)
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
            elif route == "/api/control":
                agent_id = body.get("agent_id")
                result = controller.control(
                    _text(body, "server_id"),
                    _text(body, "workspace"),
                    _text(body, "session"),
                    _text(body, "kind"),
                    agent_id if isinstance(agent_id, str) else "",
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
        except PermissionError as error:
            self._error(HTTPStatus.FORBIDDEN, str(error))
        except Exception as error:
            self._error(HTTPStatus.BAD_REQUEST, redact(error))

    def _index(self, page: str = "index.html") -> None:
        session = self.server.auth.create()
        template = (client_root() / page).read_text(encoding="utf-8")
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
            root = Path(kernel.__file__).resolve().parent.parent / "frontends/gui/electron-legacy/renderer"
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
                scope = self.server.phone_scope()
                for envelope in replay.events:
                    cursor = envelope.id
                    if scope is not None and envelope.workspace and resolve_folder(envelope.workspace) not in scope:
                        continue
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
            and self.server.host_allowed(parsed.hostname)
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
            self.server.host_allowed(parsed.hostname)
            and port == self.server.server_address[1]
        )

    def _supplied_token(self) -> str:
        header = self.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            return header[7:]
        if header.startswith("Basic "):
            try:
                return base64.b64decode(header[6:]).decode("utf-8").partition(":")[2]
            except ValueError:
                return ""
        return ""

    def _external_authorized(self) -> bool:
        expected = self.server.external_token
        devices = self.server.devices
        if not expected and devices is None:
            return True
        supplied = self._supplied_token()
        if expected and hmac.compare_digest(supplied, expected):
            return True
        if devices is not None and supplied and devices.verify(supplied) is not None:
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

    def _enforce_scope(self, route: str, fields: dict[str, Any]) -> None:
        """A phone is held to the folders where an agent runs; anything else is refused before it reaches the controller."""
        scope = self.server.phone_scope()
        if scope is None:
            return
        if route in PHONE_FORBIDDEN:
            raise PermissionError("The phone cannot do this; do it on the computer.")
        if route not in SCOPED_ROUTES:
            return
        # GET fields are lists (a query string), POST fields are plain values.
        def field(*names: str) -> str:
            for name in names:
                value = fields.get(name)
                if isinstance(value, list):
                    value = value[0] if value else ""
                if value:
                    return str(value)
            return ""

        if field("server_id", "server") not in ("local", ""):
            raise PermissionError("The phone works with this computer only.")
        if resolve_folder(field("workspace")) not in scope:
            raise PermissionError("This folder is not open to the phone: no agent runs in it.")

    def _serve_files(self, action, *args) -> None:
        """Interface files that cannot be read answer 500 and say so; an unhandled error would drop the connection with no word.

        A controller that outlives the replacement of the program it runs from can lose its files, and a phone
        that gets no answer at all can only say "cannot reach the computer"."""
        try:
            action(*args)
        except (RuntimeError, FileNotFoundError, PermissionError) as error:
            _LOG.error("cannot read the interface files: %s", error, extra={"component": "web"})
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "The interface files are missing on the computer. Restart Kvist there.")

    def _refuse_host(self) -> None:
        # The usual cause is a phone that reaches this computer by an address that is not a private one (a VPN
        # address, a name); the log is the only place anyone can see it.
        _LOG.info("refused a request for host %r from %s", self.headers.get("Host", ""), self.client_address[0], extra={"component": "web"})
        self._error(HTTPStatus.MISDIRECTED_REQUEST, "Request host was rejected.")

    def _pair_request(self) -> None:
        address = self.client_address[0]
        if not is_private_host(address):
            self._error(HTTPStatus.FORBIDDEN, "Only the local network may ask.")
            return
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= MAX_PAIR_BODY:
            self._error(HTTPStatus.BAD_REQUEST, "bad_request")
            return
        try:
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("not an object")
            request_id = self.server.pairing.request(body.get("device_id"), body.get("name"), address)
        except (ValueError, PairingError) as error:
            reason = error.reason if isinstance(error, PairingError) else "bad_request"
            self._json({"ok": False, "error": reason}, HTTPStatus.BAD_REQUEST if reason == "bad_request" else HTTPStatus.TOO_MANY_REQUESTS)
            return
        _LOG.info("a phone at %s asks to be trusted", address, extra={"component": "web"})
        self._json({"ok": True, "request_id": request_id})

    def _pair_status(self, parsed) -> None:
        if not is_private_host(self.client_address[0]):
            self._error(HTTPStatus.FORBIDDEN, "Only the local network may ask.")
            return
        self._json({"ok": True, **self.server.pairing.status(_one(parse_qs(parsed.query), "request_id", ""))})

    def _register_folder(self, query: dict[str, list[str]]) -> str:
        """The folder the core that connected works in becomes open to phones for as long as it stays connected."""
        folder = _one(query, "workspace", "")
        if not folder:
            return ""
        try:
            self.server.controller.add_workspace("local", folder)
        except (OSError, ValueError) as error:
            _LOG.warning("cannot open %s to phones: %s", folder, error)
            return ""
        return resolve_folder(folder)

    def _presence(self) -> None:
        if self.headers.get("Authorization") != f"Bearer {self.server.admin_token}":
            self._error(HTTPStatus.UNAUTHORIZED, "Not authorized.")
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        folder = self._register_folder(parse_qs(urlparse(self.path).query))
        self.server.presence.enter(folder)
        try:
            while True:
                self.wfile.write(b"\n")
                self.wfile.flush()
                time.sleep(PRESENCE_BEAT_SECONDS)
        except OSError:
            # The core closed the connection or died: this is how presence ends, not a failure.
            pass
        finally:
            self.server.presence.leave(folder)

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
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: http://127.0.0.1:*; "
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

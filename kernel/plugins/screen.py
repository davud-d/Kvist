# -*- coding: utf-8 -*-
"""The agent's virtual screen as the person sees it: framebuffer reading and a local viewer.

Xvfb in a skill's sandbox writes its framebuffer to an XWD file (``-fbdir``) in
a core-owned directory. The core maps that file and encodes JPEG frames from
it, so the GUI preview, the enlarged view and the browser tab all show the one
screen the agent works on — nothing starts a second browser to draw a preview.

The viewer listens on 127.0.0.1 only. Loopback is not authorization: every
request needs the per-screen token (in the first URL, then exchanged for an
HttpOnly cookie) and a Host header naming this listener, which defeats DNS
rebinding from a web page. Viewing never controls the screen.
"""
from __future__ import annotations

import hmac
import html
import io
import mmap
import os
import secrets
import struct
import threading
import time
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

FRAME_FILE = "Xvfb_screen0"
# A 1280x800 frame takes ~10 ms to read and encode, so 30 fps costs ~30% of one core while a viewer is open
# and 60 fps ~60%. KVIST_SCREEN_FPS overrides it (1..60).
DEFAULT_FPS = 30.0
PREVIEW_WIDTH = 1280
_COOKIE = "kvist_screen"
_STATES = frozenset({"starting", "running", "paused", "unavailable", "closed"})


def _max_fps() -> float:
    try:
        return min(max(float(os.environ.get("KVIST_SCREEN_FPS", DEFAULT_FPS)), 1.0), 60.0)
    except ValueError:
        return DEFAULT_FPS


class FramebufferError(RuntimeError):
    """The framebuffer file is missing or not an XWD image this reader understands."""


def read_frame(fb_path: Path):
    """The current screen as a PIL image (RGB)."""
    from PIL import Image

    try:
        with open(fb_path, "rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
            header = _xwd_header(data)
            width, height = header["width"], header["height"]
            offset = header["header_size"] + header["ncolors"] * 12
            size = header["bytes_per_line"] * height
            if offset + size > len(data):
                raise FramebufferError("framebuffer file is shorter than its header says")
            raw = bytes(data[offset:offset + size])
    except (OSError, ValueError) as error:
        raise FramebufferError(f"framebuffer unavailable: {error}") from None
    if header["bits_per_pixel"] != 32:
        raise FramebufferError(f"unsupported framebuffer depth {header['bits_per_pixel']}")
    mode = "BGRX" if header["byte_order"] == 0 else "XRGB"
    return Image.frombuffer("RGB", (width, height), raw, "raw", mode, header["bytes_per_line"], 1)


def frame_jpeg(fb_path: Path, max_width: int = PREVIEW_WIDTH, quality: int = 70) -> bytes:
    image = read_frame(fb_path)
    if image.width > max_width:
        image = image.resize((max_width, round(image.height * max_width / image.width)))
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=quality)
    return output.getvalue()


def frame_png(fb_path: Path) -> bytes:
    output = io.BytesIO()
    read_frame(fb_path).save(output, format="PNG")
    return output.getvalue()


def _xwd_header(data) -> dict[str, int]:
    if len(data) < 100:
        raise FramebufferError("framebuffer file has no XWD header")
    for endian in (">", "<"):
        fields = struct.unpack(endian + "25I", data[:100])
        if fields[1] == 7:  # XWD file_version
            break
    else:
        raise FramebufferError("framebuffer file is not XWD version 7")
    names = ("header_size", "file_version", "pixmap_format", "depth", "width", "height", "xoffset",
             "byte_order", "bitmap_unit", "bitmap_bit_order", "bitmap_pad", "bits_per_pixel",
             "bytes_per_line", "visual_class", "red_mask", "green_mask", "blue_mask", "bits_per_rgb",
             "colormap_entries", "ncolors")
    return dict(zip(names, fields))


class ScreenViewer:
    """A loopback HTTP viewer of one framebuffer; ``url`` carries the access token."""

    def __init__(self, fb_dir: Path, *, title: str = "Kvist") -> None:
        self.fb_path = Path(fb_dir) / FRAME_FILE
        self.title = title
        self.token = secrets.token_urlsafe(32)
        self._session = secrets.token_urlsafe(32)
        self.state = "starting"
        self.detail = ""
        self._server: ThreadingHTTPServer | None = None
        self._closed = threading.Event()

    @property
    def port(self) -> int:
        assert self._server is not None
        return self._server.server_address[1]

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def url(self) -> str:
        """The page to open in a browser; the token is exchanged for a cookie on first load."""
        return f"{self.base}/?t={self.token}"

    @property
    def stream_url(self) -> str:
        return f"{self.base}/stream.mjpeg?t={self.token}"

    @property
    def frame_url(self) -> str:
        return f"{self.base}/frame.jpg?t={self.token}"

    def set_state(self, state: str, detail: str = "") -> None:
        if state in _STATES:
            self.state = state
            self.detail = detail

    def start(self) -> None:
        viewer = self

        class Handler(_ViewerHandler):
            owner = viewer

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, name="kvist-screen-viewer", daemon=True).start()

    def close(self) -> str | None:
        self.state = "closed"
        self._closed.set()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        return None

    def authorized(self, query_token: str, cookie_header: str) -> bool:
        if query_token and hmac.compare_digest(query_token, self.token):
            return True
        cookie = SimpleCookie()
        try:
            cookie.load(cookie_header or "")
        except CookieError:
            # A malformed Cookie header carries no session, so it is refused like a missing one.
            return False
        morsel = cookie.get(_COOKIE)
        return morsel is not None and hmac.compare_digest(morsel.value, self._session)

    def host_allowed(self, host: str, origin: str) -> bool:
        allowed = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
        if host not in allowed:
            return False
        return not origin or origin in {f"http://{item}" for item in allowed} or origin == "null"


class _ViewerHandler(BaseHTTPRequestHandler):
    owner: ScreenViewer
    server_version = "KvistScreen/1"

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - BaseHTTPRequestHandler's signature
        return

    def do_GET(self) -> None:  # noqa: N802 - http.server naming
        viewer = self.owner
        parts = urlsplit(self.path)
        token = (parse_qs(parts.query).get("t") or [""])[0]
        if not viewer.host_allowed(self.headers.get("Host", ""), self.headers.get("Origin", "")):
            self._plain(HTTPStatus.FORBIDDEN, "wrong host")
            return
        if not viewer.authorized(token, self.headers.get("Cookie", "")):
            self._plain(HTTPStatus.UNAUTHORIZED, "this screen link has expired or is not yours")
            return
        if parts.path == "/":
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", "/view")
            self.send_header("Set-Cookie", f"{_COOKIE}={viewer._session}; HttpOnly; SameSite=Strict; Path=/")
            self._common_headers()
            self.end_headers()
        elif parts.path == "/view":
            self._page()
        elif parts.path == "/frame.jpg":
            self._frame()
        elif parts.path == "/stream.mjpeg":
            self._stream()
        elif parts.path == "/state.json":
            body = ('{"state": "%s", "detail": %s}' % (viewer.state, _json_str(viewer.detail))).encode()
            self._send(HTTPStatus.OK, "application/json", body)
        else:
            self._plain(HTTPStatus.NOT_FOUND, "not found")

    def _page(self) -> None:
        title = html.escape(self.owner.title)
        body = f"""<!doctype html><html><head><meta charset="utf-8"><title>{title}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>html,body{{margin:0;height:100%;background:#111;color:#ddd;font:14px system-ui,sans-serif}}
main{{display:flex;flex-direction:column;height:100%}}header{{padding:8px 12px}}
img{{flex:1;min-height:0;object-fit:contain;width:100%;background:#000}}</style></head>
<body><main><header>{title} · <span id="s">…</span> · view only</header>
<img src="/stream.mjpeg" alt="{title}"></main>
<script>async function poll(){{try{{const r=await fetch('/state.json',{{cache:'no-store'}});const j=await r.json();
document.getElementById('s').textContent=j.state+(j.detail?' — '+j.detail:'')}}catch(e){{document.getElementById('s').textContent='closed'}}
setTimeout(poll,1000)}}poll()</script></body></html>""".encode("utf-8")
        self._send(HTTPStatus.OK, "text/html; charset=utf-8", body)

    def _frame(self) -> None:
        try:
            body = frame_jpeg(self.owner.fb_path)
        except FramebufferError as error:
            self._plain(HTTPStatus.SERVICE_UNAVAILABLE, str(error))
            return
        self._send(HTTPStatus.OK, "image/jpeg", body)

    def _stream(self) -> None:
        boundary = "kvistframe"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={boundary}")
        self._common_headers()
        self.end_headers()
        viewer = self.owner
        while not viewer._closed.is_set():
            started = time.monotonic()
            try:
                frame = frame_jpeg(viewer.fb_path)
            except FramebufferError:
                frame = b""
            if frame:
                try:
                    self.wfile.write(f"--{boundary}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(frame)}\r\n\r\n"
                                     .encode("ascii") + frame + b"\r\n")
                    self.wfile.flush()
                except OSError:
                    return
            viewer._closed.wait(max(0.0, 1.0 / _max_fps() - (time.monotonic() - started)))

    def _plain(self, status: HTTPStatus, text: str) -> None:
        self._send(status, "text/plain; charset=utf-8", (text + "\n").encode("utf-8"))

    def _send(self, status: HTTPStatus, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self._common_headers()
        self.end_headers()
        self.wfile.write(body)

    def _common_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; frame-ancestors 'none'")


def _json_str(value: str) -> str:
    import json

    return json.dumps(value)

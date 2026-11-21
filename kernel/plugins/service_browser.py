"""Browser login primitives shared with the standalone Telegram helper."""
from __future__ import annotations

import json
import os
import queue
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


def open_browser(url: str) -> bool:
    """Open the OS default browser without a shell; False means show a manual link."""
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Expected a browser HTTP(S) URL")
    try:
        if sys.platform == "win32":
            os.startfile(url)
            return True
        command = "/usr/bin/open" if sys.platform == "darwin" else shutil.which("xdg-open")
        if command:
            result = subprocess.run([command, url], stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            if result.returncode == 0:
                return True
        return bool(webbrowser.open(url, new=2))
    except (OSError, subprocess.SubprocessError, webbrowser.Error):
        return False


def private_json(path: Path, value: dict) -> None:
    """Replace credentials atomically without leaving a backup of old tokens."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".auth-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class LoginPage:
    """Short-lived loopback page. No external assets, logging or cross-origin access."""

    def __init__(self, callback=None):
        self.path = "/" + secrets.token_urlsafe(32)
        self.html = "<h1>Kvist</h1><p>Preparing login…</p>"
        # ask() blocks until the person submits a form; the POST waits for the next page so the browser
        # lands on the following step (or an error) instead of a stale form.
        self._submitted = queue.Queue()
        self._shown = threading.Event()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                if self.headers.get("Host") != owner.host:
                    self.send_error(403)
                    return
                parsed = urlsplit(self.path)
                if parsed.path != owner.path:
                    self.send_error(404)
                    return
                if callback is not None and parsed.query:
                    status, content = callback(parsed.query)
                else:
                    status, content = 200, owner.html
                self.respond(status, content)

            def do_POST(self):
                if self.headers.get("Host") != owner.host or urlsplit(self.path).path != owner.path:
                    self.send_error(403)
                    return
                length = int(self.headers.get("Content-Length") or 0)
                if length > 8192:
                    self.send_error(413)
                    return
                form = parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
                owner._shown.clear()
                owner._submitted.put({key: values[0] for key, values in form.items()})
                owner._shown.wait(timeout=60)
                self.respond(200, owner.html)

            def respond(self, status, content):
                data = ("<!doctype html><html><meta charset='utf-8'><title>Kvist login</title>"
                        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                        "<body>" + content + "</body></html>").encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; "
                                 "form-action 'self'; frame-ancestors 'none'")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.host = f"127.0.0.1:{self.server.server_port}"
        self.url = f"http://{self.host}{self.path}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def show(self, html: str) -> None:
        self.html = html
        self._shown.set()

    def ask(self, html: str, timeout: float = 600) -> dict:
        """Show a form and block until it is submitted; the caller must run this off the event loop."""
        self.show(html)
        try:
            return self._submitted.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError("Login page timed out") from None

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def launch(url: str) -> None:
    opened = open_browser(url)
    print("Browser opened." if opened else "Could not open the browser automatically.", file=sys.stderr)
    print(f"Login link (open on this computer): {url}", file=sys.stderr)

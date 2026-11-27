# -*- coding: utf-8 -*-
"""Finding the person's browser: an address they gave, a DevToolsActivePort file, or port 9222.

Chromium family browsers write ``DevToolsActivePort`` into their profile folder
while remote debugging is on (``--remote-debugging-port``, or the switch on
chrome://inspect/#remote-debugging), so it is checked first: port 9222 is also
what Electron apps such as Spotify open, and those are not the person's browser.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from kernel.browser_mcp.drivers import BidiDriver, CdpDriver, DriverError
from kernel.browser_mcp.websocket import WebSocketError

DEFAULT_PORT = 9222
# Desktop apps built on Chromium open the same debugging port; their user agent names them.
_NOT_BROWSERS = ("Electron/", "Spotify/", "Discord/", "Slack/", "Code/", "Teams/")
_PROFILE_DIRS = {
    "linux": ("google-chrome", "google-chrome-beta", "google-chrome-unstable", "chromium",
              "BraveSoftware/Brave-Browser", "microsoft-edge", "vivaldi", "yandex-browser"),
    "darwin": ("Google/Chrome", "Google/Chrome Beta", "Chromium", "BraveSoftware/Brave-Browser",
               "Microsoft Edge", "Vivaldi", "Yandex/YandexBrowser"),
    "win32": ("Google/Chrome/User Data", "Chromium/User Data", "BraveSoftware/Brave-Browser/User Data",
              "Microsoft/Edge/User Data", "Vivaldi/User Data", "Yandex/YandexBrowser/User Data"),
}

HOW_TO_START = (
    "Kvist found no browser with remote debugging. Ask the person to start one of these and try again:\n"
    "  Chrome/Chromium/Brave/Edge: open chrome://inspect/#remote-debugging and turn it on, or start the browser "
    "with --remote-debugging-port=9222 (Chrome 136+ honours the flag only with a non-default --user-data-dir);\n"
    "  Firefox: start it with --remote-debugging-port 9222.\n"
    "Then the server finds it by itself; another port goes in --browser-endpoint."
)


class EndpointError(Exception):
    """No browser could be reached; the message says what to do."""


@dataclass(frozen=True, slots=True)
class Candidate:
    host: str
    port: int
    ws_path: str  # known browser WebSocket path, or "" to discover it


def _config_root() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def port_files() -> list[Path]:
    root = _config_root()
    names = _PROFILE_DIRS.get(sys.platform, _PROFILE_DIRS["linux"])
    found = [root / name / "DevToolsActivePort" for name in names]
    existing = [path for path in found if path.is_file()]
    return sorted(existing, key=lambda path: path.stat().st_mtime, reverse=True)


def read_port_file(path: Path) -> Candidate | None:
    try:
        lines = path.read_text(encoding="utf-8").split()
    except OSError:
        return None
    if len(lines) < 2 or not lines[0].isdigit():
        return None
    return Candidate("127.0.0.1", int(lines[0]), lines[1])


def parse_spec(spec: str) -> Candidate:
    """``9222``, ``host:9222``, ``http://host:9222`` or a full ``ws://`` address."""
    spec = spec.strip()
    if spec.isdigit():
        return Candidate("127.0.0.1", int(spec), "")
    if "://" not in spec:
        spec = "http://" + spec
    parts = urlsplit(spec)
    if not parts.hostname or not parts.port:
        raise EndpointError(f"cannot read a host and port from {spec!r}")
    path = parts.path if parts.scheme == "ws" and parts.path not in ("", "/") else ""
    return Candidate(parts.hostname, parts.port, path)


def _listening(candidate: Candidate) -> bool:
    try:
        with socket.create_connection((candidate.host, candidate.port), timeout=0.5):
            return True
    except OSError:
        return False


def _cdp_version(candidate: Candidate) -> dict | None:
    url = f"http://{candidate.host}:{candidate.port}/json/version"
    try:
        with urllib.request.urlopen(url, timeout=2.0) as response:  # noqa: S310 - local debugging port
            value = json.loads(response.read(65536))
    except (urllib.error.URLError, OSError, ValueError):
        return None
    return value if isinstance(value, dict) and value.get("webSocketDebuggerUrl") else None


def connect_candidate(candidate: Candidate) -> CdpDriver | BidiDriver:
    base = f"ws://{candidate.host}:{candidate.port}"
    if candidate.ws_path.startswith("/devtools/"):
        return CdpDriver(base + candidate.ws_path)
    if candidate.ws_path == "/session":
        return BidiDriver(base + "/session")
    version = _cdp_version(candidate)
    if version is not None:
        agent = str(version.get("User-Agent", ""))
        app = next((name for name in _NOT_BROWSERS if name in agent), "")
        if app:
            raise EndpointError(f"port {candidate.port} belongs to {app.rstrip('/')}, an app, not a browser")
        return CdpDriver(version["webSocketDebuggerUrl"], product=str(version.get("Browser", "")))
    return BidiDriver(base + "/session")


def connect(spec: str = "") -> CdpDriver | BidiDriver:
    if spec:
        candidates = [parse_spec(spec)]
    else:
        candidates = [found for found in map(read_port_file, port_files()) if found is not None]
        candidates.append(Candidate("127.0.0.1", DEFAULT_PORT, ""))
    errors = []
    for candidate in candidates:
        if not _listening(candidate):
            continue
        try:
            return connect_candidate(candidate)
        except (DriverError, WebSocketError, EndpointError) as error:
            errors.append(f"{candidate.host}:{candidate.port}: {error}")
    detail = ("\nTried: " + "; ".join(errors)) if errors else ""
    raise EndpointError(HOW_TO_START + detail)

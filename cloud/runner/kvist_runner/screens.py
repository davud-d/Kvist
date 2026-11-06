# -*- coding: utf-8 -*-
"""The agent's screens (the browser skill, the desktop skill) as pictures for their owner only.

Kvist serves each virtual screen on a loopback address of this machine behind a secret token, and
announces it in a ``screen`` event. That address and token mean nothing outside this machine, and
must never leave it: the event is rewritten here to drop them, and this machine fetches the frames
itself and sends them to the server over the one authenticated socket it already has. The server
hands them only to the person who owns the task (kvist_cloud/screens.py).

Frames are pulled only while someone is watching: the server renews a short lease for as long as
the owner's page asks for pictures, and the pump stops by itself when the lease runs out.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from urllib.parse import urlsplit

log = logging.getLogger("kvist_runner.screens")

FRAME_INTERVAL_SECONDS = 0.5
# A smaller picture than the 1920 px one Kvist makes for a local window: this one crosses the internet.
FRAME_WIDTH = 960
FRAME_QUALITY = 65
# The server refuses anything bigger, so there is no point reading it.
MAX_FRAME_BYTES = 1024 * 1024
FETCH_TIMEOUT_SECONDS = 3.0
MAX_LEASE_SECONDS = 30.0
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost"})
ADDRESS_FIELDS = ("url", "stream_url", "frame_url")


def is_loopback(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme == "http" and parts.hostname in LOOPBACK_HOSTS and parts.port is not None


class ScreenRelay:
    """Screens of one task."""

    def __init__(self, task_id: int, send: Callable[[dict], bool]) -> None:
        self.task_id = task_id
        self._send = send
        self._frame_urls: dict[str, str] = {}
        self._leases: dict[str, float] = {}
        self._pumps: dict[str, asyncio.Task] = {}

    def sanitize(self, event: dict) -> dict:
        """The event as the server may see it: no address, no token."""
        screen_id = str(event.get("screen_id") or "")
        frame_url = str(event.get("frame_url") or "")
        if event.get("state") == "closed":
            self._forget(screen_id)
        elif screen_id and is_loopback(frame_url):
            self._frame_urls[screen_id] = frame_url
        elif screen_id:
            log.warning("task %s: screen %s has no loopback frame address, not relayed", self.task_id, screen_id)
        return {**event, **{field: "" for field in ADDRESS_FIELDS}}

    def watch(self, screen_id: str, lease_seconds: float) -> None:
        if screen_id not in self._frame_urls:
            return
        lease = min(max(lease_seconds, 1.0), MAX_LEASE_SECONDS)
        self._leases[screen_id] = time.monotonic() + lease
        pump = self._pumps.get(screen_id)
        if pump is None or pump.done():
            self._pumps[screen_id] = asyncio.create_task(self._pump(screen_id))

    async def stop(self) -> None:
        pumps = list(self._pumps.values())
        self._frame_urls.clear()
        self._leases.clear()
        self._pumps.clear()
        for pump in pumps:
            pump.cancel()
        await asyncio.gather(*pumps, return_exceptions=True)

    def _forget(self, screen_id: str) -> None:
        self._frame_urls.pop(screen_id, None)
        self._leases.pop(screen_id, None)
        pump = self._pumps.pop(screen_id, None)
        if pump is not None:
            pump.cancel()

    async def _pump(self, screen_id: str) -> None:
        last = b""
        while time.monotonic() < self._leases.get(screen_id, 0.0):
            url = self._frame_urls.get(screen_id)
            if url is None:
                return
            frame = await asyncio.to_thread(_fetch, url)
            if frame and frame != last:
                last = frame
                self._send(
                    {
                        "type": "screen_frame",
                        "task_id": self.task_id,
                        "screen_id": screen_id,
                        "jpeg": base64.b64encode(frame).decode("ascii"),
                    }
                )
            await asyncio.sleep(FRAME_INTERVAL_SECONDS)
        self._pumps.pop(screen_id, None)


def _fetch(url: str) -> bytes:
    """One frame from the loopback viewer; empty when there is none to show right now."""
    address = f"{url}&w={FRAME_WIDTH}&q={FRAME_QUALITY}" if "?" in url else f"{url}?w={FRAME_WIDTH}&q={FRAME_QUALITY}"
    # No redirects and no proxy: this is a request to this very machine and nothing else.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)
    try:
        with opener.open(address, timeout=FETCH_TIMEOUT_SECONDS) as response:
            if response.headers.get_content_type() != "image/jpeg":
                return b""
            body = response.read(MAX_FRAME_BYTES + 1)
    except (urllib.error.URLError, OSError, ValueError) as error:
        log.debug("screen frame unavailable: %s", type(error).__name__)
        return b""
    return body if len(body) <= MAX_FRAME_BYTES else b""


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: D401 - urllib's hook
        return None


_NoRedirect = _NoRedirectHandler()

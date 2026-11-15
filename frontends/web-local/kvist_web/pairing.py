# -*- coding: utf-8 -*-
"""A phone asks to be trusted; this computer asks its owner, once, in a window on the desktop.

One question at a time, and a pause after a refusal: someone on the network who keeps asking cannot bury the
owner in windows. The window names the phone and its address, and trust is a token only this phone gets.
"""
from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from .ask import ask as ask_owner
from .devices import DeviceStore

DEVICE_ID = re.compile(r"[A-Za-z0-9-]{8,64}")
COOLDOWN_SECONDS = 30.0
KEEP_SECONDS = 300.0
ASK_SECONDS = 60


class PairingError(Exception):
    """A request that cannot be taken now; ``reason`` is for the phone (busy, cooldown, bad_request)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(slots=True)
class Request:
    id: str
    device_id: str
    name: str
    address: str
    created: float
    state: str = "pending"
    token: str = ""


def clean_name(name: object) -> str:
    """A phone's name shown in a window: printable characters only, short."""
    text = "".join(char for char in str(name) if char.isprintable()).strip()
    return text[:40] or "Phone"


def question(name: str, address: str, russian: bool) -> tuple[str, str]:
    if russian:
        return (
            "Kvist: телефон просит доступ",
            f"Телефон «{name}» ({address}) хочет управлять Kvist на этом компьютере: запускать и останавливать "
            "агентов, вводить команды, менять модели.\n\nРазрешай, только если это твой телефон.",
        )
    return (
        "Kvist: a phone asks for access",
        f"The phone “{name}” ({address}) wants to control Kvist on this computer: start and stop agents, "
        "run commands, change models.\n\nAllow it only if it is your phone.",
    )


class Pairing:
    def __init__(
        self,
        devices: DeviceStore,
        *,
        russian: bool = False,
        asker: Callable[[str, str], bool | None] = lambda title, text: ask_owner(title, text, timeout=ASK_SECONDS),
        clock: Callable[[], float] = time.monotonic,
        start: Callable[[Callable[[], None]], None] = lambda work: threading.Thread(target=work, name="kvist-pairing", daemon=True).start(),
    ) -> None:
        self._devices = devices
        self._russian = russian
        self._asker = asker
        self._clock = clock
        self._start = start
        self._lock = threading.Lock()
        self._requests: dict[str, Request] = {}
        self._denied_at: dict[str, float] = {}
        self._counter = 0

    def request(self, device_id: object, name: object, address: str) -> str:
        if not isinstance(device_id, str) or not DEVICE_ID.fullmatch(device_id):
            raise PairingError("bad_request")
        label = clean_name(name)
        now = self._clock()
        with self._lock:
            self._forget_old(now)
            for existing in self._requests.values():
                if existing.state != "pending":
                    continue
                if existing.device_id == device_id:
                    return existing.id
                raise PairingError("busy")
            if now - self._denied_at.get(address, -COOLDOWN_SECONDS) < COOLDOWN_SECONDS:
                raise PairingError("cooldown")
            self._counter += 1
            request = Request(f"p{self._counter}-{int(now * 1000)}", device_id, label, address, now)
            self._requests[request.id] = request
        self._start(lambda: self._decide(request))
        return request.id

    def status(self, request_id: str) -> dict[str, str]:
        with self._lock:
            request = self._requests.get(request_id)
            if request is None:
                return {"state": "expired"}
            result = {"state": request.state}
            if request.state == "approved" and request.token:
                # The token travels once; a phone that missed the answer pairs again.
                result["token"], request.token = request.token, ""
            return result

    def _decide(self, request: Request) -> None:
        title, text = question(request.name, request.address, self._russian)
        answer = self._asker(title, text)
        token = self._devices.approve(request.device_id, request.name) if answer else ""
        with self._lock:
            request.state = "approved" if answer else "unavailable" if answer is None else "denied"
            request.token = token
            if answer is not True:
                self._denied_at[request.address] = self._clock()

    def _forget_old(self, now: float) -> None:
        for key in [key for key, item in self._requests.items() if item.state != "pending" and now - item.created > KEEP_SECONDS]:
            del self._requests[key]

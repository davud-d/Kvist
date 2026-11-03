# -*- coding: utf-8 -*-
"""Who may write to the session: one driver at a time, everyone else only watches.

The daemon checks this before a line reaches the core, so a frontend that ignores its banner still
cannot start a second task. A driver that stops pinging (the computer lost power, the network is
gone) loses the lease after ``timeout`` seconds; the next frontend takes it by asking.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable

from kernel.ssh.wire import LEASE_SECONDS


class Lease:
    def __init__(self, timeout: float = LEASE_SECONDS, clock: Callable[[], float] = time.monotonic) -> None:
        self._timeout = timeout
        self._clock = clock
        self._lock = threading.Lock()
        self._driver: str | None = None
        self._seen = 0.0

    def _live_driver(self) -> str | None:
        if self._driver is not None and self._clock() - self._seen > self._timeout:
            return None
        return self._driver

    def claim_if_free(self, client: str) -> bool:
        """The first frontend to arrive drives; a later one watches until it asks."""
        with self._lock:
            if self._live_driver() not in (None, client):
                return False
            self._driver = client
            self._seen = self._clock()
            return True

    def touch(self, client: str) -> None:
        with self._lock:
            if self._driver == client:
                self._seen = self._clock()

    def take(self, client: str) -> str | None:
        """Make ``client`` the driver; returns the one it replaced."""
        with self._lock:
            previous = self._live_driver()
            self._driver = client
            self._seen = self._clock()
            return previous if previous != client else None

    def release(self, client: str) -> bool:
        with self._lock:
            if self._driver != client:
                return False
            self._driver = None
            return True

    def is_driver(self, client: str) -> bool:
        with self._lock:
            return self._live_driver() == client

    def reap(self) -> str | None:
        """Drop a driver that went silent; returns it so its connection can be closed."""
        with self._lock:
            if self._driver is None or self._live_driver() is not None:
                return None
            lost, self._driver = self._driver, None
            return lost

    def idle_seconds(self) -> float:
        with self._lock:
            return self._clock() - self._seen if self._driver is not None else 0.0

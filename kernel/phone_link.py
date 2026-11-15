# -*- coding: utf-8 -*-
"""Makes this computer visible to the Kvist phone app for as long as an agent runs.

A core that starts an agent makes sure the local web controller runs in LAN mode (``kvist-web --lan``) and
keeps one connection open to it. The controller counts those connections and stops a minute after the last
one is gone, so the computer is visible only while Kvist is running. A phone still has to be allowed by the
owner, once, in a window on this computer; nothing here lets anyone in.

Off with ``KVIST_PHONE=0`` or ``"phone_access": false`` in ``~/.kvist/config.json``.
"""
from __future__ import annotations

import http.client
import logging
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from kernel.config.user_config import load_user_config
from kernel.tray.tray_link import core_command

_LOG = logging.getLogger("kvist.phone")

CONFIG_KEY = "phone_access"
ENV = "KVIST_PHONE"
START_WAIT_SECONDS = 15.0
START_MARKER_SECONDS = 20.0
RETRY_SECONDS = 30.0
RECONNECT_SECONDS = 2.0
HEARTBEAT_TIMEOUT_SECONDS = 30.0
_POLL_SECONDS = 0.3


def enabled(env: dict[str, str] | None = None) -> bool:
    if (os.environ if env is None else env).get(ENV) == "0":
        return False
    try:
        return load_user_config().get(CONFIG_KEY) is not False
    except RuntimeError as error:
        # The agent reports a broken config itself; this feature simply stays off.
        _LOG.warning("phone access is off, the config cannot be read: %s", error)
        return False


def _spawn_detached(argv: list[str]) -> None:
    options: dict[str, Any] = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if sys.platform == "win32":
        options["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    # The controller asks the owner in a window of the desktop, so it keeps this process's environment (DISPLAY and the like).
    subprocess.Popen(argv, **options)


class PhoneLink:
    # Shared by every link in the process: a process that builds many agents starts the loop once.
    _started = False

    def __init__(
        self,
        *,
        spawn: Callable[[list[str]], None] = _spawn_detached,
        sleep: Callable[[float], None] = time.sleep,
        marker: Path | None = None,
    ) -> None:
        self._spawn = spawn
        self._sleep = sleep
        self._marker = marker

    def start(self) -> None:
        if PhoneLink._started:
            return
        PhoneLink._started = True
        threading.Thread(target=self.run, name="kvist-phone-link", daemon=True).start()

    def run(self) -> None:
        try:
            from kvist_web.instance import InstanceDescriptor, controller_home
        except ImportError as error:
            # An install without the web package has no phone support; that is not an error.
            _LOG.info("phone access is unavailable: %s", error)
            return
        descriptor = InstanceDescriptor()
        marker = self._marker or controller_home() / "controller.starting"
        while True:
            if self.ensure_controller(descriptor, marker):
                self.hold(descriptor)
                self._sleep(RECONNECT_SECONDS)
            else:
                self._sleep(RETRY_SECONDS)

    def ensure_controller(self, descriptor: Any, marker: Path) -> bool:
        """True when a controller that serves phones is running; starts one when there is none."""
        if descriptor.health():
            if descriptor.lan():
                return True
            # A controller for this computer's browser only: it owns the port, and a second one would fight it.
            _LOG.info("kvist-web runs without --lan; phones cannot see this computer until it is restarted with --lan")
            return False
        if not self._claim_start(marker):
            return self._wait_healthy(descriptor)
        self._spawn([*core_command(), "--web-controller", "--lan", "--auto-exit"])
        return self._wait_healthy(descriptor)

    @staticmethod
    def _claim_start(marker: Path) -> bool:
        """Several agents may start at once; only the first starts the controller."""
        try:
            if time.time() - marker.stat().st_mtime < START_MARKER_SECONDS:
                return False
        except FileNotFoundError:
            pass
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
        return True

    def _wait_healthy(self, descriptor: Any) -> bool:
        deadline = time.monotonic() + START_WAIT_SECONDS
        while time.monotonic() < deadline:
            if descriptor.health() and descriptor.lan():
                return True
            self._sleep(_POLL_SECONDS)
        return False

    @staticmethod
    def hold(descriptor: Any) -> None:
        """Stay connected until the controller goes away; its heartbeat tells a live one from a dead one."""
        opened = descriptor.open(HEARTBEAT_TIMEOUT_SECONDS)
        if opened is None:
            return
        connection, value = opened
        try:
            connection.request("GET", "/api/admin/presence", headers={"Authorization": f"Bearer {value.get('admin_token', '')}"})
            response = connection.getresponse()
            if response.status != 200:
                return
            while response.read(64):
                pass
        except (OSError, http.client.HTTPException) as error:
            _LOG.info("lost the controller: %s", error)
        finally:
            connection.close()


def start(env: dict[str, str] | None = None) -> None:
    """Called by a core that starts an agent."""
    if enabled(env):
        PhoneLink().start()

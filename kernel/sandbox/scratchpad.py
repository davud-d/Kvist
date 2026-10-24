# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import secrets
import shutil
import tempfile
import threading
from pathlib import Path

LIMIT_BYTES = 500 * 1024 * 1024


class ScratchpadFull(Exception):
    """A write would take the scratchpad past its size limit."""


class Scratchpad:
    """Throwaway directory in the system temp folder for quick test scripts.

    One per core process, so the main agent and its helpers share it. The
    directory is made on first use and removed with everything in it when the
    conversation moves on, so a script written to try something leaves no trace.
    """

    def __init__(self, limit_bytes: int = LIMIT_BYTES) -> None:
        self.limit_bytes = limit_bytes
        # The name is fixed up front so the prompt can state it before anything exists.
        self.path = Path(tempfile.gettempdir()).resolve() / f"kvist-scratch-{secrets.token_hex(6)}"
        self._lock = threading.Lock()

    def ensure(self) -> Path:
        self.path.mkdir(mode=0o700, exist_ok=True)
        return self.path

    def owns(self, resolved: Path) -> bool:
        return resolved == self.path or resolved.is_relative_to(self.path)

    def used_bytes(self) -> int:
        total = 0
        for folder, _dirs, names in os.walk(self.path):
            for name in names:
                try:
                    total += os.lstat(os.path.join(folder, name)).st_size
                except FileNotFoundError:
                    continue
        return total

    def check_room(self, extra_bytes: int, replaced_bytes: int = 0) -> None:
        """Refuse a write that would pass the limit; ``replaced_bytes`` is what it overwrites."""
        with self._lock:
            after = self.used_bytes() - replaced_bytes + extra_bytes
        if after > self.limit_bytes:
            raise ScratchpadFull(
                f"The scratchpad holds {self.limit_bytes // (1024 * 1024)} MB at most; "
                f"this write would bring it to {after // (1024 * 1024)} MB. "
                "Delete files you no longer need there."
            )

    def clear(self) -> None:
        # A file a script left unremovable must not block starting the next session.
        with self._lock:
            shutil.rmtree(self.path, ignore_errors=True)

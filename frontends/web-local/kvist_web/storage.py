"""Small controller-only state store; Kvist remains the session source of truth."""
from __future__ import annotations

import json
import os
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable


DEFAULT_STATE = {
    "servers": [],
    "workspaces": {},
    "read_cursors": {},
    "known_sessions": {},
    "ui": {},
}


class WebStateStore:
    """Persist server metadata, cursors and UI state in Kvist's global config."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._lock = threading.RLock()

    def load(self) -> dict[str, Any]:
        with self._lock:
            if self._path is None:
                from kernel.config.user_config import get_web_local_config

                value = get_web_local_config()
            else:
                try:
                    value = json.loads(self._path.read_text(encoding="utf-8"))
                except FileNotFoundError:
                    value = {}
                except (OSError, ValueError) as error:
                    raise RuntimeError("Kvist web state is unreadable.") from error
            state = deepcopy(DEFAULT_STATE)
            if isinstance(value, dict):
                for key in state:
                    if key in value and isinstance(value[key], type(state[key])):
                        state[key] = deepcopy(value[key])
            return state

    def update(self, change: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        with self._lock:
            state = self.load()
            change(state)
            if self._path is None:
                from kernel.config.user_config import save_web_local_config

                save_web_local_config(state)
            else:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self._path.with_name(f".{self._path.name}.{os.getpid()}.tmp")
                temporary.write_text(
                    json.dumps(state, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                if os.name != "nt":
                    temporary.chmod(0o600)
                temporary.replace(self._path)
            return state

    @staticmethod
    def cursor_key(server_id: str, workspace: str, session: str) -> str:
        return json.dumps([server_id, workspace, session], separators=(",", ":"))

    def mark_read(self, server_id: str, workspace: str, session: str, seq: int) -> None:
        key = self.cursor_key(server_id, workspace, session)

        def apply(state: dict[str, Any]) -> None:
            previous = int(state["read_cursors"].get(key, 0))
            state["read_cursors"][key] = max(previous, int(seq))
            state["known_sessions"][key] = True

        self.update(apply)

    def read_cursor(self, server_id: str, workspace: str, session: str) -> int:
        key = self.cursor_key(server_id, workspace, session)
        return int(self.load()["read_cursors"].get(key, 0))

    def initialize_session(
        self, server_id: str, workspace: str, session: str, current_seq: int
    ) -> int:
        """Baseline pre-existing history once; later events become unread."""
        return self.initialize_sessions(
            server_id, workspace, [(session, current_seq)]
        )[session]

    def initialize_sessions(
        self,
        server_id: str,
        workspace: str,
        sessions: list[tuple[str, int]],
    ) -> dict[str, int]:
        """Initialize a whole workspace in one atomic config update."""
        state = self.load()
        keys = {
            session: self.cursor_key(server_id, workspace, session)
            for session, _seq in sessions
        }
        if all(state["known_sessions"].get(key) for key in keys.values()):
            return {
                session: int(state["read_cursors"].get(key, 0))
                for session, key in keys.items()
            }

        def apply(value: dict[str, Any]) -> None:
            for session, current_seq in sessions:
                key = keys[session]
                if value["known_sessions"].get(key):
                    continue
                value["known_sessions"][key] = True
                value["read_cursors"][key] = max(
                    int(value["read_cursors"].get(key, 0)), int(current_seq)
                )

        updated = self.update(apply)
        return {
            session: int(updated["read_cursors"].get(key, 0))
            for session, key in keys.items()
        }

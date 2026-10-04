"""Local Runtime: headless SessionStore plus lazy stdio-multi processes."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from kernel.change_journal import build_diff_report, build_session_diff_report
from kernel.session_store import SessionStore

from ..events import EventBroker
from ..processes import ProcessSupervisor
from .base import Runtime


class LocalRuntime(Runtime):
    def __init__(
        self,
        server_id: str,
        workspaces: list[str],
        broker: EventBroker,
        *,
        on_workspace_added=None,
    ) -> None:
        self.server_id = server_id
        self._workspaces: list[str] = []
        self._on_workspace_added = on_workspace_added
        self.supervisor = ProcessSupervisor(server_id, broker)
        for workspace in workspaces:
            try:
                resolved = str(Path(workspace).expanduser().resolve(strict=True))
            except OSError:
                continue
            if resolved not in self._workspaces:
                self._workspaces.append(resolved)

    def list_workspaces(self) -> list[str]:
        return list(self._workspaces)

    def add_workspace(self, path: str) -> dict[str, Any]:
        resolved = Path(path).expanduser().resolve(strict=True)
        if not resolved.is_dir():
            raise ValueError("Workspace must be a directory.")
        value = str(resolved)
        if value not in self._workspaces:
            self._workspaces.append(value)
            if self._on_workspace_added is not None:
                self._on_workspace_added(value)
        return {"workspace": value, "sessions": self.sessions(value)}

    def sessions(self, workspace: str) -> dict[str, Any]:
        path = self._workspace(workspace)
        payload = SessionStore(path).listing()
        processes = {
            item["session"]: item
            for item in self.supervisor.snapshots()
            if item["workspace"] == str(path)
        }
        for item in payload["session_items"]:
            history = SessionStore(path).history(item["name"], limit=1)
            events = history["events"]
            item["last_event_id"] = events[-1]["seq"] if events else 0
            item["last_event_type"] = events[-1]["event"].get("type", "") if events else ""
            item["execution"] = processes.get(item["name"], {}).get("state", "idle")
            item["pending_input"] = processes.get(item["name"], {}).get("pending_input")
        return payload

    def history(
        self, workspace: str, session: str, *, after: int = 0, limit: int = 1_000
    ) -> dict[str, Any]:
        return SessionStore(self._workspace(workspace)).history(
            session, limit=limit, after=after
        )

    def create_session(self, workspace: str, name: str | None = None) -> dict[str, Any]:
        store = SessionStore(self._workspace(workspace))
        if name:
            result = store.create(name)
        else:
            result = store.new()
        return result

    def send(self, workspace: str, session: str, text: str) -> dict[str, Any]:
        path = self._workspace(workspace)
        process = self.supervisor.get_or_start(path, session)
        process.send_goal(text)
        return {"ok": True, "process": process.snapshot().to_dict()}

    def stop(self, workspace: str, session: str) -> dict[str, Any]:
        process = self.supervisor.find(self._workspace(workspace), session)
        if process is None:
            return {"ok": True, "state": "idle"}
        process.cancel()
        return {"ok": True, "state": process.snapshot().state}

    def respond(
        self, workspace: str, session: str, response: dict[str, Any]
    ) -> dict[str, Any]:
        process = self.supervisor.find(self._workspace(workspace), session)
        if process is None:
            raise ValueError("This session has no running process.")
        process.respond(response)
        return {"ok": True, "process": process.snapshot().to_dict()}

    def processes(self) -> list[dict[str, Any]]:
        return self.supervisor.snapshots()

    def configuration(self, operation: str, payload: dict | None = None) -> dict:
        from ..configuration import configuration_info, model_catalog, save_configuration

        handlers = {"info": lambda _: configuration_info(), "models": model_catalog, "save": save_configuration}
        if operation not in handlers:
            raise ValueError("Unknown configuration operation.")
        return handlers[operation](payload or {})

    def command(self, workspace: str, session: str, name: str, args: list[str]) -> dict:
        process = self.supervisor.find(self._workspace(workspace), session)
        if process is None:
            raise ValueError("This session has no running core. Save defaults for its next launch.")
        process.command(name, args)
        return {"ok": True}

    def changes(self, workspace: str, session: str | None = None) -> dict[str, Any]:
        path = self._workspace(workspace)
        report = (
            build_session_diff_report(path, session)
            if session
            else build_diff_report(path)
        )
        return {"report": report}

    def _workspace(self, value: str) -> Path:
        resolved = Path(value).expanduser().resolve(strict=True)
        if str(resolved) not in self._workspaces:
            raise ValueError("Unknown workspace for this server.")
        return resolved

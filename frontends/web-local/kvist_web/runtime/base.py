"""Runtime boundary used identically for local and SSH-backed servers."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Runtime(ABC):
    @abstractmethod
    def list_workspaces(self) -> list[str]: ...

    @abstractmethod
    def add_workspace(self, path: str) -> dict[str, Any]: ...

    @abstractmethod
    def sessions(self, workspace: str) -> dict[str, Any]: ...

    @abstractmethod
    def history(
        self, workspace: str, session: str, *, after: int = 0, limit: int = 1_000
    ) -> dict[str, Any]: ...

    @abstractmethod
    def create_session(self, workspace: str, name: str | None = None) -> dict[str, Any]: ...

    @abstractmethod
    def send(self, workspace: str, session: str, text: str) -> dict[str, Any]: ...

    @abstractmethod
    def stop(self, workspace: str, session: str) -> dict[str, Any]: ...

    @abstractmethod
    def respond(
        self, workspace: str, session: str, response: dict[str, Any]
    ) -> dict[str, Any]: ...

    @abstractmethod
    def processes(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    def changes(self, workspace: str, session: str | None = None) -> dict[str, Any]: ...

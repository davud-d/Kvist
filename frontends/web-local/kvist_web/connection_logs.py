"""Bounded live connection output; it is never persisted as conversation history."""
from collections import deque
import re
import time
import uuid

from .security import redact

_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")


class ConnectionLogs:
    def __init__(self, broker, server_id: str, attempt_id: str, *, password: str = ""):
        self.broker = broker
        self.server_id = server_id
        self.attempt_id = attempt_id
        self.password = password
        self.entries = deque()
        self.size = 0

    def clean(self, text: object) -> str:
        value = str(text)
        if self.password:
            value = value.replace(self.password, "[REDACTED]")
        value = _ANSI.sub("", value)
        value = "".join(c for c in value if c in "\n\t\r" or ord(c) >= 32)
        return redact(value)[-8192:]

    def append(self, stream: str, text: str) -> None:
        value = self.clean(text)
        if not value:
            return
        entry = {"entry_id": uuid.uuid4().hex, "stream": stream, "text": value, "ts": time.time()}
        self.entries.append(entry)
        self.size += len(value)
        while len(self.entries) > 300 or self.size > 65_536:
            self.size -= len(self.entries.popleft()["text"])
        self.broker.publish(self.server_id, "", "", {
            "type": "connection.log", "attempt_id": self.attempt_id, **entry,
        })

    def result(self, connection: dict) -> dict:
        return {**connection, "attempt_id": self.attempt_id, "logs": list(self.entries)}

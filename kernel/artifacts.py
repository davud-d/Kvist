# -*- coding: utf-8 -*-
"""Result files kept for the person: screenshots, reports, downloads — with their origin.

An artifact is a copy the core owns in ``<workspace>/.kvist/artifacts/``, so it
outlives the sandbox and the process that produced it. Its record says which
skill and version made it, in which call and run, and when. The history and
the model see the record (id, name, size, path for the person), never the bytes.
"""
from __future__ import annotations

import mimetypes
import os
import re
import shutil
import stat
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from kernel.durable_json import JsonStorageError, read_json_object, update_json_object

MAX_ARTIFACT_BYTES = 50 * 1024 * 1024
MAX_ARTIFACTS_PER_RUN = 200
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class ArtifactError(ValueError):
    pass


@dataclass(slots=True)
class Artifact:
    id: str
    name: str
    kind: str
    mime: str
    size: int
    path: str
    origin: dict[str, Any]
    created_at: float
    caption: str = ""

    def public(self) -> dict[str, Any]:
        return asdict(self)


class ArtifactStore:
    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace)
        self.root = self.workspace / ".kvist" / "artifacts"
        self._index = self.root / "index.json"

    def save_file(self, source: Path, *, name: str, kind: str, origin: dict[str, Any], caption: str = "") -> Artifact:
        """Copy one regular file (never a link) into the store."""
        try:
            info = os.lstat(source)
        except OSError as error:
            raise ArtifactError(f"cannot read {source.name}: {error.strerror}") from None
        if not stat.S_ISREG(info.st_mode):
            raise ArtifactError(f"{source.name} is not a regular file")
        if info.st_size > MAX_ARTIFACT_BYTES:
            raise ArtifactError(f"{source.name} is larger than {MAX_ARTIFACT_BYTES // (1024 * 1024)} MiB")
        run_id = str(origin.get("run_id") or "run")
        if len(self.list(run_id)) >= MAX_ARTIFACTS_PER_RUN:
            raise ArtifactError(f"a run may keep at most {MAX_ARTIFACTS_PER_RUN} artifacts")
        artifact_id = f"art_{uuid.uuid4().hex[:12]}"
        safe = _SAFE_NAME.sub("_", Path(name or source.name).name)[:100] or "artifact"
        folder = self.root / _SAFE_NAME.sub("_", run_id)[:40]
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = folder / f"{artifact_id}-{safe}"
        shutil.copyfile(source, target, follow_symlinks=False)
        os.chmod(target, 0o600)
        artifact = Artifact(
            id=artifact_id, name=safe, kind=kind,
            mime=mimetypes.guess_type(safe)[0] or "application/octet-stream",
            size=target.stat().st_size, path=str(target), origin=origin, created_at=time.time(),
            caption=caption[:500],
        )
        self._update(lambda index: {**index, artifact_id: asdict(artifact)})
        return artifact

    def save_bytes(self, data: bytes, *, name: str, kind: str, origin: dict[str, Any], caption: str = "") -> Artifact:
        staging = self.root / f".incoming-{uuid.uuid4().hex[:8]}"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        staging.write_bytes(data)
        try:
            return self.save_file(staging, name=name, kind=kind, origin=origin, caption=caption)
        finally:
            staging.unlink(missing_ok=True)

    def get(self, artifact_id: str) -> Artifact:
        record = self._records().get(artifact_id)
        if record is None:
            raise ArtifactError(f"artifact {artifact_id} does not exist")
        artifact = Artifact(**record)
        if not Path(artifact.path).is_file():
            raise ArtifactError(f"artifact {artifact_id} ({artifact.name}) was deleted")
        return artifact

    def list(self, run_id: str | None = None) -> list[Artifact]:
        items = [Artifact(**record) for record in self._records().values()]
        if run_id is not None:
            items = [item for item in items if item.origin.get("run_id") == run_id]
        return sorted(items, key=lambda item: item.created_at)

    def _records(self) -> dict[str, Any]:
        if not self._index.exists():
            return {}
        try:
            return read_json_object(self._index, boundary=self.workspace, empty_as_missing=True)
        except JsonStorageError as error:
            raise ArtifactError(f"artifact index is damaged: {error}") from None

    def _update(self, change) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        update_json_object(self._index, change, boundary=self.workspace, empty_as_missing=True)

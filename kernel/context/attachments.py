# -*- coding: utf-8 -*-
"""Private storage of attachments: originals, model copies and derived images.

An image the person attaches is kept twice: the original file exactly as sent,
and the copy prepared for models (EXIF-rotated, at most 2048 px, JPEG on a
white background, first frame). Skills and tools that need transparency, full
resolution or every frame read the original; crops and resizes become new
derived attachments, so no operation ever changes an original.

Files live in ``<workspace>/.kvist/attachments/<id>/`` with an index in
``index.json``; they never enter logs, the session journal or a model prompt
as bytes. An attachment belongs to the session it arrived in: deleting or
resetting that session deletes it, and attachments older than
``RETENTION_DAYS`` are removed when a core starts. A reference to a removed
attachment answers "deleted" instead of failing silently.
"""
from __future__ import annotations

import base64
import io
import os
import re
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from kernel.session.durable_json import JsonStorageError, read_json_object, update_json_object
from kernel.vision.vision import MAX_IMAGE_BYTES, image_from_bytes

RETENTION_DAYS = 30
MAX_PIXELS = 25_000_000
# Files a skill produces (screenshots, renders) may be larger than what a person attaches.
MAX_DERIVED_BYTES = 20 * 1024 * 1024
ORIGINS = frozenset({"user_message", "workspace_file", "skill", "browser_snapshot", "derived"})
_ID_RE = re.compile(r"^att_[0-9a-f]{16}$")
_FORMAT_SUFFIX = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp", "GIF": ".gif", "BMP": ".bmp", "TIFF": ".tiff"}
_MIME = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp", "GIF": "image/gif",
         "BMP": "image/bmp", "TIFF": "image/tiff"}


class AttachmentError(ValueError):
    """An attachment that cannot be stored or read; the message says why."""


class AttachmentGone(AttachmentError):
    """The attachment existed but its files were deleted (session removed or expired)."""


@dataclass(slots=True)
class Attachment:
    id: str
    name: str
    format: str
    mime: str
    size: int
    width: int
    height: int
    frames: int
    has_alpha: bool
    origin: dict[str, Any]
    session: str
    created_at: float
    deleted: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        """What a skill or the model may see: no paths, no bytes."""
        data = asdict(self)
        data.pop("extra", None)
        return data


class AttachmentStore:
    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace)
        self.root = self.workspace / ".kvist" / "attachments"
        self._index = self.root / "index.json"

    # --- adding ----------------------------------------------------------

    def add_bytes(self, data: bytes, name: str, *, origin: dict[str, Any], session: str,
                  max_bytes: int = MAX_IMAGE_BYTES) -> Attachment:
        if origin.get("kind") not in ORIGINS:
            raise AttachmentError(f"unknown attachment origin {origin.get('kind')!r}")
        if not data or len(data) > max_bytes:
            raise AttachmentError(f"image must be between 1 byte and {max_bytes // (1024 * 1024)} MiB")
        info = _inspect(data)
        attachment_id = f"att_{uuid.uuid4().hex[:16]}"
        folder = self.root / attachment_id
        folder.mkdir(parents=True, mode=0o700)
        original = folder / f"original{_FORMAT_SUFFIX.get(info['format'], '.bin')}"
        _write_private(original, data)
        model_copy = image_from_bytes(data, name)
        _write_private(folder / "model.jpg", base64.b64decode(model_copy["data_url"].split(",", 1)[1]))
        attachment = Attachment(
            id=attachment_id,
            name=model_copy["name"],
            format=info["format"],
            mime=_MIME.get(info["format"], "application/octet-stream"),
            size=len(data),
            width=info["width"],
            height=info["height"],
            frames=info["frames"],
            has_alpha=info["has_alpha"],
            origin=origin,
            session=session,
            created_at=time.time(),
        )
        self._update(lambda index: {**index, attachment_id: asdict(attachment)})
        return attachment

    def add_file(self, path: Path, name: str, *, origin: dict[str, Any], session: str) -> Attachment:
        try:
            size = path.stat().st_size
        except OSError as error:
            raise AttachmentError(f"cannot read {name}: {error.strerror}") from None
        if size > MAX_DERIVED_BYTES:
            raise AttachmentError("image file is too large")
        return self.add_bytes(path.read_bytes(), name, origin=origin, session=session, max_bytes=MAX_DERIVED_BYTES)

    # --- reading ---------------------------------------------------------

    def get(self, attachment_id: str) -> Attachment:
        if not _ID_RE.match(attachment_id or ""):
            raise AttachmentError(f"{attachment_id!r} is not an attachment id")
        record = self._records().get(attachment_id)
        if record is None:
            raise AttachmentError(f"attachment {attachment_id} does not exist in this workspace")
        attachment = Attachment(**record)
        if attachment.deleted or not (self.root / attachment_id).is_dir():
            raise AttachmentGone(f"attachment {attachment_id} ({attachment.name}) was deleted")
        return attachment

    def list(self, session: str | None = None) -> list[Attachment]:
        items = []
        for record in self._records().values():
            attachment = Attachment(**record)
            if session is None or attachment.session == session:
                items.append(attachment)
        return sorted(items, key=lambda item: item.created_at)

    def original_path(self, attachment_id: str) -> Path:
        attachment = self.get(attachment_id)
        return self.root / attachment_id / f"original{_FORMAT_SUFFIX.get(attachment.format, '.bin')}"

    def model_path(self, attachment_id: str) -> Path:
        self.get(attachment_id)
        return self.root / attachment_id / "model.jpg"

    def model_data_url(self, attachment_id: str) -> dict[str, str]:
        """The image as ``vision.describe_images`` takes it: name + JPEG data URL."""
        attachment = self.get(attachment_id)
        data = self.model_path(attachment_id).read_bytes()
        return {"name": attachment.name, "data_url": "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")}

    # --- derived images --------------------------------------------------

    def crop(self, attachment_id: str, box: list[int], *, session: str) -> Attachment:
        from PIL import Image

        source = self.get(attachment_id)
        if (not isinstance(box, list) or len(box) != 4 or not all(isinstance(v, int) for v in box)
                or not (0 <= box[0] < box[2] <= source.width and 0 <= box[1] < box[3] <= source.height)):
            raise AttachmentError(f"box must be [left, top, right, bottom] inside {source.width}x{source.height}")
        with Image.open(self.original_path(attachment_id)) as image:
            region = image.crop(tuple(box))
            return self._derived(region, source, f"crop {box}", session, keep_format=True)

    def resize(self, attachment_id: str, max_width: int, max_height: int, *, session: str) -> Attachment:
        from PIL import Image

        source = self.get(attachment_id)
        if not (0 < max_width <= 10000 and 0 < max_height <= 10000):
            raise AttachmentError("max_width and max_height must be in 1..10000")
        with Image.open(self.original_path(attachment_id)) as image:
            copy = image.copy()
            copy.thumbnail((max_width, max_height))
            return self._derived(copy, source, f"resize {max_width}x{max_height}", session, keep_format=True)

    def convert(self, attachment_id: str, target: str, *, session: str) -> Attachment:
        from PIL import Image

        source = self.get(attachment_id)
        target = target.upper().replace("JPG", "JPEG")
        if target not in ("PNG", "JPEG", "WEBP"):
            raise AttachmentError("format must be png, jpeg or webp")
        with Image.open(self.original_path(attachment_id)) as image:
            converted = image.convert("RGB") if target == "JPEG" else image.copy()
            return self._derived(converted, source, f"convert {target}", session, keep_format=False, fmt=target)

    def _derived(self, image, source: Attachment, operation: str, session: str, *, keep_format: bool,
                 fmt: str = "") -> Attachment:
        fmt = fmt or (source.format if keep_format and source.format in ("PNG", "JPEG", "WEBP") else "PNG")
        if fmt == "JPEG" and image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        output = io.BytesIO()
        image.save(output, format=fmt)
        stem = Path(source.name).stem or "image"
        return self.add_bytes(
            output.getvalue(), f"{stem}-{operation.split()[0]}{_FORMAT_SUFFIX[fmt]}",
            origin={"kind": "derived", "parent": source.id, "operation": operation}, session=session,
            max_bytes=MAX_DERIVED_BYTES,
        )

    # --- lifetime --------------------------------------------------------

    def delete_session(self, session: str) -> int:
        """Remove every attachment of ``session``; the index keeps a tombstone so references say "deleted"."""
        removed = 0
        for attachment in self.list(session):
            if attachment.deleted:
                continue
            shutil.rmtree(self.root / attachment.id, ignore_errors=True)
            removed += 1
        if removed:
            self._update(lambda index: {key: ({**value, "deleted": True} if value.get("session") == session else value)
                                        for key, value in index.items()})
        return removed

    def expire(self, now: float | None = None) -> int:
        limit = (now or time.time()) - RETENTION_DAYS * 86400
        stale = [item for item in self.list() if not item.deleted and item.created_at < limit]
        for attachment in stale:
            shutil.rmtree(self.root / attachment.id, ignore_errors=True)
        if stale:
            ids = {item.id for item in stale}
            self._update(lambda index: {key: ({**value, "deleted": True} if key in ids else value)
                                        for key, value in index.items()})
        return len(stale)

    def _records(self) -> dict[str, Any]:
        if not self._index.exists():
            return {}
        try:
            return read_json_object(self._index, boundary=self.workspace, empty_as_missing=True)
        except JsonStorageError as error:
            raise AttachmentError(f"attachment index is damaged: {error}") from None

    def _update(self, change) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        update_json_object(self._index, change, boundary=self.workspace, empty_as_missing=True)


def _inspect(data: bytes) -> dict[str, Any]:
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > MAX_PIXELS:
                raise AttachmentError("image exceeds 25 megapixels")
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            return {
                "format": image.format or "UNKNOWN",
                "width": image.width,
                "height": image.height,
                "frames": int(getattr(image, "n_frames", 1) or 1),
                "has_alpha": image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info,
            }
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise AttachmentError(f"not a readable image: {error}") from None


def _write_private(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(descriptor, view):]
    finally:
        os.close(descriptor)

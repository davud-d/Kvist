# -*- coding: utf-8 -*-
"""Private, recoverable JSON-object storage for security-sensitive state."""
from __future__ import annotations

import contextlib
import errno
import json
import os
import stat
import tempfile
import threading
import weakref
from pathlib import Path
from typing import Any, Callable, Iterator


DEFAULT_MAX_BYTES = 32 * 1024 * 1024
PRIVATE_FILE_MODE = 0o600
PRIVATE_DIRECTORY_MODE = 0o700


class JsonStorageError(RuntimeError):
    """Base error for durable JSON persistence."""


class JsonStorageCorruptError(JsonStorageError):
    """The primary document and any recovery copy are unusable."""


class JsonStorageSecurityError(JsonStorageError):
    """A path or file identity violates the storage boundary."""


_THREAD_LOCKS: weakref.WeakValueDictionary[str, threading.RLock] = (
    weakref.WeakValueDictionary()
)
_THREAD_LOCKS_GUARD = threading.Lock()


@contextlib.contextmanager
def exclusive_storage_lock(path: Path, *, boundary: Path) -> Iterator[None]:
    """Hold a private thread/process lock associated with ``path``.

    Callers may use this to serialize a broader transaction whose state is not
    itself JSON. The lock path receives the same boundary, symlink, owner, and
    private-mode validation as durable JSON storage.
    """
    target = validate_storage_path(path, boundary)
    with _exclusive_path_lock(target, boundary):
        yield


def validate_storage_path(path: Path, boundary: Path) -> Path:
    """Return an absolute file path after rejecting boundary and link escapes."""
    root = boundary.resolve(strict=True)
    candidate = Path(os.path.abspath(path))
    try:
        relative = candidate.relative_to(root)
    except ValueError as error:
        raise JsonStorageSecurityError(
            f"Storage path {candidate.name!r} is outside its allowed boundary"
        ) from error
    if not relative.parts:
        raise JsonStorageSecurityError("Storage path must name a file")
    _validate_existing_components(root, relative, expect_directory=False)
    return candidate


def validate_storage_directory(path: Path, boundary: Path) -> Path:
    """Return an absolute directory path after rejecting boundary/link escapes."""
    root = boundary.resolve(strict=True)
    candidate = Path(os.path.abspath(path))
    try:
        relative = candidate.relative_to(root)
    except ValueError as error:
        raise JsonStorageSecurityError(
            f"Storage directory {candidate.name!r} is outside its allowed boundary"
        ) from error
    if not relative.parts:
        return root
    _validate_existing_components(root, relative, expect_directory=True)
    return candidate


def list_json_object_entries(
    directory: Path,
    *,
    boundary: Path,
) -> list[tuple[Path, os.stat_result]]:
    """List validated JSON files with one secure directory traversal."""
    target = validate_storage_directory(directory, boundary)
    descriptor: int | None = None
    iterator = None
    try:
        if os.name != "nt":
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_DIRECTORY", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            try:
                descriptor = os.open(target, flags)
            except OSError as error:
                if error.errno == errno.ELOOP:
                    raise JsonStorageSecurityError(
                        f"Storage directory {target.name!r} is a symbolic link"
                    ) from error
                raise JsonStorageError(
                    f"Cannot open storage directory {target.name!r}"
                ) from error
            _validate_open_directory(descriptor, target)
            iterator = os.scandir(descriptor)
        else:
            iterator = os.scandir(target)

        entries: list[tuple[Path, os.stat_result]] = []
        for entry in iterator:
            if not entry.name.endswith(".json"):
                continue
            candidate = target / entry.name
            try:
                info = entry.stat(follow_symlinks=False)
            except FileNotFoundError:
                continue
            except OSError as error:
                raise JsonStorageError(
                    f"Cannot inspect JSON storage {entry.name!r}"
                ) from error
            if stat.S_ISLNK(info.st_mode):
                raise JsonStorageSecurityError(
                    f"Storage path contains a symbolic link at {entry.name!r}"
                )
            if not stat.S_ISREG(info.st_mode):
                raise JsonStorageSecurityError(
                    f"Storage target {entry.name!r} is not a regular file"
                )
            _require_current_owner(candidate, info)
            if info.st_nlink > 1:
                raise JsonStorageSecurityError(
                    f"Storage target {entry.name!r} has unsafe hard links"
                )
            entries.append((candidate, info))
        return entries
    finally:
        if iterator is not None:
            iterator.close()
        if descriptor is not None:
            os.close(descriptor)


def read_json_object(
    path: Path,
    *,
    boundary: Path,
    empty_as_missing: bool = False,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> dict[str, Any]:
    """Read one JSON object, restoring a valid backup after corruption."""
    target = validate_storage_path(path, boundary)
    primary_error: JsonStorageCorruptError | None = None
    try:
        primary = _read_candidate(target, empty_as_missing, max_bytes)
    except JsonStorageCorruptError as error:
        primary = None
        primary_error = error
    if primary is not None:
        _ensure_private_parents(target, boundary)
        _harden_existing_file(target)
        return primary

    backup = validate_storage_path(_backup_path(target), boundary)
    try:
        recovered = _read_candidate(backup, empty_as_missing, max_bytes)
    except JsonStorageCorruptError as error:
        if primary_error is not None:
            raise JsonStorageCorruptError(
                f"JSON storage {target.name!r} is corrupt and has no valid backup"
            ) from error
        raise
    if recovered is None:
        if primary_error is not None:
            raise JsonStorageCorruptError(
                f"JSON storage {target.name!r} is corrupt and has no valid backup"
            ) from primary_error
        return {}

    _harden_existing_file(backup)
    with _exclusive_path_lock(target, boundary):
        current = _read_candidate_after_lock(target, empty_as_missing, max_bytes)
        if current is not None:
            _harden_existing_file(target)
            return current
        _write_unlocked(
            target,
            recovered,
            boundary=boundary,
            backup_existing=False,
            max_bytes=max_bytes,
        )
    return recovered


def write_json_object(
    path: Path,
    data: dict[str, Any],
    *,
    boundary: Path,
    backup_existing: bool = True,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> None:
    """Atomically replace one private JSON object with a durable complete file."""
    if not isinstance(data, dict):
        raise TypeError("JSON storage payload must be a dictionary")
    target = validate_storage_path(path, boundary)
    with _exclusive_path_lock(target, boundary):
        _write_unlocked(
            target,
            data,
            boundary=boundary,
            backup_existing=backup_existing,
            max_bytes=max_bytes,
        )


def update_json_object(
    path: Path,
    update: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    boundary: Path,
    empty_as_missing: bool = False,
    backup_existing: bool = True,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> dict[str, Any]:
    """Atomically read, transform, and replace one JSON object."""
    target = validate_storage_path(path, boundary)
    with _exclusive_path_lock(target, boundary):
        current = _read_for_update(
            target,
            boundary=boundary,
            empty_as_missing=empty_as_missing,
            max_bytes=max_bytes,
        )
        updated = update(current)
        if not isinstance(updated, dict):
            raise TypeError("JSON storage update must return a dictionary")
        _write_unlocked(
            target,
            updated,
            boundary=boundary,
            backup_existing=backup_existing,
            max_bytes=max_bytes,
        )
        return updated


def create_json_object(
    path: Path,
    data: dict[str, Any],
    *,
    boundary: Path,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> bool:
    """Create a JSON object exactly once without replacing existing state."""
    if not isinstance(data, dict):
        raise TypeError("JSON storage payload must be a dictionary")
    target = validate_storage_path(path, boundary)
    with _exclusive_path_lock(target, boundary):
        for candidate in (target, _backup_path(target)):
            validated = validate_storage_path(candidate, boundary)
            try:
                validated.lstat()
            except FileNotFoundError:
                continue
            except OSError as error:
                raise JsonStorageError(
                    f"Cannot inspect JSON storage {target.name!r}"
                ) from error
            return False
        _write_unlocked(
            target,
            data,
            boundary=boundary,
            backup_existing=False,
            max_bytes=max_bytes,
        )
        return True


def delete_json_object(path: Path, *, boundary: Path) -> bool:
    """Durably remove a JSON object and its recovery copy.

    The backup is removed first.  An interruption can therefore leave the
    primary object present, but cannot leave only a backup that resurrects a
    deliberately deleted object on the next read.
    """
    target = validate_storage_path(path, boundary)
    with _exclusive_path_lock(target, boundary):
        removed = False
        for candidate in (_backup_path(target), target):
            validated = validate_storage_path(candidate, boundary)
            try:
                validated.unlink()
            except FileNotFoundError:
                continue
            except OSError as error:
                raise JsonStorageError(
                    f"Cannot remove JSON storage {target.name!r}"
                ) from error
            removed = True
        if removed:
            try:
                _fsync_directory(target.parent)
            except OSError as error:
                raise JsonStorageError(
                    f"Cannot sync deletion of JSON storage {target.name!r}"
                ) from error
        return removed


def append_private_bytes(
    path: Path,
    data: bytes,
    *,
    boundary: Path,
    sync: bool = False,
) -> int:
    """Append ``data`` to a private file and return the file's new size.

    For append-only logs whose whole-file rewrite would be too costly. A file
    that a crash left without a final newline is closed off first, so the
    torn record stays one bad line instead of swallowing the next good one.
    """
    target = validate_storage_path(path, boundary)
    with _exclusive_path_lock(target, boundary):
        flags = os.O_RDWR | os.O_CREAT | os.O_APPEND
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(target, flags, PRIVATE_FILE_MODE)
        except OSError as error:
            if error.errno == errno.ELOOP:
                raise JsonStorageSecurityError(
                    f"Storage file {target.name!r} is a symbolic link"
                ) from error
            raise JsonStorageError(
                f"Cannot open storage file {target.name!r}"
            ) from error
        try:
            _validate_open_file(descriptor, target)
            _fchmod(descriptor, PRIVATE_FILE_MODE)
            size = os.fstat(descriptor).st_size
            if size:
                os.lseek(descriptor, size - 1, os.SEEK_SET)
                if os.read(descriptor, 1) != b"\n":
                    data = b"\n" + data
            _write_all(descriptor, data)
            if sync:
                os.fsync(descriptor)
            return os.fstat(descriptor).st_size
        except OSError as error:
            raise JsonStorageError(
                f"Cannot append to storage file {target.name!r}"
            ) from error
        finally:
            os.close(descriptor)


def _write_all(descriptor: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(descriptor, view)
        view = view[written:]


def read_private_bytes(
    path: Path,
    *,
    boundary: Path,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> bytes | None:
    """Read a private file, or None when it does not exist."""
    return _read_regular_bytes(validate_storage_path(path, boundary), max_bytes)


def update_private_bytes(
    path: Path,
    update: Callable[[bytes], bytes],
    *,
    boundary: Path,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> None:
    """Atomically replace a private file with ``update(current content)``."""
    target = validate_storage_path(path, boundary)
    with _exclusive_path_lock(target, boundary):
        current = _read_regular_bytes(target, max_bytes)
        _atomic_replace_bytes(target, update(current or b""))


def delete_private_file(path: Path, *, boundary: Path) -> bool:
    """Durably remove a private file; False when there was nothing to remove."""
    target = validate_storage_path(path, boundary)
    with _exclusive_path_lock(target, boundary):
        try:
            target.unlink()
        except FileNotFoundError:
            return False
        except OSError as error:
            raise JsonStorageError(
                f"Cannot remove storage file {target.name!r}"
            ) from error
        try:
            _fsync_directory(target.parent)
        except OSError as error:
            raise JsonStorageError(
                f"Cannot sync deletion of storage file {target.name!r}"
            ) from error
        return True


def _validate_existing_components(
    root: Path,
    relative: Path,
    *,
    expect_directory: bool,
) -> None:
    current = root
    for index, part in enumerate(relative.parts):
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            return
        except OSError as error:
            raise JsonStorageSecurityError(
                f"Cannot inspect storage component {current.name!r}"
            ) from error
        if stat.S_ISLNK(info.st_mode):
            raise JsonStorageSecurityError(
                f"Storage path contains a symbolic link at {current.name!r}"
            )
        is_last = index == len(relative.parts) - 1
        expected_directory = not is_last or expect_directory
        if expected_directory and not stat.S_ISDIR(info.st_mode):
            raise JsonStorageSecurityError(
                f"Storage component {current.name!r} is not a directory"
            )
        if is_last and not expect_directory and not stat.S_ISREG(info.st_mode):
            raise JsonStorageSecurityError(
                f"Storage target {current.name!r} is not a regular file"
            )
        _require_current_owner(current, info)
        # Only *extra* names are an aliasing risk.  A durable replace leaves
        # the outgoing inode with zero links while its name still resolves, so
        # an unlocked validation legitimately observes a count below one.
        if is_last and not expect_directory and info.st_nlink > 1:
            raise JsonStorageSecurityError(
                f"Storage target {current.name!r} has unsafe hard links"
            )


def _require_current_owner(path: Path, info: os.stat_result) -> None:
    if not hasattr(os, "geteuid"):
        return
    if info.st_uid != os.geteuid():
        raise JsonStorageSecurityError(
            f"Storage component {path.name!r} is owned by another user"
        )


def _ensure_private_parents(path: Path, boundary: Path) -> None:
    root = boundary.resolve(strict=True)
    relative_parent = path.parent.relative_to(root)
    current = root
    for part in relative_parent.parts:
        current /= part
        try:
            current.mkdir(mode=PRIVATE_DIRECTORY_MODE)
        except FileExistsError:
            pass
        except OSError as error:
            raise JsonStorageError(
                f"Cannot create private storage directory {current.name!r}"
            ) from error
        try:
            info = current.lstat()
        except OSError as error:
            raise JsonStorageError(
                f"Cannot inspect private storage directory {current.name!r}"
            ) from error
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise JsonStorageSecurityError(
                f"Private storage directory {current.name!r} is not a real directory"
            )
        _require_current_owner(current, info)
        _chmod(current, PRIVATE_DIRECTORY_MODE)


def _thread_lock(path: Path) -> threading.RLock:
    key = os.path.normcase(str(path))
    with _THREAD_LOCKS_GUARD:
        lock = _THREAD_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _THREAD_LOCKS[key] = lock
        return lock


@contextlib.contextmanager
def _exclusive_path_lock(path: Path, boundary: Path) -> Iterator[None]:
    lock = _thread_lock(path)
    with lock:
        _ensure_private_parents(path, boundary)
        validate_storage_path(path, boundary)
        lock_path = validate_storage_path(
            path.with_name(f".{path.name}.lock"),
            boundary,
        )
        flags = os.O_RDWR | os.O_CREAT
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(lock_path, flags, PRIVATE_FILE_MODE)
        except OSError as error:
            if error.errno == errno.ELOOP:
                raise JsonStorageSecurityError(
                    f"Storage lock {lock_path.name!r} is a symbolic link"
                ) from error
            raise JsonStorageError(
                f"Cannot open storage lock {lock_path.name!r}"
            ) from error
        try:
            _validate_open_file(descriptor, lock_path)
            _fchmod(descriptor, PRIVATE_FILE_MODE)
            _lock_descriptor(descriptor)
            try:
                yield
            finally:
                _unlock_descriptor(descriptor)
        finally:
            os.close(descriptor)


def _lock_descriptor(descriptor: int) -> None:
    if os.name == "nt":
        import msvcrt

        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
        return
    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_EX)


def _unlock_descriptor(descriptor: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_UN)


def _validate_open_file(descriptor: int, path: Path) -> None:
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode):
        raise JsonStorageSecurityError(
            f"Storage file {path.name!r} is not regular"
        )
    _require_current_owner(path, info)
    if info.st_nlink > 1:
        raise JsonStorageSecurityError(
            f"Storage file {path.name!r} has unsafe hard links"
        )


def _validate_open_directory(descriptor: int, path: Path) -> None:
    info = os.fstat(descriptor)
    if not stat.S_ISDIR(info.st_mode):
        raise JsonStorageSecurityError(
            f"Storage directory {path.name!r} is not a directory"
        )
    _require_current_owner(path, info)


def _read_candidate(
    path: Path,
    empty_as_missing: bool,
    max_bytes: int,
) -> dict[str, Any] | None:
    raw = _read_regular_bytes(path, max_bytes)
    if raw is None:
        return None
    if not raw and empty_as_missing:
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise JsonStorageCorruptError(
            f"JSON storage {path.name!r} is corrupt"
        ) from error
    # A Unicode-heavy JSON document can require four bytes per decoded
    # character. Drop the raw file buffer before json.loads creates the
    # retained value strings so both full representations do not overlap.
    del raw
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise JsonStorageCorruptError(
            f"JSON storage {path.name!r} is corrupt"
        ) from error
    if not isinstance(payload, dict):
        raise JsonStorageCorruptError(
            f"JSON storage {path.name!r} must contain an object"
        )
    return payload


def _read_candidate_after_lock(
    path: Path,
    empty_as_missing: bool,
    max_bytes: int,
) -> dict[str, Any] | None:
    try:
        return _read_candidate(path, empty_as_missing, max_bytes)
    except JsonStorageCorruptError:
        return None


def _read_for_update(
    target: Path,
    *,
    boundary: Path,
    empty_as_missing: bool,
    max_bytes: int,
) -> dict[str, Any]:
    primary_error: JsonStorageCorruptError | None = None
    try:
        primary = _read_candidate(target, empty_as_missing, max_bytes)
    except JsonStorageCorruptError as error:
        primary = None
        primary_error = error
    if primary is not None:
        _harden_existing_file(target)
        return primary

    backup = validate_storage_path(_backup_path(target), boundary)
    try:
        recovered = _read_candidate(backup, empty_as_missing, max_bytes)
    except JsonStorageCorruptError as error:
        if primary_error is not None:
            raise JsonStorageCorruptError(
                f"JSON storage {target.name!r} is corrupt and has no valid backup"
            ) from error
        raise
    if recovered is not None:
        _harden_existing_file(backup)
        return recovered
    if primary_error is not None:
        raise JsonStorageCorruptError(
            f"JSON storage {target.name!r} is corrupt and has no valid backup"
        ) from primary_error
    return {}


def _read_regular_bytes(path: Path, max_bytes: int) -> bytes | None:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return None
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise JsonStorageSecurityError(
                f"Storage file {path.name!r} is a symbolic link"
            ) from error
        raise JsonStorageError(
            f"Cannot read JSON storage {path.name!r}"
        ) from error
    try:
        _validate_open_file(descriptor, path)
        size = os.fstat(descriptor).st_size
        if size > max_bytes:
            raise JsonStorageError(
                f"JSON storage {path.name!r} exceeds {max_bytes} bytes"
            )
        # BufferedReader fills one result object instead of retaining hundreds
        # of chunks and allocating a second full-size b"".join result.
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise JsonStorageError(
                f"JSON storage {path.name!r} exceeds {max_bytes} bytes"
            )
        return raw
    finally:
        os.close(descriptor)


def _write_unlocked(
    path: Path,
    data: dict[str, Any],
    *,
    boundary: Path,
    backup_existing: bool,
    max_bytes: int,
) -> None:
    try:
        encoded = (
            json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise JsonStorageError(
            f"Cannot serialize JSON storage {path.name!r}"
        ) from error
    if len(encoded) > max_bytes:
        raise JsonStorageError(
            f"JSON storage {path.name!r} exceeds {max_bytes} bytes"
        )

    if backup_existing:
        # Mirror the new complete generation before replacing the primary.
        # If interrupted, readers still prefer the old intact primary; after
        # success, recovery restores the latest acknowledged state rather than
        # resurrecting deleted or superseded data.
        backup = validate_storage_path(_backup_path(path), boundary)
        _atomic_replace_bytes(backup, encoded)
    _atomic_replace_bytes(path, encoded)


def _atomic_replace_bytes(path: Path, content: bytes) -> None:
    try:
        descriptor, raw_temp = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
    except OSError as error:
        raise JsonStorageError(
            f"Cannot create a temporary file for JSON storage {path.name!r}"
        ) from error
    temp_path = Path(raw_temp)
    replaced = False
    try:
        try:
            _fchmod(descriptor, PRIVATE_FILE_MODE)
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = -1
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            _replace_path(temp_path, path)
            replaced = True
            _chmod(path, PRIVATE_FILE_MODE)
            _fsync_directory(path.parent)
        except JsonStorageError:
            raise
        except OSError as error:
            raise JsonStorageError(
                f"Cannot atomically replace JSON storage {path.name!r}"
            ) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if not replaced:
            try:
                temp_path.unlink()
            except OSError:
                pass


def _replace_path(source: Path, destination: Path) -> None:
    os.replace(source, destination)


def _backup_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".bak")


def _harden_existing_file(path: Path) -> None:
    _chmod(path, PRIVATE_FILE_MODE)


def _fchmod(descriptor: int, mode: int) -> None:
    fchmod = getattr(os, "fchmod", None)
    if fchmod is not None:
        fchmod(descriptor, mode)


def _chmod(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode, follow_symlinks=False)
    except (NotImplementedError, TypeError):
        os.chmod(path, mode)
    except OSError as error:
        raise JsonStorageError(
            f"Cannot secure storage permissions for {path.name!r}"
        ) from error


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    except OSError as error:
        if error.errno not in {errno.EBADF, errno.EINVAL, errno.ENOTSUP}:
            raise
    finally:
        os.close(descriptor)

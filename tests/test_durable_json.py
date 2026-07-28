from __future__ import annotations

import concurrent.futures
import errno
import gc
import json
import multiprocessing
import os
import stat
from pathlib import Path

import pytest

from tests.conftest import requires_posix

import durable_json
from durable_json import (
    JsonStorageCorruptError,
    JsonStorageError,
    JsonStorageSecurityError,
    create_json_object,
    delete_json_object,
    list_json_object_entries,
    read_json_object,
    update_json_object,
    validate_storage_path,
    write_json_object,
)


def _increment_in_process(path: str, boundary: str, count: int) -> None:
    def increment(payload):
        payload["count"] = int(payload.get("count", 0)) + 1
        return payload

    for _ in range(count):
        update_json_object(
            Path(path),
            increment,
            boundary=Path(boundary),
        )


@requires_posix
def test_roundtrip_creates_private_directories_and_file(tmp_path):
    path = tmp_path / "private" / "nested" / "state.json"
    payload = {"message": "Привет 🌲", "items": [1, 2, 3]}

    write_json_object(path, payload, boundary=tmp_path)

    assert read_json_object(path, boundary=tmp_path) == payload
    assert stat.S_IMODE(path.parent.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_read_limit_rejects_existing_and_concurrently_grown_documents(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "state.json"
    path.write_bytes(b'{"value":"1234567890"}')

    with pytest.raises(JsonStorageError, match="exceeds 8 bytes"):
        read_json_object(path, boundary=tmp_path, max_bytes=8)

    original_fstat = durable_json.os.fstat

    def stale_size(descriptor):
        result = original_fstat(descriptor)
        return os.stat_result(
            (
                result.st_mode,
                result.st_ino,
                result.st_dev,
                result.st_nlink,
                result.st_uid,
                result.st_gid,
                8,
                result.st_atime,
                result.st_mtime,
                result.st_ctime,
            )
        )

    monkeypatch.setattr(durable_json.os, "fstat", stale_size)
    with pytest.raises(JsonStorageError, match="exceeds 8 bytes"):
        read_json_object(path, boundary=tmp_path, max_bytes=8)


def test_json_directory_listing_validates_entries_once(tmp_path):
    directory = tmp_path / "sessions"
    directory.mkdir()
    first = directory / "first.json"
    second = directory / "second.json"
    first.write_text("{}\n", encoding="utf-8")
    second.write_text('{"value": 2}\n', encoding="utf-8")
    (directory / "ignored.txt").write_text("not JSON", encoding="utf-8")

    entries = sorted(
        list_json_object_entries(directory, boundary=tmp_path),
        key=lambda entry: entry[0].name,
    )

    assert [path.name for path, _info in entries] == [
        "first.json",
        "second.json",
    ]
    assert [info.st_size for _path, info in entries] == [
        first.stat().st_size,
        second.stat().st_size,
    ]


@pytest.mark.parametrize("unsafe_kind", ["symlink", "hardlink", "directory"])
def test_json_directory_listing_rejects_unsafe_entries(
    tmp_path,
    unsafe_kind,
):
    directory = tmp_path / "sessions"
    directory.mkdir()
    unsafe = directory / "unsafe.json"

    if unsafe_kind == "hardlink" and os.name != "posix":
        pytest.skip("hardlink nlink-based detection is POSIX-specific")

    if unsafe_kind == "symlink":
        outside = tmp_path / "outside.json"
        outside.write_text("{}\n", encoding="utf-8")
        unsafe.symlink_to(outside)
    elif unsafe_kind == "hardlink":
        source = directory / "source"
        source.write_text("{}\n", encoding="utf-8")
        os.link(source, unsafe)
    else:
        unsafe.mkdir()

    with pytest.raises(JsonStorageSecurityError):
        list_json_object_entries(directory, boundary=tmp_path)


def test_failed_replace_preserves_previous_document_and_cleans_temp(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "state.json"
    write_json_object(path, {"generation": 1}, boundary=tmp_path)
    original_replace = durable_json._replace_path

    def fail_primary_replace(source, destination):
        if destination == path:
            raise OSError(errno.ENOSPC, "simulated full disk")
        original_replace(source, destination)

    monkeypatch.setattr(durable_json, "_replace_path", fail_primary_replace)

    with pytest.raises(JsonStorageError, match="atomically replace"):
        write_json_object(path, {"generation": 2}, boundary=tmp_path)

    assert read_json_object(path, boundary=tmp_path) == {"generation": 1}
    assert list(tmp_path.glob("*.tmp")) == []


def test_failed_temp_creation_preserves_previous_document(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    write_json_object(path, {"generation": 1}, boundary=tmp_path)

    def fail_mkstemp(*args, **kwargs):
        raise OSError(errno.ENOSPC, "simulated full disk")

    monkeypatch.setattr(durable_json.tempfile, "mkstemp", fail_mkstemp)

    with pytest.raises(JsonStorageError, match="temporary file"):
        write_json_object(path, {"generation": 2}, boundary=tmp_path)

    assert json.loads(path.read_text(encoding="utf-8")) == {"generation": 1}


def test_write_permission_failure_preserves_previous_document(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "state.json"
    write_json_object(path, {"generation": 1}, boundary=tmp_path)

    def deny_tempfile(*args, **kwargs):
        raise PermissionError(errno.EACCES, "simulated access denial")

    monkeypatch.setattr(durable_json.tempfile, "mkstemp", deny_tempfile)

    with pytest.raises(JsonStorageError, match="temporary file"):
        write_json_object(path, {"generation": 2}, boundary=tmp_path)

    assert read_json_object(path, boundary=tmp_path) == {"generation": 1}


@pytest.mark.parametrize("broken_primary", [b"", b"{partial", b"\xff"])
def test_partial_or_corrupt_primary_recovers_previous_generation(
    tmp_path,
    broken_primary,
):
    path = tmp_path / "state.json"
    write_json_object(path, {"generation": 1}, boundary=tmp_path)
    write_json_object(path, {"generation": 2}, boundary=tmp_path)
    path.write_bytes(broken_primary)

    assert read_json_object(
        path,
        boundary=tmp_path,
        empty_as_missing=True,
    ) == {"generation": 2}
    assert json.loads(path.read_text(encoding="utf-8")) == {"generation": 2}


def test_corrupt_primary_and_backup_raise_instead_of_resetting_state(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{primary", encoding="utf-8")
    path.with_suffix(".json.bak").write_text("{backup", encoding="utf-8")

    with pytest.raises(JsonStorageCorruptError, match="no valid backup"):
        read_json_object(path, boundary=tmp_path)


def test_symlinked_parent_is_rejected_for_reads_and_writes(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    linked = tmp_path / "private"
    linked.symlink_to(outside, target_is_directory=True)
    path = linked / "state.json"

    with pytest.raises(JsonStorageSecurityError, match="symbolic link"):
        read_json_object(path, boundary=tmp_path)
    with pytest.raises(JsonStorageSecurityError, match="symbolic link"):
        write_json_object(path, {"unsafe": True}, boundary=tmp_path)

    assert not (outside / "state.json").exists()


def test_hard_linked_document_is_rejected(tmp_path):
    path = tmp_path / "state.json"
    alias = tmp_path / "alias.json"
    path.write_text("{}\n", encoding="utf-8")
    os.link(path, alias)

    with pytest.raises(JsonStorageSecurityError, match="hard links"):
        read_json_object(path, boundary=tmp_path)
    with pytest.raises(JsonStorageSecurityError, match="hard links"):
        write_json_object(path, {"unsafe": True}, boundary=tmp_path)


def test_concurrent_writers_leave_one_complete_document(tmp_path):
    path = tmp_path / "state.json"

    def write(index):
        write_json_object(
            path,
            {"writer": index, "payload": "🌲" * 2_000},
            boundary=tmp_path,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(64)))

    payload = read_json_object(path, boundary=tmp_path)
    assert payload["writer"] in range(64)
    assert payload["payload"] == "🌲" * 2_000
    assert list(tmp_path.glob("*.tmp")) == []


def test_delete_removes_backup_and_prevents_recovery(tmp_path):
    path = tmp_path / "state.json"
    write_json_object(path, {"generation": 1}, boundary=tmp_path)
    write_json_object(path, {"generation": 2}, boundary=tmp_path)
    assert path.with_suffix(".json.bak").is_file()

    assert delete_json_object(path, boundary=tmp_path) is True

    assert not path.exists()
    assert not path.with_suffix(".json.bak").exists()
    assert read_json_object(path, boundary=tmp_path) == {}
    assert delete_json_object(path, boundary=tmp_path) is False


def test_create_only_never_overwrites_primary_or_backup(tmp_path):
    path = tmp_path / "state.json"

    assert create_json_object(
        path,
        {"generation": 1},
        boundary=tmp_path,
    ) is True
    assert create_json_object(
        path,
        {"generation": 2},
        boundary=tmp_path,
    ) is False
    assert read_json_object(path, boundary=tmp_path) == {"generation": 1}

    path.unlink()
    assert path.with_suffix(".json.bak").exists() is False
    path.with_suffix(".json.bak").write_text(
        '{"generation": 0}\n',
        encoding="utf-8",
    )
    assert create_json_object(
        path,
        {"generation": 3},
        boundary=tmp_path,
    ) is False


def test_concurrent_updates_are_one_serializable_transaction(tmp_path):
    path = tmp_path / "counter.json"
    create_json_object(path, {"count": 0}, boundary=tmp_path)

    def increment(_index):
        def update(payload):
            payload["count"] = int(payload.get("count", 0)) + 1
            return payload

        update_json_object(path, update, boundary=tmp_path)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(increment, range(100)))

    assert read_json_object(path, boundary=tmp_path) == {"count": 100}


def _stat_with_link_count(info: os.stat_result, nlink: int) -> os.stat_result:
    return os.stat_result((
        info.st_mode,
        info.st_ino,
        info.st_dev,
        nlink,
        info.st_uid,
        info.st_gid,
        info.st_size,
        int(info.st_atime),
        int(info.st_mtime),
        int(info.st_ctime),
    ))


def _force_link_count(monkeypatch, target: Path, nlink: int) -> None:
    original_lstat = Path.lstat

    def spoofed(self):
        info = original_lstat(self)
        if self == target:
            return _stat_with_link_count(info, nlink)
        return info

    monkeypatch.setattr(Path, "lstat", spoofed)


def test_replaced_inode_link_count_is_not_an_unsafe_hardlink(tmp_path, monkeypatch):
    # ``os.replace`` leaves the outgoing inode with zero links while the name
    # still resolves.  A concurrent validator must not read that as aliasing.
    path = tmp_path / "state.json"
    create_json_object(path, {"count": 0}, boundary=tmp_path)
    _force_link_count(monkeypatch, path, 0)

    assert validate_storage_path(path, tmp_path) == path


def test_additional_hardlink_is_still_rejected(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    create_json_object(path, {"count": 0}, boundary=tmp_path)
    _force_link_count(monkeypatch, path, 2)

    with pytest.raises(JsonStorageSecurityError, match="hard links"):
        validate_storage_path(path, tmp_path)


def test_listing_tolerates_replaced_inode_link_count(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    create_json_object(path, {"count": 0}, boundary=tmp_path)
    original_stat = os.DirEntry.stat

    def spoofed(self, *, follow_symlinks=True):
        info = original_stat(self, follow_symlinks=follow_symlinks)
        if self.name == "state.json":
            return _stat_with_link_count(info, 0)
        return info

    monkeypatch.setattr(os.DirEntry, "stat", spoofed)

    assert [entry for entry, _ in list_json_object_entries(tmp_path, boundary=tmp_path)] == [path]


def test_updates_are_serialized_across_processes(tmp_path):
    path = tmp_path / "counter.json"
    create_json_object(path, {"count": 0}, boundary=tmp_path)
    context = multiprocessing.get_context("spawn")
    processes = [
        context.Process(
            target=_increment_in_process,
            args=(str(path), str(tmp_path), 25),
        )
        for _ in range(4)
    ]

    started = []
    try:
        for process in processes:
            process.start()
            started.append(process)
        for process in started:
            process.join(timeout=15)
            assert process.exitcode == 0
    finally:
        for process in started:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)

    assert read_json_object(path, boundary=tmp_path) == {"count": 100}


def test_thread_lock_registry_does_not_grow_with_unique_log_paths(tmp_path):
    baseline = len(durable_json._THREAD_LOCKS)

    for index in range(200):
        write_json_object(
            tmp_path / f"run-{index}.json",
            {"index": index},
            boundary=tmp_path,
            backup_existing=False,
        )
    gc.collect()

    assert len(durable_json._THREAD_LOCKS) <= baseline

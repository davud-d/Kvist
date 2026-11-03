# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import sys

import pytest

from kernel.config import self_update


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def core(tmp_path):
    path = tmp_path / "kvist-core"
    path.write_bytes(b"old")
    return path


def _serve(monkeypatch, payload: bytes, runs: bool = True):
    def download(asset, target):
        target.write_bytes(payload)

    monkeypatch.setattr(self_update, "_download", download)
    monkeypatch.setattr(self_update, "_runs", lambda binary: runs)


def test_same_digest_changes_nothing(core, monkeypatch):
    _serve(monkeypatch, b"new")
    assert self_update.update_core(core, "a", {"a": _digest(b"old")}) is False
    assert core.read_bytes() == b"old"


def test_new_digest_replaces_the_file(core, monkeypatch):
    _serve(monkeypatch, b"new")
    assert self_update.update_core(core, "a", {"a": _digest(b"new")}) is True
    assert core.read_bytes() == b"new"
    assert not list(core.parent.glob(".*update"))


def test_digest_mismatch_keeps_old_binary(core, monkeypatch):
    _serve(monkeypatch, b"tampered")
    with pytest.raises(self_update.UpdateError):
        self_update.update_core(core, "a", {"a": _digest(b"new")})
    assert core.read_bytes() == b"old"
    assert not list(core.parent.glob(".*update"))


def test_build_that_does_not_start_is_not_installed(core, monkeypatch):
    _serve(monkeypatch, b"new", runs=False)
    with pytest.raises(self_update.UpdateError):
        self_update.update_core(core, "a", {"a": _digest(b"new")})
    assert core.read_bytes() == b"old"


def test_missing_asset_is_ignored(core):
    assert self_update.update_core(core, "a", {}) is False


def test_source_checkout_is_never_updatable(monkeypatch):
    # Tests run from source (any branch): there is nothing to replace.
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert self_update.installed_core() is None


def test_only_the_installers_binary_name_is_updatable(tmp_path, monkeypatch):
    other = tmp_path / "kvist"
    other.write_bytes(b"x")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(other))
    assert self_update.installed_core() is None


def test_switches(monkeypatch, tmp_path):
    monkeypatch.setenv("KVIST_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv(self_update.DISABLE_ENV, raising=False)
    assert self_update.auto_update_enabled() is True
    (tmp_path / "config.json").write_text('{"auto_update": false}', encoding="utf-8")
    assert self_update.auto_update_enabled() is False
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv(self_update.DISABLE_ENV, "0")
    assert self_update.auto_update_enabled() is False


def test_fetch_keeps_only_sha256_digests(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"assets": [{"name": "a", "digest": "sha256:abc"}, {"name": "b"}, {"name": "c", "digest": "md5:1"}]}

    monkeypatch.setattr(self_update.httpx, "get", lambda *a, **k: Response())
    assert self_update.fetch_release_digests() == {"a": "abc"}

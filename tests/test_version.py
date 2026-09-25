# -*- coding: utf-8 -*-
from __future__ import annotations

from importlib.metadata import PackageNotFoundError

import version


def test_kvist_version_reads_installed_distribution_metadata(monkeypatch):
    monkeypatch.setattr(version, "_installed_version", lambda name: "9.9.9")

    assert version.kvist_version() == "9.9.9"


def test_kvist_version_falls_back_to_pyproject_when_not_installed(monkeypatch):
    def _raise(name):
        raise PackageNotFoundError(name)

    monkeypatch.setattr(version, "_installed_version", _raise)

    # The real pyproject.toml next to this checkout.
    assert version.kvist_version() != "unknown"


def test_kvist_version_falls_back_to_pyproject_toml_contents(monkeypatch, tmp_path):
    def _raise(name):
        raise PackageNotFoundError(name)

    monkeypatch.setattr(version, "_installed_version", _raise)
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nversion = "1.2.3"\n', encoding="utf-8")
    monkeypatch.setattr(version, "_PYPROJECT_PATH", pyproject)

    assert version.kvist_version() == "1.2.3"


def test_kvist_version_is_unknown_when_pyproject_is_missing(monkeypatch, tmp_path):
    def _raise(name):
        raise PackageNotFoundError(name)

    monkeypatch.setattr(version, "_installed_version", _raise)
    monkeypatch.setattr(version, "_PYPROJECT_PATH", tmp_path / "missing.toml")

    assert version.kvist_version() == "unknown"


def test_kvist_version_is_unknown_when_pyproject_has_no_version_field(monkeypatch, tmp_path):
    def _raise(name):
        raise PackageNotFoundError(name)

    monkeypatch.setattr(version, "_installed_version", _raise)
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "kvist"\n', encoding="utf-8")
    monkeypatch.setattr(version, "_PYPROJECT_PATH", pyproject)

    assert version.kvist_version() == "unknown"

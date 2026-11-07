# -*- coding: utf-8 -*-
"""The flat customer-VM layout: projects and Kvist's home are one directory."""
from __future__ import annotations

import json
import tarfile
from pathlib import Path

from kvist_runner.config import load_config
from kvist_runner.workspace import write_archive


def write_config(path: Path, **extra) -> Path:
    path.write_text(json.dumps({"machine_id": 1, "gateway_url": "wss://x", "token": "t" * 20, **extra}))
    return path


def test_dirs_default_under_root(tmp_path):
    config = load_config(write_config(tmp_path / "runner.json", root=str(tmp_path)))
    assert config.work_dir == tmp_path / "work"
    assert config.home_dir == tmp_path / "home"


def test_dirs_can_be_the_same_home(tmp_path):
    home = tmp_path / "home-kvist"
    config = load_config(write_config(tmp_path / "runner.json", work_dir=str(home), home_dir=str(home)))
    assert config.work_dir == config.home_dir == home


def test_archive_of_home_skips_hidden_top_level(tmp_path):
    home = tmp_path / "kvist"
    (home / "project" / ".git").mkdir(parents=True)
    (home / "project" / ".git" / "HEAD").write_text("ref")
    (home / ".kvist").mkdir()
    (home / ".kvist" / "config.json").write_text("secret")
    (home / ".bashrc").write_text("PS1=x")
    target = tmp_path / "out" / "a.tar.gz"
    write_archive(home, target, skip_hidden=True)
    with tarfile.open(target) as archive:
        names = archive.getnames()
    assert "kvist/project/.git/HEAD" in names
    assert not any(name.startswith("kvist/.") for name in names)


def test_archive_of_one_project_keeps_its_dotfiles(tmp_path):
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    (project / ".git" / "HEAD").write_text("ref")
    target = tmp_path / "a.tar.gz"
    write_archive(project, target)
    with tarfile.open(target) as archive:
        assert "project/.git/HEAD" in archive.getnames()

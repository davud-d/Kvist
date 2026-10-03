from __future__ import annotations

import tomllib
from pathlib import Path

from kvist_web.http_api import client_root


ROOT = Path(__file__).resolve().parents[3]
WEB_PACKAGE = ROOT / "frontends" / "web-local" / "kvist_web"


def test_web_runtime_and_every_client_asset_are_declared_for_wheels():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    packages = set(config["tool"]["setuptools"]["packages"])
    assert {
        "kvist_web",
        "kvist_web.runtime",
        "kvist_web.ssh",
        "kvist_web.bridge",
        "kvist_web.installers",
    } <= packages

    declared = set(config["tool"]["setuptools"]["package-data"]["kvist_web"])
    assets = {
        str(path.relative_to(WEB_PACKAGE)).replace("\\", "/")
        for path in client_root().rglob("*")
        if path.is_file()
    }
    assert assets <= declared


def test_client_assets_are_package_owned_not_prefix_data_files():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert client_root() == WEB_PACKAGE / "client"
    assert "data-files" not in config["tool"]["setuptools"]

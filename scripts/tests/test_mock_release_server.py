# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib.util
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "release" / "mock_release_server.py"
_SPEC = importlib.util.spec_from_file_location("mock_release_server", _PATH)
mock = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mock)


@pytest.fixture
def base(tmp_path, monkeypatch):
    binary = tmp_path / "core"
    binary.write_bytes(bytes(range(256)))
    monkeypatch.setitem(mock.ASSETS, "kvist-linux-x64", (binary,))
    server = mock.make_server(0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}{mock.RELEASE_PATH}"
    server.shutdown()
    server.server_close()


def _get(url: str, **headers: str):
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers))


def test_serves_an_asset_whole_and_by_range(base):
    assert _get(f"{base}/kvist-linux-x64").read() == bytes(range(256))
    part = _get(f"{base}/kvist-linux-x64", Range="bytes=10-19")
    assert (part.status, part.read()) == (206, bytes(range(10, 20)))
    assert _get(f"{base}/kvist-linux-x64", Range="bytes=-4").read() == bytes(range(252, 256))


def test_serves_the_real_installer_script(base):
    assert _get(f"{base}/install.sh").read().startswith(b"#!")


def test_unknown_paths_and_missing_assets_are_404(base, monkeypatch):
    monkeypatch.setitem(mock.ASSETS, "kvist-vscode.vsix", ())
    for tail in ("/kvist-vscode.vsix", "/nothing"):
        with pytest.raises(urllib.error.HTTPError) as error:
            _get(base + tail)
        assert error.value.code == 404
    with pytest.raises(urllib.error.HTTPError):
        _get(base.replace(mock.RELEASE_PATH, "/elsewhere") + "/install.sh")

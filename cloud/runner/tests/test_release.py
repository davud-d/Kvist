# -*- coding: utf-8 -*-
"""Updating in place: a release staged beside the running code, the switch, and what it does to a running task."""
from __future__ import annotations

import asyncio
import hashlib
import http.server
import sys
import threading
import time
import zipfile
from pathlib import Path

import pytest
from test_task import KEY, Recorder, config, limits

from kvist_runner import guard, release
from kvist_runner.config import RunnerConfig
from kvist_runner.runner import Runner
from kvist_runner.task import TaskProcess

RELEASE = "0123456789abcdef"
TOKEN = "t" * 20


@pytest.fixture(autouse=True)
def no_systemd(monkeypatch):
    monkeypatch.setattr(guard, "_systemd", False)


def build_wheel(directory: Path, name: str, files: dict[str, str]) -> tuple[Path, str]:
    """A real, minimal wheel: pip installs it like any other."""
    distribution = name.split("-")[0]
    info = f"{distribution}-1.0.dist-info"
    path = directory / f"{distribution}-1.0-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as wheel:
        for member, text in files.items():
            wheel.writestr(member, text)
        wheel.writestr(f"{info}/METADATA", f"Metadata-Version: 2.1\nName: {distribution}\nVersion: 1.0\n")
        wheel.writestr(
            f"{info}/WHEEL", "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        )
        wheel.writestr(f"{info}/RECORD", "")
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


class Server:
    """Serves wheels the way the control server does: only with the machine's bearer token."""

    def __init__(self, directory: Path) -> None:
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                owner.seen.append((self.path, self.headers.get("Authorization")))
                if self.headers.get("User-Agent", "").startswith("Python-urllib"):
                    self.send_error(403)  # what Cloudflare does with urllib's own User-Agent
                    return
                target = directory / self.path.rsplit("/", 1)[-1]
                if self.headers.get("Authorization") != f"Bearer {TOKEN}" or not target.is_file():
                    self.send_error(404)
                    return
                body = target.read_bytes()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args) -> None:
                pass

        self.seen: list[tuple[str, str | None]] = []
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"ws://127.0.0.1:{self.httpd.server_address[1]}/gateway/ws"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()


def configured(tmp_path, url: str) -> RunnerConfig:
    base = config(tmp_path)
    return RunnerConfig(machine_id=1, gateway_url=url, token=TOKEN, root=base.root, kvist_command=base.kvist_command,
                        heartbeat_seconds=0.1)


GOOD = {
    "kernel/__init__.py": "",
    "kernel/agent/__init__.py": "",
    "kernel/agent/agent.py": "VERSION = 'new'\n",
    "kvist_runner/__init__.py": "",
    "kvist_runner/runner.py": "",
}


@pytest.fixture
def served(tmp_path):
    directory = tmp_path / "served"
    directory.mkdir()
    server = Server(directory)
    yield directory, server
    server.close()


def manifest(*wheels: tuple[Path, str], release_id: str = RELEASE) -> dict:
    return {"type": "update", "release": release_id,
            "files": [{"name": path.name, "sha256": digest, "size": path.stat().st_size} for path, digest in wheels]}


async def test_a_release_is_downloaded_checked_installed_beside_and_marked_ready(tmp_path, served):
    directory, server = served
    kvist = build_wheel(directory, "kvist-1.0", {k: v for k, v in GOOD.items() if k.startswith("kernel/")})
    runner = build_wheel(directory, "kvist_runner-1.0", {k: v for k, v in GOOD.items() if k.startswith("kvist_")})
    cfg = configured(tmp_path, server.url)
    assert await release.stage(cfg, manifest(kvist, runner)) == RELEASE
    assert release.is_ready(cfg, RELEASE)
    assert (release.site_dir(cfg, RELEASE) / "kernel" / "agent" / "agent.py").is_file()
    assert all(auth == f"Bearer {TOKEN}" for _path, auth in server.seen)
    assert server.seen[0][0].startswith(f"/gateway/release/{RELEASE}/")
    # Staging only builds: nothing runs from it until the pointer is written.
    assert release.current(cfg) == "" and release.overlay_env(cfg) == {}
    release.activate(cfg, RELEASE)
    assert release.current(cfg) == RELEASE
    assert release.overlay_env(cfg) == {"PYTHONPATH": str(release.site_dir(cfg, RELEASE))}
    assert release.needs_restart(cfg) is True


async def test_a_wheel_that_does_not_match_its_checksum_is_refused(tmp_path, served):
    directory, server = served
    kvist = build_wheel(directory, "kvist-1.0", GOOD)
    cfg = configured(tmp_path, server.url)
    broken = manifest((kvist[0], "0" * 64))
    with pytest.raises(release.ReleaseError, match="checksum"):
        await release.stage(cfg, broken)
    assert not release.is_ready(cfg, RELEASE)


async def test_a_release_whose_code_does_not_start_is_never_ready(tmp_path, served):
    directory, server = served
    broken = {**GOOD, "kernel/agent/agent.py": "import a_dependency_the_image_lacks\n"}
    bad = build_wheel(directory, "kvist-1.0", broken)
    cfg = configured(tmp_path, server.url)
    with pytest.raises(release.ReleaseError, match="does not start"):
        await release.stage(cfg, manifest(bad))
    assert not release.is_ready(cfg, RELEASE)
    with pytest.raises(release.ReleaseError, match="not staged"):
        release.activate(cfg, RELEASE)


async def test_a_download_the_server_refuses_fails_cleanly(tmp_path, served):
    _directory, server = served
    cfg = configured(tmp_path, server.url)
    with pytest.raises(release.ReleaseError, match="download"):
        await release.stage(cfg, {"release": RELEASE, "files": [
            {"name": "missing-1.0-py3-none-any.whl", "sha256": "a" * 64, "size": 1}]})


@pytest.mark.parametrize(
    "message",
    [
        {"release": "../etc", "files": [{"name": "a.whl", "sha256": "a" * 64}]},
        {"release": RELEASE, "files": [{"name": "../a.whl", "sha256": "a" * 64}]},
        {"release": RELEASE, "files": [{"name": "a.whl", "sha256": "short"}]},
        {"release": RELEASE, "files": []},
    ],
)
async def test_a_malformed_update_message_is_refused_before_anything_is_written(tmp_path, message):
    cfg = configured(tmp_path, "ws://127.0.0.1:1/gateway/ws")
    with pytest.raises(release.ReleaseError, match="malformed|not a wheel"):
        await release.stage(cfg, message)
    assert not (tmp_path / "releases").exists()


def test_a_pointer_to_a_release_that_is_not_intact_is_ignored(tmp_path):
    cfg = configured(tmp_path, "ws://x/gateway/ws")
    (tmp_path / "current-release").write_text(RELEASE)
    assert release.current(cfg) == "" and release.needs_restart(cfg) is False


def test_the_release_in_use_is_what_a_restart_is_not_needed_for(tmp_path, monkeypatch):
    cfg = configured(tmp_path, "ws://x/gateway/ws")
    (tmp_path / "releases" / RELEASE / "site").mkdir(parents=True)
    (tmp_path / "releases" / RELEASE / release.READY).write_text(RELEASE)
    release.activate(cfg, RELEASE)
    monkeypatch.setattr(sys, "path", [str(release.site_dir(cfg, RELEASE)), *sys.path])
    assert release.needs_restart(cfg) is False


def staged(tmp_path, release_id: str = RELEASE) -> RunnerConfig:
    cfg = configured(tmp_path, "ws://x/gateway/ws")
    (tmp_path / "releases" / release_id / "site").mkdir(parents=True)
    (tmp_path / "releases" / release_id / release.READY).write_text(release_id)
    return cfg


def start(runner: Runner, cfg: RunnerConfig, goal: str, task_id: int = 5) -> TaskProcess:
    recorder = Recorder()
    task = TaskProcess(
        task_id=task_id, goal=goal, repo_url="", resume=False,
        env={"AGENT_LLM_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": KEY}, limits=limits(), config=cfg,
        emit=runner.emit, on_change=recorder.change,
    )
    runner.tasks[task_id] = task
    runner._runs[task_id] = asyncio.create_task(runner._run_task(task))
    return task


async def running(task: TaskProcess) -> None:
    for _ in range(200):
        if task.running:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("the fake Kvist did not start")


async def test_the_switch_stops_a_running_kvist_in_seconds_and_restarts_on_the_new_release(tmp_path):
    cfg = staged(tmp_path)
    runner = Runner(cfg)
    task = start(runner, cfg, "hang")
    await running(task)
    began = time.monotonic()
    await runner._switch(RELEASE)
    assert time.monotonic() - began < release_budget()
    assert runner.restart.is_set()
    exits = [m for m in runner._outbox if m["type"] == "task_exit"]
    assert [(m["task_id"], m["reason"]) for m in exits] == [(5, "core_updated")]
    assert release.current(cfg) == RELEASE
    # What a Kvist started after the restart gets in its environment.
    assert task._child_env()["PYTHONPATH"] == str(release.site_dir(cfg, RELEASE))


def release_budget() -> float:
    from kvist_runner.runner import SWITCH_GRACE_SECONDS

    return SWITCH_GRACE_SECONDS + 3.0


async def test_a_kvist_that_will_not_stop_is_killed_after_the_grace(tmp_path, monkeypatch):
    monkeypatch.setattr("kvist_runner.runner.SWITCH_GRACE_SECONDS", 0.5)
    cfg = staged(tmp_path)
    runner = Runner(cfg)
    task = start(runner, cfg, "stubborn")
    await running(task)
    # The fake ends on cancel/eof by itself; take that away so only the kill can end it.
    task._cancel = _nothing
    task.close_stdin = _nothing
    began = time.monotonic()
    await runner._switch(RELEASE)
    assert time.monotonic() - began < 3.0
    assert task.process.returncode is not None
    assert [m["reason"] for m in runner._outbox if m["type"] == "task_exit"] == ["core_updated"]


async def _nothing(*_args) -> None:
    return None


async def test_a_switch_to_a_release_that_is_not_staged_touches_nothing(tmp_path):
    cfg = configured(tmp_path, "ws://x/gateway/ws")
    runner = Runner(cfg)
    task = start(runner, cfg, "hang")
    await running(task)
    await runner._switch(RELEASE)
    assert not runner.restart.is_set() and task.running
    assert [m["type"] for m in runner._outbox if m["type"] == "update_failed"] == ["update_failed"]
    await task.stop()
    await asyncio.wait({runner._runs[5]}, timeout=5)


async def test_staging_does_not_disturb_a_running_task(tmp_path, served):
    directory, server = served
    kvist = build_wheel(directory, "kvist-1.0", {k: v for k, v in GOOD.items() if k.startswith("kernel/")})
    runner_wheel = build_wheel(directory, "kvist_runner-1.0", {k: v for k, v in GOOD.items() if k.startswith("kvist_")})
    cfg = configured(tmp_path, server.url)
    runner = Runner(cfg)
    task = start(runner, cfg, "hang")
    await running(task)
    await runner._stage(manifest(kvist, runner_wheel))
    assert [m["type"] for m in runner._outbox if m["type"].startswith("update_")] == ["update_ready"]
    assert task.running and not runner.restart.is_set()
    # Staged is not switched: a Kvist started now still runs the old code.
    assert "PYTHONPATH" not in task._child_env()
    await task.stop()
    await asyncio.wait({runner._runs[5]}, timeout=5)

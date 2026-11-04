# -*- coding: utf-8 -*-
"""Remote core, version skew: a daemon started by an OLDER Kvist is still running when a newer interface connects.

A daemon outlives the interfaces that start it, so after Kvist is updated on a server the old code keeps running until it is
restarted. The old code is a real commit (default: the one before the provider was lent to the daemon), unpacked with
``git archive``. The new interface must say so in its own dialog, restart the agent when it is allowed to, and then work with the
provider of this computer. Needs git and a POSIX system. Exit code 0 means every check passed.

    python scripts/e2e_remote_skew.py [OLD_COMMIT]
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_remote_smoke import TIMEOUT, Frontend, operation  # noqa: E402
from e2e_tools_smoke import ROOT, SCENARIOS, FakeProvider, configure  # noqa: E402

OLD_COMMIT = "c3602f4"


def unpack_old_sources(commit: str, into: Path) -> None:
    archive = subprocess.run(
        ["git", "archive", commit, "kernel", "frontends/tui/classic", "frontends/zed", "pyproject.toml", "requirements.txt"],
        cwd=ROOT, capture_output=True, check=True,
    )
    subprocess.run(["tar", "-x", "-C", str(into)], input=archive.stdout, check=True)


def write_tools(directory: Path, server_home: Path, core_entry: Path) -> None:
    ssh = directory / "ssh"
    ssh.write_text(f'#!/bin/sh\nfor last; do :; done\nHOME="{server_home}" USERPROFILE="{server_home}" exec sh -c "$last"\n')
    core = directory / "kvist-core"
    core.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{core_entry}" "$@"\n')
    for script in (ssh, core):
        script.chmod(0o755)


def answer_dialogs_until(frontend: Frontend, event_type: str, choose: str, seen: list[dict]) -> dict:
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        try:
            event = frontend.events.get(timeout=1)
        except Exception:  # noqa: BLE001 - queue.Empty; the loop is the timeout
            continue
        seen.append(event)
        if event.get("type") == "input_request":
            values = [option["value"] for option in event.get("options") or []]
            reply = {"request_id": event["request_id"], "value": choose if choose in values else values[0]}
            frontend.process.stdin.write(json.dumps(reply) + "\n")
            frontend.process.stdin.flush()
        if event.get("type") == event_type and (event_type != "hello" or event.get("provider")):
            return event
    raise TimeoutError(f"no '{event_type}' in {TIMEOUT}s; got {[event.get('type') for event in seen]}")


def main() -> int:
    commit = sys.argv[1] if len(sys.argv) > 1 else OLD_COMMIT
    provider = FakeProvider()
    with tempfile.TemporaryDirectory(prefix="kvist-skew-") as scratch:
        base = Path(scratch)
        old_sources = base / "old-sources"
        computer, server, workspace, tools, stand_in = (base / name for name in ("computer", "server", "workspace", "tools", "stand-in"))
        for folder in (old_sources, computer, server, workspace, tools, stand_in):
            folder.mkdir()
        unpack_old_sources(commit, old_sources)
        # The old code needs a provider of its own (it knows nothing about lending); this computer has the same one.
        configure(server, provider.url)
        configure(computer, provider.url)
        os.environ.update(HOME=str(server), AGENT_SANDBOX_NET="allow", AGENT_WRITE_APPROVAL="0", AGENT_API_INTERVAL="0", PYTHONUTF8="1")

        old = subprocess.run(
            [sys.executable, str(old_sources / "kernel" / "__main__.py"), "--remote-op", "start", "--workspace", str(workspace)],
            env=dict(os.environ, AGENT_LLM_PROVIDER="custom:fake"), capture_output=True, text=True, timeout=60,
        )
        started = json.loads(old.stdout.splitlines()[-1])
        assert started["ok"], old.stdout + old.stderr
        assert "revision" not in started["daemon"], "the old daemon already knows revisions; pick an older commit"
        old_pid = started["daemon"]["pid"]
        print(f"ok  an old daemon is running (pid {old_pid}, {commit})")

        # The server now has the new code (what installing it does); the old process keeps running the old one.
        write_tools(tools, server, ROOT / "kernel" / "__main__.py")
        os.environ["PATH"] = f"{tools}:{os.environ['PATH']}"
        seen: list[dict] = []
        frontend = Frontend(computer, tools, workspace, stand_in)
        try:
            frontend.collect_until("hello", TIMEOUT)
            hello = answer_dialogs_until(frontend, "hello", "restart", seen)
            dialog = next((event for event in seen if event.get("type") == "input_request"), None)
            assert dialog is not None and "older Kvist" in dialog["title"], f"no restart dialog; saw {[e.get('type') for e in seen]}"
            print("ok  the interface said the agent is older and asked to restart it, in its own dialog")

            assert hello["provider"] == "custom:fake", hello
            status = json.loads(subprocess.run(
                [sys.executable, "-m", "kernel", "--remote-op", "status", "--workspace", str(workspace)],
                cwd=ROOT, env=dict(os.environ), capture_output=True, text=True, timeout=60,
            ).stdout.splitlines()[-1])
            assert status["daemon"]["pid"] != old_pid and status["daemon"]["revision"] >= 2, status
            print("ok  the agent was restarted with the new code and works with this computer's provider")

            server_configs = [path.read_text(errors="replace") for path in server.rglob("config.json")]
            frontend.process.stdin.write(SCENARIOS[0][0] + "\n")
            frontend.process.stdin.flush()
            frontend.collect_until("turn_end", TIMEOUT)
            assert (workspace / "hello.txt").read_bytes() == SCENARIOS[0][2]
            print("ok  a turn runs")
            # What the server's own config had is exactly what it had before (the old setup is not touched).
            assert [path.read_text(errors="replace") for path in server.rglob("config.json")] == server_configs
        except (AssertionError, TimeoutError) as error:
            print(f"FAIL: {error}")
            print("--- stderr:\n" + "\n".join(frontend.stderr_lines[-20:]))
            return 1
        finally:
            if frontend.process.poll() is None:
                frontend.stop()
            print(operation("stop", workspace, server))
            provider.server.shutdown()
    print("remote skew smoke passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

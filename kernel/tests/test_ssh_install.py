# -*- coding: utf-8 -*-
"""Copying this checkout to a machine, for a build the machine's own Kvist does not yet have."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from kernel.ssh import deploy
from kernel.ssh.command import call_op
from kernel.tests.test_ssh_real_sshd import pytestmark as _needs_sshd  # noqa: F401
from kernel.tests.test_ssh_real_sshd import server, target_for, trust  # noqa: F401  (fixtures and helpers)


def test_the_core_and_what_it_imports_are_copied_and_no_tests():
    names = deploy.source_files()

    assert "kernel/ssh/deploy.py" in names
    assert "kernel/main.py" in names
    assert "frontends/tui/classic/terminal_banner.py" in names
    assert "pyproject.toml" in names and "requirements.txt" in names
    assert not [name for name in names if "/tests/" in name]
    assert not [name for name in names if name.startswith(("site/", "docs/", "frontends/gui/"))]


def test_the_setup_checks_python_replaces_a_release_binary_only_once_and_says_what_to_install_when_venv_is_missing():
    script = deploy.remote_script("/opt/kv")

    assert "sys.version_info >= (3, 11)" in script
    assert "apt install python3-venv" in script
    assert "grep -q kvist-dev-shim" in script and 'mv "$core" "$core.release"' in script
    assert "pip install -q -r requirements.txt" in script
    assert "pip install" not in deploy.remote_script("/opt/kv", install_dependencies=False)


def _stub(directory: Path, name: str, body: str) -> None:
    program = directory / name
    program.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    program.chmod(0o755)


def test_an_environment_left_half_made_without_pip_is_thrown_away_and_made_again(tmp_path):
    """What Debian leaves when python3-venv is missing: a .venv folder with a python in it and no pip."""
    import subprocess

    work, tools = tmp_path / "work", tmp_path / "tools"
    (work / ".venv" / "bin").mkdir(parents=True)
    tools.mkdir()
    _stub(work / ".venv" / "bin", "python", 'echo "No module named pip" >&2\nexit 1\n')
    _stub(tools, "python3", 'mkdir -p .venv/bin\nprintf "#!/bin/sh\\nexit 0\\n" > .venv/bin/python\nchmod +x .venv/bin/python\n')

    done = subprocess.run(["sh", "-c", deploy.venv_guard(needs_pip=True)], cwd=work, env={"PATH": f"{tools}:/usr/bin:/bin"}, capture_output=True, text=True)

    assert done.returncode == 0, done.stderr
    assert subprocess.run([str(work / ".venv" / "bin" / "python"), "-m", "pip", "--version"]).returncode == 0


def test_when_the_environment_cannot_be_made_the_leftover_is_removed_and_the_package_to_install_is_named(tmp_path):
    import subprocess

    work, tools = tmp_path / "work", tmp_path / "tools"
    work.mkdir()
    tools.mkdir()
    _stub(tools, "python3", "mkdir -p .venv/bin\necho 'ensurepip is not available' >&2\nexit 1\n")

    done = subprocess.run(["sh", "-c", deploy.venv_guard(needs_pip=True)], cwd=work, env={"PATH": f"{tools}:/usr/bin:/bin"}, capture_output=True, text=True)

    assert done.returncode == 21 and "apt install python3-venv" in done.stderr
    assert not (work / ".venv").exists()


def test_a_working_environment_is_left_alone(tmp_path):
    import subprocess

    work = tmp_path / "work"
    (work / ".venv" / "bin").mkdir(parents=True)
    _stub(work / ".venv" / "bin", "python", "exit 0\n")
    marker = work / ".venv" / "keep"
    marker.write_text("x")

    done = subprocess.run(["sh", "-c", deploy.venv_guard(needs_pip=True)], cwd=work, env={"PATH": "/usr/bin:/bin"}, capture_output=True, text=True)

    assert done.returncode == 0 and marker.exists()


def test_a_machine_gets_the_sources_and_a_kvist_core_that_runs_them_through_real_ssh(server):
    trust(server)
    folder = server.home / "kvist-src"
    folder.mkdir()
    # The machine's own Python environment is the one running this test, so nothing is downloaded or installed here.
    (folder / ".venv").symlink_to(Path(sys.prefix))

    version = deploy.install(target_for(server), str(folder), install_dependencies=False)

    core = server.home / ".local" / "share" / "kvist" / "kvist-core"
    assert version
    assert os.access(core, os.X_OK) and "kvist-dev-shim" in core.read_text()
    assert (folder / "kernel" / "ssh" / "deploy.py").is_file() and not (folder / "kernel" / "tests").exists()
    # What was there before is kept, and a second run does not push it aside again.
    assert (core.parent / "kvist-core.release").is_file()
    deploy.install(target_for(server), str(folder), install_dependencies=False)
    kept = (core.parent / "kvist-core.release").read_text()
    assert "kvist-dev-shim" not in kept and "__main__.py" in kept

    # And it now answers the remote operations an old Kvist refused.
    code, answer, stderr = call_op(target_for(server, str(server.home)), "probe")
    assert code == 0, stderr
    assert answer["ok"] is True and answer["protocol_version"] == 1


def test_a_connection_that_ends_before_the_sources_were_read_is_reported_at_once_and_does_not_hang(monkeypatch, tmp_path):
    # An ssh that dies without reading its input used to leave tar blocked on a full pipe, and the install waited for ever.
    import time

    stub = tmp_path / "ssh-that-dies"
    stub.write_text("#!/bin/sh\necho 'Permission denied (publickey).' >&2\nexit 255\n")
    stub.chmod(0o755)
    monkeypatch.setenv("KVIST_SSH", str(stub))
    from kernel.ssh.command import Target
    from kernel.ssh.paths import RemoteError

    started = time.monotonic()
    with pytest.raises(RemoteError, match="Permission denied"):
        deploy.install(Target("me@box", None, "/"), "$HOME/kvist-src", install_dependencies=False)

    assert time.monotonic() - started < 30


def test_an_unknown_machine_is_said_so(capsys):
    assert deploy.main(["nowhere"]) == 2
    assert "kvist --machines" in capsys.readouterr().err


# --- this computer's own program, copied as it is -----------------------------------------------------


def _program(directory: Path, version_works: bool = True) -> Path:
    program = directory / "kvist-core-built-here"
    body = '#!/bin/sh\nif [ "$1" = "--version" ]; then echo "kvist 9.9.9"; exit 0; fi\n' if version_works else (
        '#!/bin/sh\necho "error while loading shared libraries: GLIBC_2.99 not found" >&2\nexit 127\n'
    )
    program.write_text(body, encoding="utf-8")
    program.chmod(0o755)
    return program


def test_this_computers_program_is_copied_tried_there_and_then_becomes_the_machines_core(server, tmp_path):
    trust(server)
    core = server.home / ".local" / "share" / "kvist" / "kvist-core"
    old = core.read_text()

    version = deploy.install_binary(target_for(server), _program(tmp_path))

    assert version == "kvist 9.9.9"
    assert os.access(core, os.X_OK) and "9.9.9" in core.read_text()
    # What was there is kept, and nothing half-copied is left beside it.
    assert (core.parent / "kvist-core.previous").read_text() == old
    assert sorted(path.name for path in core.parent.iterdir()) == ["kvist-core", "kvist-core.previous"]


def test_a_program_that_does_not_run_on_the_machine_is_refused_and_the_machine_is_left_as_it_was(server, tmp_path):
    trust(server)
    core = server.home / ".local" / "share" / "kvist" / "kvist-core"
    before = core.read_text()

    with pytest.raises(deploy.RemoteError, match="GLIBC_2.99"):
        deploy.install_binary(target_for(server), _program(tmp_path, version_works=False))

    assert core.read_text() == before
    assert sorted(path.name for path in core.parent.iterdir()) == ["kvist-core"]


def test_a_machine_of_another_kind_is_not_given_a_program_built_here(server, tmp_path):
    trust(server)

    with pytest.raises(deploy.RemoteError, match="does not run there"):
        deploy.install_binary(target_for(server), _program(tmp_path), local=("darwin", "arm64"))


def test_an_installation_with_nothing_there_yet_creates_the_folder(server, tmp_path):
    trust(server)
    (server.home / ".local" / "share" / "kvist" / "kvist-core").unlink()

    deploy.install_binary(target_for(server), _program(tmp_path))

    assert (server.home / ".local" / "share" / "kvist" / "kvist-core").is_file()
    assert not (server.home / ".local" / "share" / "kvist" / "kvist-core.previous").exists()


def test_the_system_names_are_compared_the_way_people_mean_them():
    assert deploy._platform_of("Linux", "amd64") == deploy._platform_of("linux", "x86_64")
    assert deploy._platform_of("Linux", "aarch64") == deploy._platform_of("Linux", "arm64")
    assert deploy._platform_of("Linux", "x86_64") != deploy._platform_of("Linux", "arm64")


def test_a_checkout_that_is_not_a_frozen_program_gets_its_sources_copied_instead(monkeypatch, server):
    trust(server)
    calls = []
    monkeypatch.setattr(deploy, "install", lambda target: calls.append("sources") or "kvist 1")
    monkeypatch.setattr(deploy, "local_binary", lambda: None)

    assert deploy.install_core(target_for(server)) == "kvist 1" and calls == ["sources"]

# -*- coding: utf-8 -*-
"""Stage 1.1 regression suite.

Covers workspace-secret masking, .kvist isolation, weak-mode fail-closed
behaviour, process-tree teardown of detached descendants, bounded output
capture, shebang interpreter attestation, and project toolchain / venv support.
Tests use the real ``TerminalExecutor`` / ``process_tree`` paths wherever
practical; strong-sandbox cases skip when Bubblewrap is unavailable.
"""
from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
from pathlib import Path

import pytest


from tests.conftest import requires_posix

import terminal as terminal_module
from process_tree import (

    ProcessSupervisionError,
    _BoundedReader,
    cgroup_supervision_available,
    run_supervised,
)
from sandbox import resolve_bubblewrap_path, wrap_command_for_sandbox, SandboxMode
from secret_paths import (
    UnsafeSecretPathError,
    enumerate_secret_targets,
    is_secret_basename,
    secret_mask_digest,
)
from terminal import (
    ApprovalRequiredError,
    HardDenyError,
    TerminalExecutor,
    _controlled_child_path,
    _is_trusted_system_executable,
    _parse_shebang,
)

pytestmark = pytest.mark.slow

BWRAP = resolve_bubblewrap_path()
requires_bwrap = pytest.mark.skipif(BWRAP is None, reason="bubblewrap not installed")
requires_cgroup = pytest.mark.skipif(
    not cgroup_supervision_available(), reason="cgroup v2 scope unavailable"
)


def _write_policy(workspace: Path, allow) -> None:
    kv = workspace / ".kvist"
    kv.mkdir(exist_ok=True)
    (kv / "permissions.json").write_text(json.dumps({"allow": list(allow)}), encoding="utf-8")


def _bwrap_executor(workspace: Path, monkeypatch, allow=("*",), timeout=10) -> TerminalExecutor:
    monkeypatch.setenv("AGENT_SANDBOX_MODE", "auto")
    _write_policy(workspace, allow)
    ex = TerminalExecutor(workspace, timeout=timeout)
    assert ex.sandbox_mode is SandboxMode.BUBBLEWRAP
    return ex


# ─────────────────────────── secret classification ───────────────────────────

@pytest.mark.parametrize(
    "name,secret",
    [
        (".env", True),
        (".env.local", True),
        (".env.production", True),
        ("id_rsa", True),
        ("id_ed25519", True),
        ("server.pem", True),
        ("private.key", True),
        ("bundle.p12", True),
        ("cert.pfx", True),
        ("credentials", True),
        (".git-credentials", True),
        ("main.py", False),
        ("README.md", False),
        ("keyboard.txt", False),
    ],
)
def test_secret_basename_classification(name, secret):
    assert is_secret_basename(name) is secret


def test_enumerate_secret_targets_finds_nested(tmp_path):
    (tmp_path / ".env").write_text("A=1")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "server.pem").write_text("key")
    (tmp_path / ".ssh").mkdir()
    (tmp_path / ".ssh" / "id_rsa").write_text("k")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("ok")
    files, dirs = enumerate_secret_targets(tmp_path)
    names = {p.name for p in files}
    dir_names = {p.name for p in dirs}
    assert ".env" in names and "server.pem" in names
    assert ".ssh" in dir_names
    # We must not descend into a masked secret directory.
    assert all(".ssh" not in p.parts[:-1] for p in files)


def test_secret_mask_digest_changes_when_secret_added(tmp_path):
    before = secret_mask_digest(tmp_path)
    (tmp_path / ".env").write_text("A=1")
    after = secret_mask_digest(tmp_path)
    assert before != after


def test_secret_enumeration_fails_closed_when_directory_cannot_be_scanned(
    tmp_path,
    monkeypatch,
):
    hidden = tmp_path / "temporarily-unreadable"
    hidden.mkdir()
    (hidden / ".env").write_text("A=1", encoding="utf-8")
    original_iterdir = Path.iterdir

    def deny_hidden(path):
        if path == hidden:
            raise PermissionError("fault-injected unreadable directory")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", deny_hidden)

    with pytest.raises(UnsafeSecretPathError, match="complete secret mask"):
        enumerate_secret_targets(tmp_path)


def test_external_hardlink_alias_is_included_in_protected_file_masks(
    tmp_path,
    tmp_path_factory,
):
    outside = tmp_path_factory.mktemp("outside-hardlink-mask")
    external = outside / "public.txt"
    external.write_text("boundary fixture\n", encoding="utf-8")
    alias = tmp_path / "alias.txt"
    os.link(external, alias)

    files, directories = enumerate_secret_targets(tmp_path)

    assert directories == []
    assert files == [alias]


# ─────────────────────── strong-mode secret masking (bwrap) ──────────────────

@requires_bwrap
@pytest.mark.parametrize(
    "cmd_factory",
    [
        pytest.param(lambda ws: _py_reader(ws), id="python-open"),
        pytest.param(lambda ws: _shell_reader(ws), id="shell-redirect"),
        pytest.param(
            lambda ws: ["awk", 'BEGIN{p="." "env"; getline x < p; print x}'],
            id="awk-dynamic",
        ),
        pytest.param(lambda ws: _sed_reader(ws), id="sed-script"),
    ],
)
def test_workspace_secret_not_readable_in_strong_mode(tmp_path, monkeypatch, cmd_factory):
    (tmp_path / ".env").write_text("OPENAI_API_KEY=super-secret\n", encoding="utf-8")
    (tmp_path / "input.txt").write_text("x\n", encoding="utf-8")
    cmd = cmd_factory(tmp_path)
    ex = _bwrap_executor(tmp_path, monkeypatch)
    result = ex.run(cmd)
    assert "super-secret" not in result.stdout
    assert "super-secret" not in result.stderr


def _py_reader(ws: Path):
    (ws / "read_env.py").write_text('print(open(".env").read())', encoding="utf-8")
    return ["python3", "read_env.py"]


def _sed_reader(ws: Path):
    (ws / "read.sed").write_text("1r .env\n", encoding="utf-8")
    return ["sed", "-n", "--file=read.sed", "input.txt"]


def _shell_reader(ws: Path):
    (ws / "leak.sh").write_text("cat < .env\n", encoding="utf-8")
    return ["sh", "leak.sh"]


@requires_bwrap
def test_symlink_to_secret_is_masked_in_strong_mode(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("OPENAI_API_KEY=super-secret\n", encoding="utf-8")
    (tmp_path / "link").symlink_to(tmp_path / ".env")
    (tmp_path / "reader.py").write_text(
        "import sys\n"
        "try:\n"
        "    sys.stdout.write(open('link').read())\n"
        "except OSError as e:\n"
        "    sys.stdout.write('blocked')\n",
        encoding="utf-8",
    )
    ex = _bwrap_executor(tmp_path, monkeypatch)
    result = ex.run(["python3", "reader.py"])
    assert "super-secret" not in result.stdout


@requires_bwrap
@pytest.mark.parametrize(
    "setup",
    [
        pytest.param(("top", ".env", "SECRET_TOP"), id="hardlink-top-env"),
        pytest.param(("sub", "sub/id_rsa", "SECRET_SUB"), id="hardlink-in-subdir"),
        pytest.param(("git", ".git/real.pem", "SECRET_GIT"), id="hardlink-in-git"),
    ],
)
def test_hardlink_to_secret_is_masked_in_strong_mode(tmp_path, monkeypatch, setup):
    _kind, rel, marker = setup
    secret = tmp_path / rel
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_text(f"{marker}=leak\n", encoding="utf-8")
    link = tmp_path / "innocent.txt"
    os.link(secret, link)  # second name for the same inode
    ex = _bwrap_executor(tmp_path, monkeypatch)
    result = ex.run(["cat", "innocent.txt"])
    assert marker not in result.stdout
    assert marker not in result.stderr


@requires_bwrap
def test_compiled_helper_cannot_read_secret(tmp_path, monkeypatch):
    cc = terminal_module.shutil.which("cc") or terminal_module.shutil.which("gcc")
    if cc is None:
        pytest.skip("no C compiler")
    (tmp_path / ".env").write_text("OPENAI_API_KEY=super-secret\n", encoding="utf-8")
    src = tmp_path / "leak.c"
    src.write_text(
        '#include <stdio.h>\n'
        'int main(){FILE*f=fopen(".env","r");if(!f){printf("blocked");return 0;}'
        'char b[256];while(fgets(b,sizeof b,f))fputs(b,stdout);return 0;}\n',
        encoding="utf-8",
    )
    # Compile on the host (outside the sandbox), then execute the binary sandboxed.
    import subprocess as sp

    sp.run([cc, str(src), "-o", str(tmp_path / "leak")], check=True)
    ex = _bwrap_executor(tmp_path, monkeypatch)
    result = ex.run(["./leak"])
    assert "super-secret" not in result.stdout


@requires_bwrap
def test_env_example_template_is_still_masked_like_env(tmp_path, monkeypatch):
    # Existing policy treats .env.* as secret; the mask must be consistent.
    (tmp_path / ".env.example").write_text("KEY=placeholder-secret\n", encoding="utf-8")
    (tmp_path / "r.py").write_text(
        "try: print(open('.env.example').read())\n"
        "except OSError: print('blocked')\n",
        encoding="utf-8",
    )
    ex = _bwrap_executor(tmp_path, monkeypatch)
    result = ex.run(["python3", "r.py"])
    assert "placeholder-secret" not in result.stdout


# ───────────────────────── .kvist isolation (bwrap) ──────────────────────────

@requires_bwrap
def test_kvist_policy_cannot_be_modified_in_strong_mode(tmp_path, monkeypatch):
    ex = _bwrap_executor(tmp_path, monkeypatch)
    original = (tmp_path / ".kvist" / "permissions.json").read_text()
    (tmp_path / "w.py").write_text(
        "try:\n"
        "    open('.kvist/permissions.json','w').write('HACKED')\n"
        "    print('wrote')\n"
        "except OSError:\n"
        "    print('blocked')\n",
        encoding="utf-8",
    )
    result = ex.run(["python3", "w.py"])
    assert "HACKED" not in (tmp_path / ".kvist" / "permissions.json").read_text()
    assert (tmp_path / ".kvist" / "permissions.json").read_text() == original


@requires_bwrap
def test_kvist_contents_not_readable_in_strong_mode(tmp_path, monkeypatch):
    ex = _bwrap_executor(tmp_path, monkeypatch)
    (tmp_path / "r.py").write_text(
        "import os; print('files', os.listdir('.kvist'))\n", encoding="utf-8"
    )
    result = ex.run(["python3", "r.py"])
    # .kvist is a tmpfs mount: empty, so permissions.json content never leaks.
    assert "permissions.json" not in result.stdout


# ───────────────────────────── weak-mode policy ──────────────────────────────

def test_auto_fallback_hard_denies_arbitrary_code_without_tree_control(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("AGENT_SANDBOX_MODE", "auto")
    monkeypatch.setattr(terminal_module.shutil, "which", lambda name, path=None: None)
    monkeypatch.setattr(
        "sandbox.shutil.which", lambda name, path=None: None
    )
    _write_policy(tmp_path, ["python *"])
    (tmp_path / "s.py").write_text("print('x')", encoding="utf-8")
    ex = TerminalExecutor(tmp_path)
    ex._cgroup_available = False
    assert ex.sandbox_mode is SandboxMode.WORKSPACE_ONLY
    requested = []
    monkeypatch.setattr(
        TerminalExecutor,
        "_request_user_approval",
        classmethod(lambda cls, c, r: requested.append(r) or False),
    )
    with pytest.raises(HardDenyError, match="cannot control detached descendants"):
        ex.run(["python", "s.py"])
    assert requested == []


@pytest.mark.parametrize(
    "command",
    [
        ["awk", 'BEGIN { print "fixture" }'],
        ["sed", "-n", "1p", "input.txt"],
    ],
)
def test_auto_fallback_treats_path_programming_tools_as_arbitrary_code(
    tmp_path,
    monkeypatch,
    command,
):
    monkeypatch.setenv("AGENT_SANDBOX_MODE", "auto")
    monkeypatch.setattr("sandbox.resolve_bubblewrap_path", lambda: None)
    monkeypatch.setattr(terminal_module, "resolve_bubblewrap_path", lambda: None)
    (tmp_path / "input.txt").write_text("fixture\n", encoding="utf-8")
    executor = TerminalExecutor(tmp_path)
    executor._cgroup_available = False

    with pytest.raises(HardDenyError, match="cannot control detached descendants"):
        executor.run(command)


def test_explicit_workspace_mode_is_named_and_warned(monkeypatch):
    from sandbox import detect_sandbox_mode

    mode, note = detect_sandbox_mode("workspace")
    assert mode is SandboxMode.WORKSPACE_ONLY
    assert "not an OS sandbox" in note
    assert "host permissions" in note.casefold()


# ─────────────────────── process-tree teardown (real) ────────────────────────

def _proc_running(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().split(") ", 1)[1].split()[0] not in ("Z", "X")
    except (FileNotFoundError, IndexError):
        return False


def _run_pt(script: str, marker: Path, timeout: float):
    body = script.replace("MARKER", repr(str(marker)))
    return run_supervised(
        [sys.executable, "-c", body],
        cwd=str(marker.parent),
        env={"PATH": "/usr/bin:/bin"},
        timeout=timeout,
        max_capture_bytes=100_000,
        rlimit_setter=None,
        is_windows=False,
        use_cgroup=cgroup_supervision_available(),
    )


@requires_posix
def test_same_session_descendant_killed_on_normal_completion_without_cgroup(
    tmp_path,
):
    marker = tmp_path / "gpid-no-cgroup-normal"
    body = (
        "import subprocess,time\n"
        "p=subprocess.Popen(['sleep','30'])\n"
        f"open({str(marker)!r},'w').write(str(p.pid))\n"
        "time.sleep(0.2)\n"
    )
    started = time.monotonic()
    result = run_supervised(
        [sys.executable, "-c", body],
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin"},
        timeout=5,
        max_capture_bytes=4096,
        rlimit_setter=None,
        is_windows=False,
        use_cgroup=False,
    )
    descendant_pid = int(marker.read_text())
    deadline = time.monotonic() + 2
    while _proc_running(descendant_pid) and time.monotonic() < deadline:
        time.sleep(0.02)
    try:
        assert result.timed_out is False
        assert time.monotonic() - started < 3
        assert not _proc_running(descendant_pid)
    finally:
        _cleanup(descendant_pid)


@requires_posix
def test_same_session_descendant_killed_on_timeout_without_cgroup(tmp_path):
    marker = tmp_path / "gpid-no-cgroup-timeout"
    body = (
        "import subprocess,time\n"
        "p=subprocess.Popen(['sleep','30'])\n"
        f"open({str(marker)!r},'w').write(str(p.pid))\n"
        "time.sleep(30)\n"
    )
    result = run_supervised(
        [sys.executable, "-c", body],
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin"},
        timeout=1,
        max_capture_bytes=4096,
        rlimit_setter=None,
        is_windows=False,
        use_cgroup=False,
    )
    descendant_pid = int(marker.read_text())
    deadline = time.monotonic() + 2
    while _proc_running(descendant_pid) and time.monotonic() < deadline:
        time.sleep(0.02)
    try:
        assert result.timed_out is True
        assert not _proc_running(descendant_pid)
    finally:
        _cleanup(descendant_pid)


def test_required_cgroup_disappearance_fails_before_spawn(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "process_tree._CgroupScope.create",
        classmethod(lambda cls: None),
    )
    spawned = []
    monkeypatch.setattr(
        "process_tree.subprocess.Popen",
        lambda *args, **kwargs: spawned.append((args, kwargs)),
    )

    with pytest.raises(ProcessSupervisionError, match="became unavailable"):
        run_supervised(
            [sys.executable, "-c", "print('must not spawn')"],
            cwd=tmp_path,
            env={"PATH": "/usr/bin:/bin"},
            timeout=1,
            max_capture_bytes=4096,
            rlimit_setter=None,
            is_windows=False,
            use_cgroup=True,
        )

    assert spawned == []


@requires_posix
def test_required_cgroup_cleanup_failure_is_reported_after_reader_cleanup(
    tmp_path,
    monkeypatch,
):
    closed = []

    class BrokenScope:
        def join_current_process(self):
            pass

        def kill(self):
            return "fault-injected cgroup cleanup failure"

        def close(self):
            closed.append(True)
            return None

    monkeypatch.setattr(
        "process_tree._CgroupScope.create",
        classmethod(lambda cls: BrokenScope()),
    )

    with pytest.raises(ProcessSupervisionError, match="fault-injected"):
        run_supervised(
            [sys.executable, "-c", "print('fixture')"],
            cwd=tmp_path,
            env={"PATH": "/usr/bin:/bin"},
            timeout=2,
            max_capture_bytes=4096,
            rlimit_setter=None,
            is_windows=False,
            use_cgroup=True,
        )

    assert closed == [True]
    assert not any(
        isinstance(thread, _BoundedReader)
        for thread in threading.enumerate()
    )


@requires_posix
def test_detached_pipe_holder_does_not_leak_reader_threads_without_cgroup(
    tmp_path,
):
    marker = tmp_path / "detached-pipe-holder"
    body = (
        "import subprocess,time\n"
        "p=subprocess.Popen(['sleep','30'],start_new_session=True)\n"
        f"open({str(marker)!r},'w').write(str(p.pid))\n"
        "time.sleep(0.2)\n"
    )
    started = time.monotonic()
    result = run_supervised(
        [sys.executable, "-c", body],
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin"},
        timeout=5,
        max_capture_bytes=4096,
        rlimit_setter=None,
        is_windows=False,
        use_cgroup=False,
    )
    descendant_pid = int(marker.read_text())
    deadline = time.monotonic() + 2
    while _proc_running(descendant_pid) and time.monotonic() < deadline:
        time.sleep(0.02)
    try:
        assert result.timed_out is False
        assert time.monotonic() - started < 3
        assert not _proc_running(descendant_pid)
        assert not any(
            isinstance(thread, _BoundedReader)
            for thread in threading.enumerate()
        )
    finally:
        _cleanup(descendant_pid)


@requires_posix
def test_setsid_descendant_is_tracked_on_timeout_without_cgroup(tmp_path):
    marker = tmp_path / "setsid-no-cgroup-timeout"
    body = (
        "import subprocess,time\n"
        "p=subprocess.Popen(['sleep','30'],start_new_session=True)\n"
        f"open({str(marker)!r},'w').write(str(p.pid))\n"
        "time.sleep(30)\n"
    )
    result = run_supervised(
        [sys.executable, "-c", body],
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin"},
        timeout=1,
        max_capture_bytes=4096,
        rlimit_setter=None,
        is_windows=False,
        use_cgroup=False,
    )
    descendant_pid = int(marker.read_text())
    deadline = time.monotonic() + 2
    while _proc_running(descendant_pid) and time.monotonic() < deadline:
        time.sleep(0.02)
    try:
        assert result.timed_out is True
        assert not _proc_running(descendant_pid)
    finally:
        _cleanup(descendant_pid)


@requires_cgroup
def test_setsid_descendant_killed_on_timeout(tmp_path):
    marker = tmp_path / "gpid"
    t0 = time.time()
    r = _run_pt(
        "import subprocess,time\n"
        "p=subprocess.Popen(['sleep','30'],start_new_session=True)\n"
        "open(MARKER,'w').write(str(p.pid))\n"
        "time.sleep(30)\n",
        marker,
        timeout=2,
    )
    elapsed = time.time() - t0
    assert r.timed_out is True
    assert elapsed < 8  # not blocked for the full 30s
    gpid = int(marker.read_text())
    deadline = time.time() + 3
    while _proc_running(gpid) and time.time() < deadline:
        time.sleep(0.05)
    assert not _proc_running(gpid)
    _cleanup(gpid)


@requires_cgroup
def test_setsid_descendant_killed_on_normal_completion(tmp_path):
    marker = tmp_path / "gpid"
    r = _run_pt(
        "import subprocess,time\n"
        "p=subprocess.Popen(['sleep','30'],start_new_session=True)\n"
        "open(MARKER,'w').write(str(p.pid))\n"
        "time.sleep(0.2)\n",
        marker,
        timeout=10,
    )
    assert r.timed_out is False
    gpid = int(marker.read_text())
    deadline = time.time() + 3
    while _proc_running(gpid) and time.time() < deadline:
        time.sleep(0.05)
    assert not _proc_running(gpid)
    _cleanup(gpid)


@requires_cgroup
def test_pipe_held_open_does_not_block_parent(tmp_path):
    marker = tmp_path / "gpid"
    t0 = time.time()
    r = _run_pt(
        "import subprocess,time\n"
        "p=subprocess.Popen(['sleep','30'])\n"  # inherits stdout pipe, no setsid
        "open(MARKER,'w').write(str(p.pid))\n"
        "time.sleep(0.2)\n",
        marker,
        timeout=10,
    )
    assert time.time() - t0 < 8
    _cleanup(int(marker.read_text()))


@requires_cgroup
def test_repeated_setsid_teardown_is_not_flaky(tmp_path):
    for i in range(5):
        marker = tmp_path / f"gpid{i}"
        r = _run_pt(
            "import subprocess,time\n"
            "p=subprocess.Popen(['sleep','20'],start_new_session=True)\n"
            "open(MARKER,'w').write(str(p.pid))\n"
            "time.sleep(20)\n",
            marker,
            timeout=1,
        )
        assert r.timed_out is True
        gpid = int(marker.read_text())
        deadline = time.time() + 3
        while _proc_running(gpid) and time.time() < deadline:
            time.sleep(0.05)
        assert not _proc_running(gpid)
        _cleanup(gpid)


def _cleanup(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


# ────────────────────────────── bounded output ───────────────────────────────

def _run_out(script: str, limit: int, timeout: float = 5):
    return run_supervised(
        [sys.executable, "-c", script],
        cwd="/tmp",
        env={"PATH": "/usr/bin:/bin"},
        timeout=timeout,
        max_capture_bytes=limit,
        rlimit_setter=None,
        is_windows=False,
        use_cgroup=cgroup_supervision_available(),
    )


@requires_posix
def test_output_below_limit_is_complete():
    r = _run_out("import sys; sys.stdout.write('C'*100)", 50_000)
    assert len(r.stdout) == 100 and not r.stdout_truncated


@requires_posix
def test_output_exactly_at_limit_not_truncated():
    r = _run_out("import sys; sys.stdout.write('B'*50000)", 50_000)
    assert len(r.stdout) == 50_000 and not r.stdout_truncated


@requires_posix
def test_output_above_limit_is_bounded():
    r = _run_out("import sys; sys.stdout.write('A'*500000)", 50_000)
    assert len(r.stdout) == 50_000 and r.stdout_truncated


@requires_posix
def test_endless_output_stays_bounded_and_times_out():
    import resource

    rss0 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    r = _run_out("import sys\nwhile True: sys.stdout.write('A'*4096)", 50_000, timeout=3)
    rss1 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    assert r.timed_out is True
    assert len(r.stdout) == 50_000
    assert rss1 - rss0 < 50_000  # parent memory did not scale with total output (KB)


@requires_posix
def test_both_streams_bounded_with_non_utf8():
    r = _run_out(
        "import os\nos.write(1, b'\\xff'*40000)\nos.write(2, b'\\x80'*40000)\n", 10_000
    )
    assert len(r.stdout) == 10_000 and len(r.stderr) == 10_000
    assert r.stdout_truncated and r.stderr_truncated
    text = terminal_module._decode_process_output(r.stdout)
    assert "�" in text  # UTF-8 replacement applied cleanly


# ──────────────────────────── shebang attestation ────────────────────────────

@pytest.mark.parametrize(
    "line,expected",
    [
        (b"#!/usr/bin/python\n", ["/usr/bin/python"]),
        (b"#!/usr/bin/env python\n", ["/usr/bin/env", "python"]),
        (b"#!/usr/bin/env -S python -u\n", ["/usr/bin/env", "-S", "python", "-u"]),
        (b"#!/bin/sh\n", ["/bin/sh"]),
        (b"not a shebang\n", None),
        (b"#!\n", []),
    ],
)
def test_parse_shebang(line, expected):
    assert _parse_shebang(line) == expected


def test_shebang_env_impersonation_is_denied(tmp_path):
    # An attacker binary merely NAMED `env` outside trusted roots must be
    # attested as itself (and denied), not silently resolved to its argument.
    ext = tmp_path.parent / "evil_env_dir"
    ext.mkdir(exist_ok=True)
    fake_env = ext / "env"
    fake_env.write_text("#!/bin/sh\necho PWNED\n", encoding="utf-8")
    fake_env.chmod(0o755)
    _write_policy(tmp_path, ["*"])
    script = tmp_path / "run"
    script.write_text(f"#!{fake_env} python\nprint('hi')\n", encoding="utf-8")
    script.chmod(0o755)
    ex = TerminalExecutor(tmp_path, timeout=5)
    with pytest.raises(HardDenyError, match="shebang interpreter"):
        ex.run(["./run"])


def test_shebang_external_interpreter_is_denied(tmp_path, monkeypatch):
    _write_policy(tmp_path, ["*"])
    script = tmp_path / "run"
    script.write_text("#!/tmp/kvist-nonexistent-interp-xyz\nbody\n", encoding="utf-8")
    script.chmod(0o755)
    # Point the shebang at a real but untrusted external interpreter.
    ext = tmp_path.parent / "ext_interp"
    ext.write_text("#!/bin/sh\n", encoding="utf-8")
    ext.chmod(0o755)
    script.write_text(f"#!{ext}\nbody\n", encoding="utf-8")
    ex = TerminalExecutor(tmp_path)
    with pytest.raises(HardDenyError, match="shebang interpreter"):
        ex.run(["./run"])


@requires_posix
def test_shebang_system_interpreter_is_attested_and_runs(tmp_path, monkeypatch):
    _write_policy(tmp_path, ["*"])
    script = tmp_path / "hello.sh"
    script.write_text("#!/bin/sh\necho shebang-ok\n", encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setattr(
        TerminalExecutor, "_request_user_approval", classmethod(lambda cls, c, r: True)
    )
    ex = TerminalExecutor(tmp_path, timeout=8)
    result = ex.run(["./hello.sh"])
    assert "shebang-ok" in result.stdout


def test_shebang_interpreter_mutation_invalidates_context(tmp_path):
    script = tmp_path / "hello.sh"
    script.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    script.chmod(0o755)
    ex = TerminalExecutor(tmp_path, timeout=8)
    env = ex._build_env(None)
    b1 = ex._resolve_executable_binding("./hello.sh", tmp_path, env)
    assert b1.interpreter_path is not None
    # Rewrite to select a different interpreter.
    script.write_text("#!/usr/bin/env python3\nprint('hi')\n", encoding="utf-8")
    b2 = ex._resolve_executable_binding("./hello.sh", tmp_path, env)
    assert b1 != b2  # binding change invalidates the bound execution context


# ─────────────────────── project PATH / venv / toolchains ────────────────────

@requires_posix
def test_controlled_path_prepends_project_tool_dirs(tmp_path):
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    (tmp_path / "node_modules" / ".bin").mkdir(parents=True)
    path = _controlled_child_path(tmp_path).split(os.pathsep)
    assert str(tmp_path / ".venv" / "bin") in path
    assert str(tmp_path / "node_modules" / ".bin") in path
    assert path.index(str(tmp_path / ".venv" / "bin")) < path.index("/usr/bin")


@requires_posix
def test_controlled_path_ignores_absent_dirs(tmp_path):
    path = _controlled_child_path(tmp_path).split(os.pathsep)
    assert str(tmp_path / ".venv" / "bin") not in path
    assert "/usr/bin" in path or "/bin" in path


@requires_posix
def test_external_tool_paths_validated(tmp_path, monkeypatch):
    good = tmp_path / "tools"
    good.mkdir()
    good.chmod(0o750)
    monkeypatch.setenv(
        "AGENT_TOOL_PATHS", os.pathsep.join([str(good), "relative/no", "/nonexistent-xyz"])
    )
    from terminal import _external_tool_paths

    resolved = _external_tool_paths()
    assert good.resolve() in resolved
    assert all(p.is_absolute() and p.is_dir() for p in resolved)


@requires_posix
def test_external_tool_paths_reject_group_writable_and_broad_mounts(
    tmp_path,
    monkeypatch,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    internal = workspace / "tools"
    internal.mkdir()
    group_writable = tmp_path / "group-tools"
    group_writable.mkdir()
    group_writable.chmod(0o770)
    safe_external = tmp_path / "external" / "bin"
    safe_external.mkdir(parents=True)
    safe_external.chmod(0o750)
    monkeypatch.setenv(
        "AGENT_TOOL_PATHS",
        os.pathsep.join(
            [
                "/",
                str(workspace),
                str(internal),
                str(group_writable),
                str(safe_external),
            ]
        ),
    )
    from terminal import _external_tool_paths

    resolved = _external_tool_paths(workspace)

    assert resolved == (safe_external.resolve(),)


def test_venv_symlink_to_system_python_is_permitted(tmp_path, monkeypatch, trusted_python_path):
    _write_policy(tmp_path, ["*"])
    vb = tmp_path / ".venv" / "bin"
    vb.mkdir(parents=True)
    (vb / "python").symlink_to(trusted_python_path)
    (tmp_path / ".venv" / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")
    (tmp_path / "hi.py").write_text("import sys; print('prefix', sys.prefix)", encoding="utf-8")
    ex = TerminalExecutor(tmp_path, timeout=10)
    result = ex.run([".venv/bin/python", "hi.py"])
    assert result.returncode == 0
    assert "prefix" in result.stdout


def test_venv_symlink_to_untrusted_target_is_denied(tmp_path):
    vb = tmp_path / ".venv" / "bin"
    vb.mkdir(parents=True)
    evil = tmp_path.parent / "evil_interp"
    evil.write_text("#!/bin/sh\necho pwned\n", encoding="utf-8")
    evil.chmod(0o755)
    (vb / "python").symlink_to(evil)
    (tmp_path / ".venv" / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")
    ex = TerminalExecutor(tmp_path, timeout=5)
    with pytest.raises(HardDenyError, match="symlink resolves outside"):
        ex.run([".venv/bin/python", "-c", "1"])


def test_venv_without_pyvenv_cfg_is_denied(tmp_path):
    """A launcher symlink without an attested pyvenv.cfg is not a venv exception."""
    _write_policy(tmp_path, ["*"])
    vb = tmp_path / ".venv" / "bin"
    vb.mkdir(parents=True)
    (vb / "python").symlink_to(os.path.realpath(sys.executable))
    (tmp_path / "hi.py").write_text("print('should not run')", encoding="utf-8")
    ex = TerminalExecutor(tmp_path, timeout=5)
    with pytest.raises(HardDenyError, match="symlink resolves outside"):
        ex.run([".venv/bin/python", "hi.py"])


# ────────────── explicit workspace mode: venv launcher outside system roots ──────────────
# Mirrors a ``uv``-managed venv, whose interpreter lives under the user's
# HOME rather than under /usr, /bin or /sbin (see _verify_venv_python_launcher's
# ``allow_external_interpreter``).  Only ``AGENT_SANDBOX_MODE=workspace`` may
# accept it, and only when pyvenv.cfg's own ``executable`` field names that
# exact resolved path.

@pytest.fixture
def external_python_interpreter():
    """A real, already-installed Python whose resolved path is outside every
    trusted system root -- exactly the uv-managed case this feature targets.

    Returned by reference (never copied): modern CPython's getpath locates
    its stdlib relative to the *real* binary's own directory, so a bare copy
    of the executable without its sibling ``lib/`` layout fails to start.
    """
    resolved = Path(os.path.realpath(sys.executable))
    if _is_trusted_system_executable(resolved, resolved):
        pytest.skip(
            "this interpreter is installed under a trusted system root; "
            "cannot exercise the external-interpreter path"
        )
    return resolved


def _write_uv_style_pyvenv_cfg(venv_dir: Path, executable: Path) -> None:
    venv_dir.mkdir(parents=True, exist_ok=True)
    (venv_dir / "pyvenv.cfg").write_text(
        f"home = {executable.parent}\n"
        "include-system-site-packages = false\n"
        "version = 3.11.15\n"
        f"executable = {executable}\n"
        "command = uv venv\n",
        encoding="utf-8",
    )


def test_venv_workspace_mode_permits_trusted_root_interpreter(
    tmp_path, monkeypatch, trusted_python_path
):
    """(a) workspace mode + system-rooted interpreter: still permitted, as before."""
    monkeypatch.setenv("AGENT_SANDBOX_MODE", "workspace")
    _write_policy(tmp_path, ["*"])
    vb = tmp_path / ".venv" / "bin"
    vb.mkdir(parents=True)
    (vb / "python").symlink_to(trusted_python_path)
    (tmp_path / ".venv" / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")
    (tmp_path / "hi.py").write_text("print('prefix ok')", encoding="utf-8")
    ex = TerminalExecutor(tmp_path, timeout=10)
    assert ex.sandbox_mode is SandboxMode.WORKSPACE_ONLY
    result = ex.run([".venv/bin/python", "hi.py"])
    assert result.returncode == 0
    assert "prefix ok" in result.stdout


def test_venv_workspace_mode_permits_external_interpreter_matching_pyvenv_cfg(
    tmp_path, monkeypatch, external_python_interpreter
):
    """(b) workspace mode + interpreter outside system roots, attested by pyvenv.cfg: permitted."""
    monkeypatch.setenv("AGENT_SANDBOX_MODE", "workspace")
    _write_policy(tmp_path, ["*"])
    external = external_python_interpreter
    venv_dir = tmp_path / ".venv"
    _write_uv_style_pyvenv_cfg(venv_dir, external)
    (venv_dir / "bin").mkdir(parents=True)
    (venv_dir / "bin" / "python").symlink_to(external)
    (tmp_path / "hi.py").write_text("print('external ok')", encoding="utf-8")
    ex = TerminalExecutor(tmp_path, timeout=10)
    assert ex.sandbox_mode is SandboxMode.WORKSPACE_ONLY
    assert ex.sandbox_request == "workspace"
    result = ex.run([".venv/bin/python", "hi.py"])
    assert result.returncode == 0
    assert "external ok" in result.stdout


@pytest.mark.parametrize(
    "sandbox_env",
    [
        None,
        "auto",
        pytest.param("bubblewrap", marks=requires_bwrap),
    ],
)
def test_venv_non_workspace_mode_runs_external_interpreter_read_only(
    tmp_path, monkeypatch, sandbox_env, external_python_interpreter
):
    """(c) auto/bubblewrap/default: the attested interpreter runs, and under
    Bubblewrap its installation root is visible read-only only."""
    if sandbox_env is None:
        monkeypatch.delenv("AGENT_SANDBOX_MODE", raising=False)
    else:
        monkeypatch.setenv("AGENT_SANDBOX_MODE", sandbox_env)
    _write_policy(tmp_path, ["*"])
    external = external_python_interpreter
    venv_dir = tmp_path / ".venv"
    _write_uv_style_pyvenv_cfg(venv_dir, external)
    (venv_dir / "bin").mkdir(parents=True)
    (venv_dir / "bin" / "python").symlink_to(external)
    (tmp_path / "hi.py").write_text(
        "import pathlib, sys\n"
        "probe = pathlib.Path(sys.base_prefix) / 'kvist-write-probe'\n"
        "try:\n"
        "    probe.write_text('x')\n"
        "except OSError:\n"
        "    print('external ok, root read-only')\n"
        "else:\n"
        "    probe.unlink()\n"
        "    print('external ok, root writable')\n",
        encoding="utf-8",
    )
    ex = TerminalExecutor(tmp_path, timeout=20)

    result = ex.run([".venv/bin/python", "hi.py"])

    assert result.returncode == 0, result.stderr
    if ex.sandbox_mode is SandboxMode.BUBBLEWRAP:
        assert "external ok, root read-only" in result.stdout
    else:
        assert "external ok" in result.stdout


def test_venv_workspace_mode_denies_mismatched_pyvenv_cfg_executable(
    tmp_path, monkeypatch, external_python_interpreter
):
    """(d) pyvenv.cfg names a different target than the symlink: denied even in workspace mode."""
    monkeypatch.setenv("AGENT_SANDBOX_MODE", "workspace")
    _write_policy(tmp_path, ["*"])
    external = external_python_interpreter
    # A real, different file than ``external`` -- the declared and effective
    # interpreter targets must diverge, whether or not the declared one
    # would itself be a runnable Python.
    other = tmp_path.parent / f"not_the_venv_interpreter_{tmp_path.name}"
    other.write_bytes(b"")
    venv_dir = tmp_path / ".venv"
    # pyvenv.cfg declares ``other``, but the launcher symlink actually
    # resolves to ``external`` -- the declared and effective targets diverge.
    _write_uv_style_pyvenv_cfg(venv_dir, other)
    (venv_dir / "bin").mkdir(parents=True)
    (venv_dir / "bin" / "python").symlink_to(external)
    (tmp_path / "hi.py").write_text("print('should not run')", encoding="utf-8")
    ex = TerminalExecutor(tmp_path, timeout=5)
    with pytest.raises(HardDenyError, match="symlink resolves outside"):
        ex.run([".venv/bin/python", "hi.py"])


def test_venv_workspace_mode_denies_non_venv_structure(
    tmp_path, monkeypatch, external_python_interpreter
):
    """(e) workspace mode does not relax the structural venv checks."""
    monkeypatch.setenv("AGENT_SANDBOX_MODE", "workspace")
    _write_policy(tmp_path, ["*"])
    external = external_python_interpreter
    venv_dir = tmp_path / ".venv"
    _write_uv_style_pyvenv_cfg(venv_dir, external)
    # Not a python-named basename under .venv/bin -- fails the launcher
    # basename check regardless of an otherwise-valid pyvenv.cfg.
    (venv_dir / "bin").mkdir(parents=True)
    (venv_dir / "bin" / "run-python").symlink_to(external)
    (tmp_path / "hi.py").write_text("print('should not run')", encoding="utf-8")
    ex = TerminalExecutor(tmp_path, timeout=5)
    with pytest.raises(HardDenyError, match="symlink resolves outside"):
        ex.run([".venv/bin/run-python", "hi.py"])


@requires_bwrap
def test_real_venv_runs_and_imports_local_fixture(tmp_path, monkeypatch):
    import venv as venvmod

    venv_dir = tmp_path / ".venv"
    venvmod.EnvBuilder(with_pip=False, symlinks=True).create(venv_dir)
    site = next(venv_dir.glob("lib/python*/site-packages"))
    (site / "kvist_fixture.py").write_text("VALUE = 'imported-ok'\n", encoding="utf-8")
    (tmp_path / "check.py").write_text(
        "import sys, kvist_fixture\n"
        "print('prefix', sys.prefix)\n"
        "print('value', kvist_fixture.VALUE)\n"
        "import os; print('home_secret', os.path.exists(os.path.expanduser('~/.ssh')))\n",
        encoding="utf-8",
    )
    ex = _bwrap_executor(tmp_path, monkeypatch)
    result = ex.run([".venv/bin/python", "check.py"])
    assert "imported-ok" in result.stdout
    assert str(venv_dir) in result.stdout  # sys.prefix identifies the venv
    assert "home_secret True" not in result.stdout  # host HOME not visible


@requires_posix
def test_tool_path_mount_is_added_to_bwrap(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ext = tmp_path / "external_tools"
    ext.mkdir()
    ext.chmod(0o750)
    wrapped = wrap_command_for_sandbox(
        ["ls"],
        SandboxMode.BUBBLEWRAP,
        workspace,
        workspace,
        bwrap_path="/usr/bin/bwrap",
        network_allowed=False,
        tool_paths=(ext,),
    )
    joined = " ".join(wrapped)
    assert str(ext.resolve()) in joined


@pytest.mark.parametrize("relative", [None, "inside", "group-writable"])
def test_tool_path_mount_rejects_broad_project_and_mutable_paths(tmp_path, relative):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    if relative is None:
        candidate = Path("/")
    elif relative == "inside":
        candidate = workspace / "tools"
        candidate.mkdir()
    else:
        candidate = tmp_path / relative
        candidate.mkdir()
        candidate.chmod(0o770)

    with pytest.raises(ValueError, match="unsafe external tool path"):
        wrap_command_for_sandbox(
            ["ls"],
            SandboxMode.BUBBLEWRAP,
            workspace,
            workspace,
            bwrap_path="/usr/bin/bwrap",
            network_allowed=False,
            tool_paths=(candidate,),
        )


def test_secret_mask_args_present_in_bwrap_wrap(tmp_path):
    (tmp_path / ".env").write_text("A=1", encoding="utf-8")
    (tmp_path / ".ssh").mkdir()
    wrapped = wrap_command_for_sandbox(
        ["ls"],
        SandboxMode.BUBBLEWRAP,
        tmp_path,
        tmp_path,
        bwrap_path="/usr/bin/bwrap",
        network_allowed=False,
    )
    assert "/dev/null" in wrapped  # .env masked
    # .ssh masked as tmpfs and .kvist tmpfs'd
    tmpfs_targets = [wrapped[i + 1] for i, a in enumerate(wrapped) if a == "--tmpfs"]
    assert any(t.endswith(".ssh") for t in tmpfs_targets)

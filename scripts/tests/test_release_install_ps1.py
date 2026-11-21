# -*- coding: utf-8 -*-
"""scripts/release/install.ps1 is the Windows twin of install.sh: the same installer screen drives it.

No PowerShell runs here (CI has it on Windows only), so these tests pin the contract between the
script and the screen (frontends/tui/ink/setup): the component ids, the request spelling and the
pinned downloads. The script itself is exercised by test_release_install_ps1_run.py when pwsh exists.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (ROOT / "scripts" / "release" / "install.ps1").read_text(encoding="utf-8")
INSTALL_SH = (ROOT / "scripts" / "release" / "install.sh").read_text(encoding="utf-8")
FLOW = (ROOT / "frontends" / "tui" / "ink" / "setup" / "flow.ts").read_text(encoding="utf-8")
OPS = (ROOT / "frontends" / "tui" / "ink" / "setup" / "ops.ts").read_text(encoding="utf-8")


def _screen_ids() -> set[str]:
    return set(re.findall(r'"(\w+)"', re.search(r"COMPONENT_IDS = \[([^\]]*)\]", FLOW).group(1)))


def test_every_component_the_plan_offers_is_one_the_screen_knows():
    plan = SCRIPT[SCRIPT.index("function Write-Plan"):SCRIPT.index("function Test-Terminal")]
    offered = set(re.findall(r'id = "(\w+)"', plan))
    assert offered and offered <= _screen_ids()
    # What the screen offers by default on every system is offered here too.
    assert {"cli", "node", "app", "jetbrains", "vscode", "path"} <= offered


def test_the_script_takes_the_arguments_the_screen_passes_on_windows():
    for argument in ("-Cache", "-Plan", "-Only", "-Events", "-From"):
        assert argument in OPS or argument == "-From"
        assert f"${argument[1:]}" in SCRIPT.split("function ", 1)[0]
    assert '"-ExecutionPolicy", "Bypass", "-File"' in OPS


def test_the_events_are_the_ones_the_screen_reads():
    ts = (ROOT / "frontends" / "tui" / "ink" / "setup" / "ops.ts").read_text(encoding="utf-8")
    read = set(re.findall(r'"(\w+)"', re.search(r"export type InstallEvent =(.*?)\n\nexport type InstallReply", ts, re.S).group(1)))
    sent = set(re.findall(r'Send-Event "(\w+)"', SCRIPT))
    assert sent == {"component", "done", "error", "step", "warning"}
    assert sent <= read
    for status in ("start", "done", "failed"):
        assert f'status = "{status}"' in SCRIPT
    assert "event = $Name" in SCRIPT


def test_nothing_but_json_goes_to_stdout_under_events():
    """The screen parses every stdout line; Write-Host would put prose among them."""
    installer_screen = SCRIPT[SCRIPT.index("function Start-InstallerScreen"):SCRIPT.index("function Invoke-Component")]
    after_events = SCRIPT[SCRIPT.index('Send-Event "done"'):]
    for line in SCRIPT.splitlines():
        if "Write-Host" not in line or line.lstrip().startswith("#"):
            continue
        # Only the step helper (behind its -Events branch), the bootstrap (never run under -Events)
        # and the closing words (after the done event has returned) print prose.
        assert 'Write-Host "==> $Message"' in line or line in installer_screen or line in after_events, line
    assert "-and -not $Events" in SCRIPT


def test_node_is_pinned_to_the_version_install_sh_pins():
    version = re.search(r'NODE_VERSION="([\d.]+)"', INSTALL_SH).group(1)
    assert f'$NodeVersion = "{version}"' in SCRIPT
    assert re.search(r'\$NodeSha256 = "[0-9a-f]{64}"', SCRIPT)
    assert re.search(r'\$FfmpegSha256 = "[0-9a-f]{64}"', SCRIPT)
    # install.sh and install.ps1 must be the same eugeneware release.
    assert 'FFMPEG_URL="https://github.com/eugeneware/ffmpeg-static/releases/download/b6.0"' in INSTALL_SH
    assert "eugeneware/ffmpeg-static/releases/download/b6.0" in SCRIPT


def test_the_bootstrap_hands_the_screen_what_main_py_and_ops_ts_read():
    for name in ("KVIST_INSTALL_SCRIPT", "KVIST_INSTALL_CACHE", "KVIST_NODE_HOME", "KVIST_NODE"):
        assert f"{name}" in SCRIPT
    assert "--installer" in SCRIPT
    # Run through `irm | iex` it must not close the person's window or leave its variables behind.
    assert "SetEnvironmentVariable($name, $saved[$name], \"Process\")" in SCRIPT
    assert not re.search(r"^\s*exit\s+0", SCRIPT, re.MULTILINE)

# -*- coding: utf-8 -*-
"""The desktop app's trip from the release workflow to the person's application menu.

Four files must agree on one set of names: the workflow that builds and publishes the app, the
shell and PowerShell installers that download it, and the Python installer that registers it.
A rename in one of them that the others miss would publish an app nobody can install, and none of
the installers' own tests would notice, so this checks them against each other.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "plugin-release.yml"
INSTALL_SH = ROOT / "scripts" / "release" / "install.sh"
INSTALL_PS1 = ROOT / "scripts" / "release" / "install.ps1"
TAURI = ROOT / "frontends" / "gui" / "tauri" / "src-tauri"


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _built_assets(workflow: dict) -> set[str]:
    return {entry["asset"] for entry in workflow["jobs"]["build-desktop-app"]["strategy"]["matrix"]["include"]}


def test_the_workflow_builds_the_app_for_linux_macos_and_windows(workflow):
    job = workflow["jobs"]["build-desktop-app"]
    runners = {entry["asset"]: entry["runner"] for entry in job["strategy"]["matrix"]["include"]}
    assert runners["kvist-app-linux-x64"].startswith("ubuntu")
    assert runners["kvist-app-macos-arm64"].startswith("macos")
    assert runners["kvist-app-macos-x64"].startswith("macos")
    assert runners["kvist-app-windows-x64.exe"].startswith("windows")
    assert job["strategy"]["fail-fast"] is False, "one platform failing must not cancel the others"


def test_the_build_runs_the_tests_and_a_locked_release_build(workflow):
    steps = workflow["jobs"]["build-desktop-app"]["steps"]
    script = "\n".join(step.get("run", "") for step in steps)
    assert "node --test \"tests/*.test.mjs\"" in script and "cargo test --locked" in script
    assert "cargo build --release --locked" in script
    assert (TAURI / "Cargo.lock").is_file(), "--locked needs the lock file in the repository"


def test_linux_gets_the_system_libraries_the_web_view_needs(workflow):
    install = next(step for step in workflow["jobs"]["build-desktop-app"]["steps"] if "libwebkit2gtk-4.1-dev" in step.get("run", ""))
    assert install["if"] == "runner.os == 'Linux'"


def test_each_build_uploads_the_binary_cargo_produces(workflow):
    job = workflow["jobs"]["build-desktop-app"]
    upload = next(step for step in job["steps"] if step.get("uses", "").startswith("actions/upload-artifact"))
    assert upload["with"]["name"] == "desktop-app-${{ matrix.asset }}"
    assert upload["with"]["path"].endswith("target/release/${{ matrix.binary }}")
    binaries = {entry["binary"] for entry in job["strategy"]["matrix"]["include"]}
    assert binaries == {"kvist-tauri", "kvist-tauri.exe"}
    assert 'name = "kvist-tauri"' in (TAURI / "Cargo.toml").read_text(encoding="utf-8")


def test_the_release_job_waits_for_the_app_but_does_not_depend_on_it_succeeding(workflow):
    job = workflow["jobs"]["build-plugin"]
    assert "build-desktop-app" in job["needs"]
    # The condition names only the Windows agent build, so a failed app build cannot stop a release.
    assert "build-desktop-app" not in job["if"]


def test_the_release_publishes_every_app_asset_and_its_icons(workflow):
    steps = workflow["jobs"]["build-plugin"]["steps"]
    prepare = next(step for step in steps if step.get("name") == "Prepare the desktop apps")["run"]
    collect = next(step for step in steps if step.get("name") == "Collect release assets")["run"]
    for asset in _built_assets(workflow):
        assert asset in prepare, asset
        # A failed platform keeps what the rolling release already has.
    assert "gh release download latest-plugin-build" in prepare
    assert "release/kvist-app-icon.png" in prepare and "release/kvist-app.icns" in prepare
    assert "release/kvist-app-*" in collect and "release/kvist-app.icns" in collect


def test_the_icons_the_release_copies_exist_in_the_repository():
    for name in ("128x128@2x.png", "icon.icns", "icon.ico"):
        assert (TAURI / "icons" / name).is_file(), name


def test_install_sh_downloads_exactly_the_assets_the_workflow_publishes(workflow):
    script = INSTALL_SH.read_text(encoding="utf-8")
    named = set(re.findall(r'APP_ASSET="(kvist-app-[\w.-]+)"', script))
    assert named == _built_assets(workflow) - {"kvist-app-windows-x64.exe"}
    assert 'APP_ICON_ASSET="kvist-app-icon.png"' in script and 'APP_ICNS_ASSET="kvist-app.icns"' in script


def test_install_ps1_downloads_the_windows_asset_the_workflow_publishes(workflow):
    script = INSTALL_PS1.read_text(encoding="utf-8")
    assert '$AppAsset = "kvist-app-windows-x64.exe"' in script
    assert "kvist-app-windows-x64.exe" in _built_assets(workflow)
    # The app is optional: a release without it must still install the agent.
    assert "Get-OptionalAsset $AppAsset" in script and "if ($appBinary) { Install-App $appBinary }" in script
    # Start menu and Desktop shortcuts, as the Electron app's installer makes them.
    assert 'Start Menu\\Programs' in script and "[Environment]::GetFolderPath(\"Desktop\")" in script
    assert 'Join-Path $appDir "kvist-app.exe"' in script


def test_the_developer_installer_builds_the_same_binary_the_workflow_uploads(workflow):
    """install_app.py compiles; only install.sh / install.ps1 download. Both mean the one cargo binary."""
    sys_path = str(ROOT / "scripts" / "installer")
    import sys

    sys.path.insert(0, sys_path)
    try:
        import install_app
    finally:
        sys.path.remove(sys_path)
    binaries = {entry["binary"] for entry in workflow["jobs"]["build-desktop-app"]["strategy"]["matrix"]["include"]}
    source = (ROOT / "scripts" / "installer" / "install_app.py").read_text(encoding="utf-8")
    assert {"kvist-tauri", "kvist-tauri.exe"} == binaries
    assert '"kvist-tauri.exe" if system == "Windows" else "kvist-tauri"' in source
    assert install_app.TAURI_DIR == TAURI and "urllib" not in source


def test_the_installed_app_is_found_where_every_installer_puts_the_agent():
    """kvist-core sits one folder above the app on Linux and Windows (the app looks there)."""
    core = (TAURI / "src" / "core.rs").read_text(encoding="utf-8")
    assert "up.join(CORE_NAME)" in core and '".local/share/kvist"' in core and 'join("Kvist")' in core
    assert 'APP_DIR="$LIB_DIR/app"' in INSTALL_SH.read_text(encoding="utf-8")
    assert 'Join-Path $LibDir "app"' in INSTALL_PS1.read_text(encoding="utf-8")


def test_the_installer_screen_ticks_the_app_by_default():
    flow = (ROOT / "frontends" / "tui" / "ink" / "setup" / "flow.ts").read_text(encoding="utf-8")
    ids = re.search(r"COMPONENT_IDS = \[([^\]]*)\]", flow).group(1)
    assert '"app"' in ids
    unticked = re.search(r"const UNTICKED = \[([^\]]*)\]", flow).group(1)
    assert "app" not in unticked, "the download-and-install box must start ticked"
    # install.sh runs the app too when nobody chose, so the plain (no-screen) install matches the screen.
    assert re.search(r'ONLY="cli,app,', INSTALL_SH.read_text(encoding="utf-8"))

# -*- mode: python ; coding: utf-8 -*-

import os
import pkgutil

# Bundle every first-party top-level module so the build never silently drops a
# source file that is reached only via a dynamic/lazy import. New files added to
# the project root are picked up automatically without touching this spec.
_ENTRY_SCRIPT = 'main.py'
# make_mascot_frames is a development tool (it needs Pillow and numpy), not part of the app.
_NON_APP_MODULES = frozenset({'run_task', 'package_script', 'install', 'make_mascot_frames'})
local_hiddenimports = sorted(
    name[:-3]
    for name in os.listdir(SPECPATH)
    if name.endswith('.py')
    and name != _ENTRY_SCRIPT
    and name[:-3] not in _NON_APP_MODULES
)


def _acp_hiddenimports():
    """Every submodule of the ACP SDK, so `kvist --acp` cannot ship without it.

    `acp_frontend` imports the SDK from a nested package (`acp.agent.*`) that
    the static analysis reaches, but a frozen binary with a silently missing
    SDK would make `--acp` fail at handshake time instead of at build time.
    """
    try:
        import acp
    except ImportError:
        return []
    return sorted(
        module.name
        for module in pkgutil.walk_packages(acp.__path__, prefix='acp.')
    )


local_hiddenimports = sorted(set(local_hiddenimports) | set(_acp_hiddenimports()))

# The Ink terminal UI is one self-contained file made by `npm run build`; it is
# optional because building it needs Node.js. Without it the binary still works
# and `--frontend tui` falls back to the classic terminal with an explanation.
_UI_BUNDLE = os.path.join(SPECPATH, 'dist-ui', 'kvist-tui.mjs')
_ui_datas = [(_UI_BUNDLE, 'dist-ui')] if os.path.isfile(_UI_BUNDLE) else []

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('prompts', 'prompts'),
        ('themes', 'themes'),
        ('assets', 'assets'),
        # version.py's pyproject.toml fallback (no installed distribution
        # metadata exists inside a frozen build either).
        ('pyproject.toml', '.'),
        *_ui_datas,
    ],
    hiddenimports=local_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='kvist',
    debug=False,
    # Onefile builds run a bootloader parent process plus a child process
    # (the real interpreter). Both sit in the same foreground process group,
    # so the kernel already delivers Ctrl+C (SIGINT) straight to the child.
    # With the default False, the bootloader *also* forwards the signal to
    # the child itself, so the child receives SIGINT twice in quick
    # succession. A second SIGINT arriving while CPython's `threading`/
    # `queue` internals are mid-cleanup from the first one (re-acquiring a
    # Condition's lock) corrupts that lock's state and raises an unhandled
    # "release unlocked lock" RuntimeError before the app's own
    # KeyboardInterrupt handling ever runs. Setting this to True stops the
    # bootloader from re-forwarding, so the child gets exactly one SIGINT,
    # matching `python main.py` behavior.
    bootloader_ignore_signals=True,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# -*- mode: python ; coding: utf-8 -*-

import os
import pkgutil

# Bundle every first-party module so the build never silently drops a source
# file that is reached only via a dynamic/lazy import; kernel.main itself is
# run through runpy by the entry script, so static analysis never sees it.
# New files in these directories are picked up without touching this spec.
_CLASSIC_DIR = os.path.join(SPECPATH, 'frontends', 'tui', 'classic')
_ZED_DIR = os.path.join(SPECPATH, 'frontends', 'zed')


def _module_names(directory, prefix=''):
    return [
        prefix + name[:-3]
        for name in os.listdir(directory)
        if name.endswith('.py') and name != '__main__.py'
    ]


local_hiddenimports = sorted(
    _module_names(os.path.join(SPECPATH, 'kernel'), 'kernel.')
    + _module_names(os.path.join(SPECPATH, 'kernel', 'providers'), 'kernel.providers.')
    + _module_names(_CLASSIC_DIR)
    + _module_names(_ZED_DIR)
)
local_hiddenimports = [name.removesuffix('.__init__') for name in local_hiddenimports]


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

# The Ink terminal UI is one self-contained file made by `npm run build` in
# frontends/tui/ink; it is optional because building it needs Node.js. Without
# it the binary still works and `--frontend tui` falls back to the classic
# terminal with an explanation.
_UI_BUNDLE = os.path.join(SPECPATH, 'frontends', 'tui', 'ink', 'dist-ui', 'kvist-tui.mjs')
_ui_datas = [(_UI_BUNDLE, 'frontends/tui/ink/dist-ui')] if os.path.isfile(_UI_BUNDLE) else []

# Data keeps its path relative to the module that reads it: kernel modules
# unpack under kernel/, the classic terminal's modules at the top level.
a = Analysis(
    ['kernel/__main__.py'],
    pathex=[SPECPATH, _CLASSIC_DIR, _ZED_DIR],
    binaries=[],
    datas=[
        ('kernel/prompts', 'kernel/prompts'),
        ('kernel/themes', 'kernel/themes'),
        ('frontends/tui/classic/assets', 'assets'),
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
    # matching `python -m kernel` behavior.
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

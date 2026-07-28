# -*- mode: python ; coding: utf-8 -*-

import os

# Bundle every first-party top-level module so the build never silently drops a
# source file that is reached only via a dynamic/lazy import. New files added to
# the project root are picked up automatically without touching this spec.
_ENTRY_SCRIPT = 'main.py'
_NON_APP_MODULES = frozenset({'run_task', 'package_script'})
local_hiddenimports = sorted(
    name[:-3]
    for name in os.listdir(SPECPATH)
    if name.endswith('.py')
    and name != _ENTRY_SCRIPT
    and name[:-3] not in _NON_APP_MODULES
)


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('prompts', 'prompts')],
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
    bootloader_ignore_signals=False,
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

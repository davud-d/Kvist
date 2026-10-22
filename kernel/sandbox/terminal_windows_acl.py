# -*- coding: utf-8 -*-
"""Windows ACL evaluation for the executable trust check.

``os.stat()`` reports a synthetic mode on Windows (always 0o666 for files), so
whether an ordinary user can change a path is read from its DACL.
"""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

# Principals that install and maintain system files; write access for them
# does not make a path user-mutable. Like root on Unix, a user who runs Kvist
# elevated is outside this model.
_OS_PRINCIPAL_SIDS = frozenset({
    "S-1-5-18",  # NT AUTHORITY\SYSTEM
    "S-1-5-32-544",  # BUILTIN\Administrators
    "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464",  # NT SERVICE\TrustedInstaller
})

_FILE_WRITE_DATA = 0x0002  # FILE_ADD_FILE on a directory
_FILE_APPEND_DATA = 0x0004  # FILE_ADD_SUBDIRECTORY on a directory
_FILE_WRITE_EA = 0x0010
_FILE_DELETE_CHILD = 0x0040
_FILE_WRITE_ATTRIBUTES = 0x0100
_DELETE = 0x00010000
_WRITE_DAC = 0x00040000
_WRITE_OWNER = 0x00080000
_GENERIC_ALL = 0x10000000
_GENERIC_WRITE = 0x40000000
_WRITE_RIGHTS = (
    _FILE_WRITE_DATA | _FILE_APPEND_DATA | _FILE_WRITE_EA | _FILE_DELETE_CHILD
    | _FILE_WRITE_ATTRIBUTES | _DELETE | _WRITE_DAC | _WRITE_OWNER
    | _GENERIC_ALL | _GENERIC_WRITE
)

# The entry applies to children only, not to the object the DACL belongs to.
_INHERIT_ONLY_ACE = 0x08


def dacl_lets_users_write(allowed_aces: Iterable[tuple[str, int, int]]) -> bool:
    """Whether an allow entry grants a write right to a principal outside the OS ones.

    ``allowed_aces`` holds ``(sid, access_mask, ace_flags)`` for each
    ACCESS_ALLOWED entry. Deny entries are not evaluated: ignoring them can
    only report a path as more writable than it is.
    """
    return any(
        sid not in _OS_PRINCIPAL_SIDS
        and mask & _WRITE_RIGHTS
        and not flags & _INHERIT_ONLY_ACE
        for sid, mask, flags in allowed_aces
    )


def windows_path_is_user_mutable(path: Path) -> bool:
    """Read ``path``'s DACL and report whether an ordinary user can change it.

    Anything that cannot be read counts as mutable: nothing is trusted that
    cannot be proven read-only for users. Owner rights are not evaluated.
    """
    try:
        import ntsecuritycon
        import pywintypes
        import win32security
    except ImportError:
        return True

    try:
        descriptor = win32security.GetFileSecurity(
            str(path), win32security.DACL_SECURITY_INFORMATION
        )
        dacl = descriptor.GetSecurityDescriptorDacl()
    except pywintypes.error:
        return True
    if dacl is None:
        return True

    allowed: list[tuple[str, int, int]] = []
    for index in range(dacl.GetAceCount()):
        ace = dacl.GetAce(index)
        (ace_type, ace_flags), mask, sid = ace[0], ace[1], ace[-1]
        if ace_type == ntsecuritycon.ACCESS_ALLOWED_ACE_TYPE:
            allowed.append((win32security.ConvertSidToStringSid(sid), mask, ace_flags))
    return dacl_lets_users_write(allowed)

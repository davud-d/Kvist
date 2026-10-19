# -*- coding: utf-8 -*-
"""Windows package reads: hold each parent open while refusing reparse points."""
from __future__ import annotations

from contextlib import ExitStack, closing
from pathlib import Path


def read_file(path: Path, max_bytes: int) -> bytes:
    import pywintypes
    import win32con
    import win32file

    target = path.absolute()
    # No FILE_SHARE_WRITE/DELETE: a checked directory cannot be replaced or
    # turned into a junction before the child is opened, nor can the file grow.
    flags = win32con.FILE_FLAG_OPEN_REPARSE_POINT | win32con.FILE_FLAG_BACKUP_SEMANTICS
    try:
        with ExitStack() as stack:
            for parent in reversed(target.parents):
                handle = stack.enter_context(closing(win32file.CreateFile(
                    str(parent), 0, win32con.FILE_SHARE_READ, None, win32con.OPEN_EXISTING, flags, None)))
                attributes = win32file.GetFileInformationByHandle(handle)[0]
                if attributes & win32con.FILE_ATTRIBUTE_REPARSE_POINT or not attributes & win32con.FILE_ATTRIBUTE_DIRECTORY:
                    raise OSError("package directory is a reparse point or not a directory")
            handle = stack.enter_context(closing(win32file.CreateFile(
                str(target), win32con.GENERIC_READ, win32con.FILE_SHARE_READ, None,
                win32con.OPEN_EXISTING, flags, None)))
            info = win32file.GetFileInformationByHandle(handle)
            if info[0] & (win32con.FILE_ATTRIBUTE_REPARSE_POINT | win32con.FILE_ATTRIBUTE_DIRECTORY):
                raise OSError("package file is a reparse point or directory")
            if win32file.GetFileType(handle) != win32con.FILE_TYPE_DISK or info[7] != 1:
                raise OSError("package file is not a regular file with one link")
            size = (info[5] << 32) | info[6]
            if size > max_bytes:
                raise OSError(f"package file is larger than {max_bytes} bytes")
            chunks = []
            while size:
                _, data = win32file.ReadFile(handle, min(size, 1024 * 1024))
                if not data:
                    raise OSError("package file ended before its declared size")
                chunks.append(data)
                size -= len(data)
            return b''.join(chunks)
    except pywintypes.error as error:
        raise OSError(f"cannot read package file: {error}") from None

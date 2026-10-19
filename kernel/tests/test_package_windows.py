# -*- coding: utf-8 -*-
"""Contract tests for native Windows reads; no Windows API is called on Linux."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from kernel.plugins.package_windows import read_file


@pytest.fixture
def windows(monkeypatch):
    constants = SimpleNamespace(FILE_FLAG_OPEN_REPARSE_POINT=0x200000, FILE_FLAG_BACKUP_SEMANTICS=0x2000000,
                               FILE_SHARE_READ=1, OPEN_EXISTING=3, FILE_ATTRIBUTE_REPARSE_POINT=0x400,
                               FILE_ATTRIBUTE_DIRECTORY=16, GENERIC_READ=0x80000000, FILE_TYPE_DISK=1)
    state = SimpleNamespace(handles=[], content=b'line1\r\nline2\x00', attributes=0, parent_attributes=16, links=1,
                            file_type=1, reads=0)

    def create(path, access, share, security, disposition, flags, template):
        assert share == 1 and flags & 0x200000 and disposition == 3
        handle = SimpleNamespace(path=path, file=bool(access), closed=False)
        handle.close = lambda: setattr(handle, 'closed', True)
        assert all(not item.closed for item in state.handles)
        state.handles.append(handle)
        return handle

    def info(handle):
        return (state.attributes if handle.file else state.parent_attributes, 0, 0, 0, 0, 0, len(state.content), state.links)

    def read(handle, count):
        state.reads += 1
        return 0, state.content[:count]

    monkeypatch.setitem(sys.modules, 'win32con', constants)
    monkeypatch.setitem(sys.modules, 'pywintypes', SimpleNamespace(error=type('WindowsError', (Exception,), {})))
    monkeypatch.setitem(sys.modules, 'win32file', SimpleNamespace(CreateFile=create, GetFileInformationByHandle=info,
                                                               GetFileType=lambda handle: state.file_type, ReadFile=read))
    return state


def test_windows_keeps_ancestors_locked_and_preserves_binary_data(windows, tmp_path):
    path = tmp_path / 'skill' / 'manifest.json'
    assert read_file(path, 100) == windows.content
    assert [item.path for item in windows.handles] == [str(p) for p in reversed(path.parents)] + [str(path)]
    assert all(item.closed for item in windows.handles)


@pytest.mark.parametrize('field,value', [('attributes', 0x400), ('attributes', 16), ('parent_attributes', 0x410),
                                        ('links', 2), ('file_type', 3)])
def test_windows_refuses_reparse_points_directories_hardlinks_and_devices(windows, tmp_path, field, value):
    setattr(windows, field, value)
    with pytest.raises(OSError):
        read_file(tmp_path / 'manifest.json', 100)
    assert windows.reads == 0
    assert all(item.closed for item in windows.handles)


def test_windows_bounds_reads_before_allocating_and_accepts_empty_files(windows, tmp_path):
    with pytest.raises(OSError, match='larger'):
        read_file(tmp_path / 'manifest.json', 1)
    assert windows.reads == 0
    windows.handles.clear()
    windows.content = b''
    assert read_file(tmp_path / 'empty', 10) == b''

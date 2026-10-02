import assert from 'node:assert/strict';
import { test } from 'node:test';

import { SupportedOs, currentOs, defaultPosixShell, isMacos, isPosix, relativeBinarySegments } from './platform';

test('Windows and Linux map as before', () => {
    assert.equal(currentOs('win32', 'x64'), SupportedOs.WINDOWS);
    assert.equal(currentOs('linux', 'x64'), SupportedOs.LINUX);
    assert.equal(currentOs('linux', 'arm64'), SupportedOs.LINUX);
});

test('a Mac takes the binary for its CPU, and Rosetta falls back to x64', () => {
    assert.equal(currentOs('darwin', 'arm64'), SupportedOs.MACOS_ARM64);
    assert.equal(currentOs('darwin', 'x64'), SupportedOs.MACOS_X64);
});

test('other platforms are refused', () => {
    assert.equal(currentOs('freebsd', 'x64'), null);
    assert.equal(currentOs('aix', 'ppc64'), null);
});

test('every platform has its own folder under bin', () => {
    assert.deepEqual(relativeBinarySegments(SupportedOs.WINDOWS), ['bin', 'win', 'kvist.exe']);
    assert.deepEqual(relativeBinarySegments(SupportedOs.LINUX), ['bin', 'linux', 'kvist.bin']);
    assert.deepEqual(relativeBinarySegments(SupportedOs.MACOS_ARM64), ['bin', 'mac-arm64', 'kvist.bin']);
    assert.deepEqual(relativeBinarySegments(SupportedOs.MACOS_X64), ['bin', 'mac-x64', 'kvist.bin']);
});

test('Linux and macOS run through a POSIX shell, Windows does not', () => {
    assert.equal(isPosix(SupportedOs.LINUX), true);
    assert.equal(isPosix(SupportedOs.MACOS_ARM64), true);
    assert.equal(isPosix(SupportedOs.MACOS_X64), true);
    assert.equal(isPosix(SupportedOs.WINDOWS), false);
    assert.equal(isPosix(null), false);
});

test('the fallback shell is zsh on a Mac and bash elsewhere', () => {
    assert.equal(isMacos(SupportedOs.MACOS_X64), true);
    assert.equal(isMacos(SupportedOs.LINUX), false);
    assert.equal(defaultPosixShell(SupportedOs.MACOS_ARM64), '/bin/zsh');
    assert.equal(defaultPosixShell(SupportedOs.LINUX), '/bin/bash');
});

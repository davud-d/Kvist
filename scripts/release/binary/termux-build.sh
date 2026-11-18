#!/usr/bin/env bash
# Builds and smoke-tests the single binary inside Termux (run it in the termux/termux-docker image,
# from the repository root). PyInstaller does not cross-compile, and Termux is bionic rather than
# glibc, so the build has to happen in Termux's own userland.
set -euxo pipefail

pkg update -y
pkg install -y python nodejs rust clang make binutils libffi openssl git ldd

# The NDK headers mark strtok deprecated and PyInstaller's bootloader builds with -Werror.
export CFLAGS="-Wno-deprecated-declarations"
# maturin (jiter, pydantic-core, ...) asks Android's getprop for the API level, which the image lacks;
# 24 is the level Termux's own packages are built for.
export ANDROID_API_LEVEL=24
python -m pip install --disable-pip-version-check -r requirements.txt pyinstaller

(cd frontends/tui/ink && npm ci && npm run build)
(cd kernel/session-rs && cargo build --release --locked)

KVIST_BUNDLE_NODE="$(command -v node)"
export KVIST_BUNDLE_NODE
python -m PyInstaller kvist.spec --noconfirm
python scripts/release/binary/smoke.py dist/kvist

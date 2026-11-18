#!/usr/bin/env bash
# Builds and smoke-tests the single binary inside Termux (run it in the termux/termux-docker image,
# from the repository root). PyInstaller does not cross-compile, and Termux is bionic rather than
# glibc, so the build has to happen in Termux's own userland.
set -euxo pipefail

# Left alone, pkg picks a random mirror, and one stalled the download for 40 minutes: pin the main
# one and bound every request.
TERMUX_PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
if [ -e "$TERMUX_PREFIX/etc/termux/mirrors/default" ]; then
	ln -sf "$TERMUX_PREFIX/etc/termux/mirrors/default" "$TERMUX_PREFIX/etc/termux/chosen_mirrors"
fi
printf 'Acquire::http::Timeout "30";\nAcquire::https::Timeout "30";\nAcquire::Retries "5";\n' >"$TERMUX_PREFIX/etc/apt/apt.conf.d/99ci"

pkg update -y
pkg install -y python nodejs rust clang make binutils libffi openssl git ldd libjpeg-turbo libsodium

# The NDK headers mark strtok deprecated and PyInstaller's bootloader builds with -Werror.
export CFLAGS="-Wno-deprecated-declarations"
# maturin (jiter, pydantic-core, ...) asks Android's getprop for the API level, which the image lacks;
# 24 is the level Termux's own packages are built for.
export ANDROID_API_LEVEL=24
# PyNaCl's bundled libsodium sources have no configure script here; link Termux's own instead.
export SODIUM_INSTALL=system
# Android's linker will not resolve libpython symbols (PyLong_Type, ...) unless an extension links it
# explicitly; maturin-built wheels (cryptography, pydantic-core, jiter, bcrypt) do not by default.
PYLIB="python$(python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
RUSTFLAGS="-C link-arg=-l${PYLIB}" python -m pip install --disable-pip-version-check -r requirements.txt pyinstaller
# Fail here, not three steps later inside PyInstaller, if a compiled dependency cannot be loaded.
python -c "import cryptography.hazmat.bindings._rust, pydantic_core, jiter, bcrypt, nacl.bindings, PIL._imaging"

(cd frontends/tui/ink && npm ci && npm run build)
(cd kernel/session-rs && cargo build --release --locked)

KVIST_BUNDLE_NODE="$(command -v node)"
export KVIST_BUNDLE_NODE
python -m PyInstaller kvist.spec --noconfirm
python scripts/release/binary/smoke.py dist/kvist
# The runner (another user) uploads the result.
chmod -R a+rX dist

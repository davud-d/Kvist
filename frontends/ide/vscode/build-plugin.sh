#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
EXTENSION_DIR="$SCRIPT_DIR"
# Keep the embedded core build private to the extension. The repository-level
# dist/ and build/ directories are used by other installers and must not race it.
CORE_BUILD_DIR="$EXTENSION_DIR/.build/core"
DIST_DIR="$CORE_BUILD_DIR/dist"
WORK_DIR="$CORE_BUILD_DIR/work"
VSIX_DIR="$EXTENSION_DIR/.build/vsix"
LINUX_BINARY_SRC="$DIST_DIR/kvist"
LINUX_BINARY_DST="$EXTENSION_DIR/bin/linux/kvist.bin"

log() {
	printf '===> %s\n' "$1"
}

find_python() {
	local venv_dir
	for venv_dir in "$PROJECT_DIR/.venv" "$PROJECT_DIR/venv"; do
		if [[ -x "$venv_dir/bin/python" ]]; then
			printf '%s\n' "$venv_dir/bin/python"
			return 0
		fi
	done

	if command -v python3 >/dev/null 2>&1; then
		command -v python3
		return 0
	fi

	if command -v python >/dev/null 2>&1; then
		command -v python
		return 0
	fi

	printf 'Python interpreter not found.\n' >&2
	return 1
}

ensure_node_deps() {
	if [[ -d "$EXTENSION_DIR/node_modules" ]]; then
		return 0
	fi

	log "Installing VS Code extension dependencies"
	cd "$EXTENSION_DIR"
	if [[ -f package-lock.json ]]; then
		npm ci
	else
		npm install
	fi
}

case "$(uname -s)" in
	Linux*) ;;
	*)
		printf 'This script builds the Linux VS Code extension package. Use build-plugin.ps1 on Windows.\n' >&2
		exit 1
		;;
esac

PYTHON_BIN="$(find_python)"

if ! command -v npm >/dev/null 2>&1; then
	printf 'npm not found. Install Node.js 18+ and npm first.\n' >&2
	exit 1
fi

if ! command -v npx >/dev/null 2>&1; then
	printf 'npx not found. Install Node.js 18+ and npm first.\n' >&2
	exit 1
fi

log "Project directory: $PROJECT_DIR"
log "Extension directory: $EXTENSION_DIR"
log "Python: $PYTHON_BIN"
log "npm: $(command -v npm)"

if [[ ! -f "$PROJECT_DIR/kvist.spec" ]]; then
	printf 'kvist.spec not found in %s\n' "$PROJECT_DIR" >&2
	exit 1
fi

if [[ ! -f "$EXTENSION_DIR/package.json" ]]; then
	printf 'package.json not found in %s\n' "$EXTENSION_DIR" >&2
	exit 1
fi

ensure_node_deps

log "Cleaning previous Python build artifacts"
rm -rf "$CORE_BUILD_DIR"

log "Building Kvist binary with PyInstaller"
cd "$PROJECT_DIR"
"$PYTHON_BIN" -m PyInstaller kvist.spec --noconfirm --distpath "$DIST_DIR" --workpath "$WORK_DIR"

if [[ ! -f "$LINUX_BINARY_SRC" ]]; then
	printf 'PyInstaller output not found: %s\n' "$LINUX_BINARY_SRC" >&2
	exit 1
fi

log "Installing Linux binary into extension"
install -Dm755 "$LINUX_BINARY_SRC" "$LINUX_BINARY_DST"

log "Compiling VS Code extension"
cd "$EXTENSION_DIR"
npm run build

VERSION="$(node -p "require('./package.json').version")"
VSIX_PATH="$VSIX_DIR/kvist-$VERSION-linux-x64.vsix"
mkdir -p "$VSIX_DIR"

log "Packaging VS Code extension VSIX"
npx --no-install vsce package --no-dependencies --skip-license --out "$VSIX_PATH"

log "Build completed successfully"
printf 'VSIX: %s\n' "$VSIX_PATH"

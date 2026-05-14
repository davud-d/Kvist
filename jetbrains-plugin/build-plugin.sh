#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PLUGIN_DIR="$PROJECT_DIR/jetbrains-plugin"
DIST_DIR="$PROJECT_DIR/dist"
BUILD_DIR="$PROJECT_DIR/build"
PLUGIN_BUILD_DIR="$PLUGIN_DIR/build"
SANDBOX_ROOT="$PLUGIN_DIR/.intellijPlatform/sandbox"
PLUGIN_NAME="devagent-intellij"
LINUX_BINARY_SRC="$DIST_DIR/devagent"
LINUX_BINARY_DST="$PLUGIN_DIR/bin/linux/devagent.bin"

log() {
	printf '===> %s\n' "$1"
}

find_python() {
	if [[ -x "$PROJECT_DIR/venv/bin/python" ]]; then
		printf '%s\n' "$PROJECT_DIR/venv/bin/python"
		return 0
	fi

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

stop_running_sandbox_agents() {
	if ! command -v pgrep >/dev/null 2>&1; then
		return 0
	fi

	local pattern="$SANDBOX_ROOT/.*/plugins/$PLUGIN_NAME/bin/linux/devagent\.bin"
	local pids
	pids="$(pgrep -f "$pattern" || true)"
	if [[ -z "$pids" ]]; then
		return 0
	fi

	log "Stopping running sandbox DevAgent processes"
	while IFS= read -r pid; do
		[[ -n "$pid" ]] || continue
		kill "$pid" 2>/dev/null || true
	done <<< "$pids"

	sleep 1
}

refresh_sandbox_binary() {
	[[ -d "$SANDBOX_ROOT" ]] || return 0

	local copied_any=0
	while IFS= read -r sandbox_bin; do
		[[ -n "$sandbox_bin" ]] || continue
		mkdir -p "$(dirname "$sandbox_bin")"
		install -Dm755 "$LINUX_BINARY_DST" "$sandbox_bin"
		copied_any=1
		printf '    synced: %s\n' "$sandbox_bin"
	done < <(find "$SANDBOX_ROOT" -type f -path "*/plugins/$PLUGIN_NAME/bin/linux/devagent.bin" 2>/dev/null | sort)

	if [[ "$copied_any" -eq 1 ]]; then
		log "Sandbox binaries refreshed"
	fi
}

PYTHON_BIN="$(find_python)"

log "Project directory: $PROJECT_DIR"
log "Plugin directory: $PLUGIN_DIR"
log "Python: $PYTHON_BIN"

if [[ ! -f "$PROJECT_DIR/devagent.spec" ]]; then
	printf 'devagent.spec not found in %s\n' "$PROJECT_DIR" >&2
	exit 1
fi

if [[ ! -x "$PLUGIN_DIR/gradlew" ]]; then
	printf 'gradlew not found or not executable in %s\n' "$PLUGIN_DIR" >&2
	exit 1
fi

stop_running_sandbox_agents

log "Cleaning previous Python build artifacts"
rm -rf "$DIST_DIR" "$BUILD_DIR"

log "Cleaning previous plugin build artifacts"
rm -rf "$PLUGIN_BUILD_DIR"

log "Building DevAgent binary with PyInstaller"
cd "$PROJECT_DIR"
"$PYTHON_BIN" -m PyInstaller devagent.spec

if [[ ! -f "$LINUX_BINARY_SRC" ]]; then
	printf 'PyInstaller output not found: %s\n' "$LINUX_BINARY_SRC" >&2
	exit 1
fi

log "Installing Linux binary into plugin"
install -Dm755 "$LINUX_BINARY_SRC" "$LINUX_BINARY_DST"

refresh_sandbox_binary

log "Building JetBrains plugin ZIP"
cd "$PLUGIN_DIR"
./gradlew clean buildPlugin

log "Build completed successfully"
printf 'ZIP: %s\n' "$PLUGIN_DIR/build/distributions/"

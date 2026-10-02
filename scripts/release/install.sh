#!/usr/bin/env bash
# Installs Kvist from the release assets, on Linux and macOS (best-effort), with
# nothing but bash and curl (the system bash 3.2 of macOS is enough):
#   * the agent binary as ~/.local/share/kvist/kvist-core plus the `kvist` launcher
#     in ~/.local/bin (the layout scripts/installer/install_cli.py produces);
#   * the JetBrains plugin into every JetBrains IDE found;
#   * the VS Code extension into VS Code, VS Code Insiders and VSCodium.
#
#   curl -fsSL https://github.com/davud-d/Kvist/releases/download/latest-plugin-build/install.sh | bash
#   bash install.sh --from ./downloaded-assets      # use files already on disk
set -euo pipefail

RELEASE_URL="https://github.com/davud-d/Kvist/releases/download/latest-plugin-build"
JETBRAINS_ASSET="kvist-jetbrains.zip"
VSCODE_ASSET="kvist-vscode.vsix"
JETBRAINS_FOLDER="kvist-intellij"
JETBRAINS_PRODUCTS='IntelliJIdea|IdeaIC|PyCharm|PyCharmCE|WebStorm|PhpStorm|GoLand|CLion|Rider|RubyMine|DataGrip|DataSpell|RustRover|AndroidStudio|Aqua'

LIB_DIR="${HOME}/.local/share/kvist"
BIN_DIR="${HOME}/.local/bin"

die() {
	printf 'error: %s\n' "$1" >&2
	exit 1
}

step() {
	printf '==> %s\n' "$1"
}

warn() {
	printf 'warning: %s\n' "$1" >&2
}

FROM_DIR=""
while [[ $# -gt 0 ]]; do
	case "$1" in
		--from)
			[[ $# -ge 2 ]] || die "--from needs a directory"
			FROM_DIR="$2"
			shift 2
			;;
		*) die "unknown argument: $1" ;;
	esac
done

OS_NAME="$(uname -s)"
ARCH="$(uname -m)"
case "$OS_NAME/$ARCH" in
	Linux/x86_64) BINARY_ASSET="kvist-linux-x64" ;;
	Darwin/arm64) BINARY_ASSET="kvist-macos-arm64" ;;
	Darwin/x86_64) BINARY_ASSET="kvist-macos-x64" ;;
	Linux/* | Darwin/*) die "no release binary for $OS_NAME on $ARCH" ;;
	*) die "this script supports Linux and macOS only; use install.ps1 on Windows" ;;
esac

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/kvist-install.XXXXXX")"
trap 'rm -rf "$WORK_DIR"' EXIT

# Prints the path of the named asset, downloading it unless --from was given.
fetch() {
	local name="$1"
	if [[ -n "$FROM_DIR" ]]; then
		[[ -f "$FROM_DIR/$name" ]] || die "$name not found in $FROM_DIR"
		printf '%s\n' "$FROM_DIR/$name"
		return
	fi
	command -v curl >/dev/null 2>&1 || die "curl is required to download the release assets"
	step "Downloading $name" >&2
	curl -fSL --progress-bar -o "$WORK_DIR/$name" "$RELEASE_URL/$name" || die "could not download $RELEASE_URL/$name"
	printf '%s\n' "$WORK_DIR/$name"
}

install_cli() {
	local binary="$1"
	mkdir -p "$LIB_DIR" "$BIN_DIR"
	# Copy to a temporary name first: replacing the running binary in place fails with ETXTBSY.
	install -m 755 "$binary" "$LIB_DIR/.kvist-core.new"
	mv -f "$LIB_DIR/.kvist-core.new" "$LIB_DIR/kvist-core"
	# --workspace comes first so extra flags do not have to repeat it; argparse
	# keeps the last occurrence, so an explicit --workspace still wins.
	cat >"$BIN_DIR/.kvist.new" <<EOF
#!/usr/bin/env bash
# Installed by the Kvist release installer.
exec '$LIB_DIR/kvist-core' --workspace "\$PWD" "\$@"
EOF
	chmod 755 "$BIN_DIR/.kvist.new"
	mv -f "$BIN_DIR/.kvist.new" "$BIN_DIR/kvist"
	if [[ "$OS_NAME" == "Darwin" ]]; then
		# A binary saved by a browser is quarantined, and Gatekeeper refuses it unless notarized.
		xattr -d com.apple.quarantine "$LIB_DIR/kvist-core" 2>/dev/null || true
	fi
	step "Installed $LIB_DIR/kvist-core and the launcher $BIN_DIR/kvist"
}

# The directories that hold one folder per installed JetBrains IDE.
jetbrains_roots() {
	if [[ "$OS_NAME" == "Darwin" ]]; then
		printf '%s\n' "$HOME/Library/Application Support/JetBrains"
		return
	fi
	if [[ -n "${XDG_DATA_HOME:-}" ]]; then
		printf '%s\n' "$XDG_DATA_HOME/JetBrains"
	fi
	printf '%s\n' "$HOME/.local/share/JetBrains"
}

jetbrains_config_dirs() {
	local root dir name
	while IFS= read -r root; do
		[[ -d "$root" ]] || continue
		for dir in "$root"/*/; do
			name="$(basename "$dir")"
			if [[ "$name" =~ ^($JETBRAINS_PRODUCTS)[0-9] ]]; then
				printf '%s\n' "${dir%/}"
			fi
		done
	done < <(jetbrains_roots) | sort -u
}

# Where an IDE loads unpacked plugins from: the data directory itself on Linux, a
# plugins folder inside it on macOS (as on Windows).
jetbrains_plugins_dir() {
	if [[ "$OS_NAME" == "Darwin" ]]; then
		printf '%s\n' "$1/plugins"
	else
		printf '%s\n' "$1"
	fi
}

install_jetbrains() {
	local zip="$1" ide plugins staging
	local found=0
	command -v unzip >/dev/null 2>&1 || {
		warn "unzip not found, skipping the JetBrains plugin"
		return
	}
	while IFS= read -r ide; do
		found=1
		plugins="$(jetbrains_plugins_dir "$ide")"
		mkdir -p "$plugins"
		staging="$(mktemp -d "$plugins/.kvist-install.XXXXXX")"
		if unzip -q -o "$zip" -d "$staging" && [[ -d "$staging/$JETBRAINS_FOLDER" ]]; then
			rm -rf "${plugins:?}/$JETBRAINS_FOLDER"
			mv "$staging/$JETBRAINS_FOLDER" "$plugins/$JETBRAINS_FOLDER"
			step "JetBrains plugin installed into $ide (restart the IDE)"
		else
			warn "could not unpack the plugin into $ide"
		fi
		rm -rf "$staging"
	done < <(jetbrains_config_dirs)
	[[ "$found" == 1 ]] || step "No JetBrains IDE found, skipping the plugin"
}

# Prints the command-line launcher of one editor: `$1` on PATH, else the one inside its
# macOS app bundle (`$2`), since `code` is on PATH only after "Install 'code' command".
editor_command() {
	local root candidate
	if command -v "$1" >/dev/null 2>&1; then
		command -v "$1"
		return 0
	fi
	[[ "$OS_NAME" == "Darwin" ]] || return 1
	for root in /Applications "$HOME/Applications"; do
		candidate="$root/$2.app/Contents/Resources/app/bin/$1"
		if [[ -x "$candidate" ]]; then
			printf '%s\n' "$candidate"
			return 0
		fi
	done
	return 1
}

install_vscode() {
	local vsix="$1" pair cli editor
	local found=0
	for pair in "code|Visual Studio Code" "code-insiders|Visual Studio Code - Insiders" "codium|VSCodium"; do
		cli="${pair%%|*}"
		editor="$(editor_command "$cli" "${pair##*|}")" || continue
		found=1
		if "$editor" --install-extension "$vsix" --force >/dev/null; then
			step "VS Code extension installed into $cli (reload the editor)"
		else
			warn "could not install the extension into $cli"
		fi
	done
	[[ "$found" == 1 ]] || step "No VS Code, Insiders or VSCodium found, skipping the extension"
}

# Assigned on their own lines: a failed fetch inside an argument would not stop `set -e`.
binary="$(fetch "$BINARY_ASSET")"
jetbrains_zip="$(fetch "$JETBRAINS_ASSET")"
vsix="$(fetch "$VSCODE_ASSET")"

install_cli "$binary"
install_jetbrains "$jetbrains_zip"
install_vscode "$vsix"

# The command that puts BIN_DIR on PATH for the login shell.
path_hint() {
	local export_line="export PATH=\"$BIN_DIR:\$PATH\""
	case "$(basename "${SHELL:-sh}")" in
		fish) printf 'fish_add_path %s\n' "$BIN_DIR" ;;
		zsh) printf "echo '%s' >> ~/.zshrc\n" "$export_line" ;;
		# Terminal.app starts login shells, which read .bash_profile and not .bashrc.
		bash)
			if [[ "$OS_NAME" == "Darwin" ]]; then
				printf "echo '%s' >> ~/.bash_profile\n" "$export_line"
			else
				printf "echo '%s' >> ~/.bashrc\n" "$export_line"
			fi
			;;
		*) printf '%s\n' "$export_line" ;;
	esac
}

case ":$PATH:" in
	*":$BIN_DIR:"*) printf '\nDone. Run kvist in any directory to start the agent there.\n' ;;
	*) printf '\nDone. %s is not on your PATH. Add it, then open a new terminal and run kvist:\n  %s\n' "$BIN_DIR" "$(path_hint)" ;;
esac

#!/usr/bin/env bash
# Installs Kvist from the release assets, on Linux, with nothing but bash and curl:
#   * the agent binary as ~/.local/share/kvist/kvist-core plus the `kvist` launcher
#     in ~/.local/bin (the layout scripts/installer/install_cli.py produces);
#   * the JetBrains plugin into every JetBrains IDE found;
#   * the VS Code extension into VS Code, VS Code Insiders and VSCodium.
#
#   curl -fsSL https://github.com/davud-d/Kvist/releases/download/latest-plugin-build/install.sh | bash
#   bash install.sh --from ./downloaded-assets      # use files already on disk
set -euo pipefail

RELEASE_URL="https://github.com/davud-d/Kvist/releases/download/latest-plugin-build"
BINARY_ASSET="kvist-linux-x64"
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

[[ "$(uname -s)" == "Linux" ]] || die "this script supports Linux only; use install.ps1 on Windows"
[[ "$(uname -m)" == "x86_64" ]] || die "the release binary is built for x86_64, this machine is $(uname -m)"

WORK_DIR="$(mktemp -d)"
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
	step "Installed $LIB_DIR/kvist-core and the launcher $BIN_DIR/kvist"
}

jetbrains_config_dirs() {
	local root dir name
	for root in "${XDG_DATA_HOME:+$XDG_DATA_HOME/JetBrains}" "$HOME/.local/share/JetBrains"; do
		[[ -n "$root" && -d "$root" ]] || continue
		for dir in "$root"/*/; do
			name="$(basename "$dir")"
			if [[ "$name" =~ ^($JETBRAINS_PRODUCTS)[0-9] ]]; then
				printf '%s\n' "${dir%/}"
			fi
		done
	done | sort -u
}

install_jetbrains() {
	local zip="$1" ide staging
	local found=0
	command -v unzip >/dev/null 2>&1 || {
		warn "unzip not found, skipping the JetBrains plugin"
		return
	}
	while IFS= read -r ide; do
		found=1
		staging="$(mktemp -d "$ide/.kvist-install.XXXXXX")"
		if unzip -q -o "$zip" -d "$staging" && [[ -d "$staging/$JETBRAINS_FOLDER" ]]; then
			rm -rf "${ide:?}/$JETBRAINS_FOLDER"
			mv "$staging/$JETBRAINS_FOLDER" "$ide/$JETBRAINS_FOLDER"
			step "JetBrains plugin installed into $ide (restart the IDE)"
		else
			warn "could not unpack the plugin into $ide"
		fi
		rm -rf "$staging"
	done < <(jetbrains_config_dirs)
	[[ "$found" == 1 ]] || step "No JetBrains IDE found, skipping the plugin"
}

install_vscode() {
	local vsix="$1" editor
	local found=0
	for editor in code code-insiders codium; do
		command -v "$editor" >/dev/null 2>&1 || continue
		found=1
		if "$editor" --install-extension "$vsix" --force >/dev/null; then
			step "VS Code extension installed into $editor (reload the editor)"
		else
			warn "could not install the extension into $editor"
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

case ":$PATH:" in
	*":$BIN_DIR:"*) printf '\nDone. Run kvist in any directory to start the agent there.\n' ;;
	*) printf '\nDone. %s is not on your PATH, add it (fish: fish_add_path %s) and run kvist.\n' "$BIN_DIR" "$BIN_DIR" ;;
esac

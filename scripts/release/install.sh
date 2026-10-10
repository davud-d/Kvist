#!/usr/bin/env bash
# Installs Kvist from the release assets, on Linux and macOS (best-effort), with
# nothing but bash and curl (the system bash 3.2 of macOS is enough):
#   * the agent binary as ~/.local/share/kvist/kvist-core plus the `kvist` launcher
#     in ~/.local/bin (the layout scripts/installer/install_cli.py produces);
#   * the JetBrains plugin into every JetBrains IDE found;
#   * the VS Code extension into VS Code, VS Code Insiders and VSCodium;
#   * Node.js, which the terminal interface runs on, as ~/.local/share/kvist/node
#     (downloaded from nodejs.org and checked against a pinned SHA-256);
#   * on request (--only), the PATH line in the login shell's startup file.
#
# With a terminal to draw on, it downloads the agent and Node.js into a temporary folder and
# opens the installer screen: what to install is ticked there, and the setup follows it.
#
#   curl -fsSL https://github.com/davud-d/Kvist/releases/download/latest-plugin-build/install.sh | bash
#   KVIST_INSTALL_PLAIN=1 bash install.sh           # no screen, install the defaults
#   bash install.sh --from ./downloaded-assets      # use files already on disk
#   bash install.sh --plan                          # JSON: what each component would touch here
#   bash install.sh --only cli,path --events        # chosen components, progress as JSON lines
set -euo pipefail

# KVIST_RELEASE_URL points the download at a mirror or a local test server.
RELEASE_URL="${KVIST_RELEASE_URL:-https://github.com/davud-d/Kvist/releases/download/latest-plugin-build}"
JETBRAINS_ASSET="kvist-jetbrains.zip"
VSCODE_ASSET="kvist-vscode.vsix"
JETBRAINS_FOLDER="kvist-intellij"
JETBRAINS_PRODUCTS='IntelliJIdea|IdeaIC|PyCharm|PyCharmCE|WebStorm|PhpStorm|GoLand|CLion|Rider|RubyMine|DataGrip|DataSpell|RustRover|AndroidStudio|Aqua'

# The Node.js the interface runs on, pinned: the SHA-256 sums are those of nodejs.org's
# SHASUMS256.txt for this version.
NODE_VERSION="22.23.3"
NODE_URL="https://nodejs.org/dist/v${NODE_VERSION}"

LIB_DIR="${HOME}/.local/share/kvist"
BIN_DIR="${HOME}/.local/bin"

FROM_DIR=""
CACHE_DIR=""
PLAN=0
EVENTS=0
ONLY_GIVEN=0
# Components installed when --only is absent: what the script always did.
ONLY="cli,jetbrains,vscode"

# One JSON string literal; quotes, backslashes and control characters are escaped.
json_string() {
	local text="$1" out="" char i
	for ((i = 0; i < ${#text}; i++)); do
		char="${text:i:1}"
		case "$char" in
			'"') out+='\"' ;;
			'\') out+='\\' ;;
			$'\n') out+='\n' ;;
			$'\t') out+='\t' ;;
			$'\r') ;;
			*) out+="$char" ;;
		esac
	done
	printf '"%s"' "$out"
}

# Under --events every line the person would read is one JSON object on stdout instead.
emit() {
	[[ "$EVENTS" == 1 ]] || return 0
	printf '{"event":%s' "$(json_string "$1")"
	shift
	while [[ $# -ge 2 ]]; do
		printf ',%s:%s' "$(json_string "$1")" "$(json_string "$2")"
		shift 2
	done
	printf '}\n'
}

die() {
	if [[ "$EVENTS" == 1 ]]; then
		emit error message "$1"
	else
		printf 'error: %s\n' "$1" >&2
	fi
	exit 1
}

step() {
	if [[ "$EVENTS" == 1 ]]; then emit step message "$1"; else printf '==> %s\n' "$1"; fi
}

warn() {
	if [[ "$EVENTS" == 1 ]]; then emit warning message "$1"; else printf 'warning: %s\n' "$1" >&2; fi
}

while [[ $# -gt 0 ]]; do
	case "$1" in
		--from)
			[[ $# -ge 2 ]] || die "--from needs a directory"
			FROM_DIR="$2"
			shift 2
			;;
		--cache)
			[[ $# -ge 2 ]] || die "--cache needs a directory"
			CACHE_DIR="$2"
			shift 2
			;;
		--only)
			[[ $# -ge 2 ]] || die "--only needs a comma-separated list of components"
			ONLY="$2"
			ONLY_GIVEN=1
			shift 2
			;;
		--plan)
			PLAN=1
			shift
			;;
		--events)
			EVENTS=1
			shift
			;;
		*) die "unknown argument: $1" ;;
	esac
done

# Without --only the defaults are all but the PATH line; Node.js is among them only when the
# assets are downloaded (--from is an offline install of the files given).
if [[ "$ONLY_GIVEN" == 0 && -z "$FROM_DIR" ]]; then
	ONLY="cli,node,jetbrains,vscode"
fi

wanted() {
	case ",$ONLY," in *",$1,"*) return 0 ;; *) return 1 ;; esac
}

OS_NAME="$(uname -s)"
ARCH="$(uname -m)"
case "$OS_NAME/$ARCH" in
	Linux/x86_64)
		BINARY_ASSET="kvist-linux-x64"
		NODE_ASSET="node-v${NODE_VERSION}-linux-x64.tar.gz"
		NODE_SHA256="1084aa36196bba4c3a5e69a1ee388a6e4ff729dad09445fbcd434b28fe3c24af"
		;;
	Darwin/arm64)
		BINARY_ASSET="kvist-macos-arm64"
		NODE_ASSET="node-v${NODE_VERSION}-darwin-arm64.tar.gz"
		NODE_SHA256="23b25245dcfb9af7262f8ff142e9e2e0af025368117329e7a7458a51e5922f53"
		;;
	Darwin/x86_64)
		BINARY_ASSET="kvist-macos-x64"
		NODE_ASSET="node-v${NODE_VERSION}-darwin-x64.tar.gz"
		NODE_SHA256="8a677b0219178efd6eb0e475457c4afb452b521a92f6e67845a73bd85727f2a8"
		;;
	Linux/* | Darwin/*) die "no release binary for $OS_NAME on $ARCH" ;;
	*) die "this script supports Linux and macOS only; use install.ps1 on Windows" ;;
esac

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/kvist-install.XXXXXX")"
trap 'rm -rf "$WORK_DIR"' EXIT

# Sets FETCHED to the path of the named asset, downloading it unless --from was given. Not run
# in a command substitution: its progress events go to stdout like every other step's.
fetch() {
	local name="$1"
	if [[ -n "$FROM_DIR" ]]; then
		[[ -f "$FROM_DIR/$name" ]] || die "$name not found in $FROM_DIR"
		FETCHED="$FROM_DIR/$name"
		return
	fi
	# --cache: assets a previous step already downloaded; whatever is missing is downloaded.
	if [[ -n "$CACHE_DIR" && -f "$CACHE_DIR/$name" ]]; then
		FETCHED="$CACHE_DIR/$name"
		return
	fi
	command -v curl >/dev/null 2>&1 || die "curl is required to download the release assets"
	step "Downloading $name"
	local progress="--progress-bar"
	[[ "$EVENTS" == 1 ]] && progress="--silent --show-error"
	# shellcheck disable=SC2086
	curl -fSL $progress -o "$WORK_DIR/$name" "$RELEASE_URL/$name" || die "could not download $RELEASE_URL/$name"
	FETCHED="$WORK_DIR/$name"
}

sha256_of() {
	local sum
	if command -v sha256sum >/dev/null 2>&1; then
		sum="$(sha256sum "$1")"
	elif command -v shasum >/dev/null 2>&1; then
		sum="$(shasum -a 256 "$1")"
	else
		die "sha256sum or shasum is needed to check the Node.js download"
	fi
	printf '%s\n' "${sum%% *}"
}

# Sets NODE_HOME to an unpacked Node.js (bin/node inside): the one the bootstrap already
# prepared (KVIST_NODE_HOME), or the pinned release downloaded, checked and unpacked here.
fetch_node() {
	if [[ -n "${KVIST_NODE_HOME:-}" ]]; then
		NODE_HOME="$KVIST_NODE_HOME"
		return
	fi
	local tarball="$WORK_DIR/$NODE_ASSET"
	if [[ -n "$FROM_DIR" ]]; then
		[[ -f "$FROM_DIR/$NODE_ASSET" ]] || die "$NODE_ASSET not found in $FROM_DIR"
		tarball="$FROM_DIR/$NODE_ASSET"
	elif [[ -n "$CACHE_DIR" && -f "$CACHE_DIR/$NODE_ASSET" ]]; then
		tarball="$CACHE_DIR/$NODE_ASSET"
	else
		command -v curl >/dev/null 2>&1 || die "curl is required to download Node.js"
		step "Downloading Node.js $NODE_VERSION from nodejs.org"
		local progress="--progress-bar"
		[[ "$EVENTS" == 1 ]] && progress="--silent --show-error"
		# shellcheck disable=SC2086
		curl -fSL $progress -o "$tarball" "$NODE_URL/$NODE_ASSET" || die "could not download $NODE_URL/$NODE_ASSET"
	fi
	[[ "$(sha256_of "$tarball")" == "$NODE_SHA256" ]] || die "$NODE_ASSET does not match the pinned SHA-256; refusing to use it"
	mkdir -p "$WORK_DIR/node"
	tar -xzf "$tarball" -C "$WORK_DIR/node" --strip-components=1 || die "could not unpack $NODE_ASSET"
	NODE_HOME="$WORK_DIR/node"
}

install_node() {
	mkdir -p "$LIB_DIR"
	rm -rf "$LIB_DIR/.node.new"
	cp -R "$NODE_HOME" "$LIB_DIR/.node.new"
	rm -rf "$LIB_DIR/node"
	mv "$LIB_DIR/.node.new" "$LIB_DIR/node"
	step "Installed Node.js $NODE_VERSION as $LIB_DIR/node"
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
	cat >"$BIN_DIR/.kvist-web.new" <<EOF
#!/usr/bin/env bash
# Installed by the Kvist release installer.
exec '$LIB_DIR/kvist-core' --web-controller "\$@"
EOF
	chmod 755 "$BIN_DIR/.kvist-web.new"
	mv -f "$BIN_DIR/.kvist-web.new" "$BIN_DIR/kvist-web"
	cat >"$BIN_DIR/.kvist-web-bridge.new" <<EOF
#!/usr/bin/env bash
# Installed by the Kvist release installer.
exec '$LIB_DIR/kvist-core' --web-bridge "\$@"
EOF
	chmod 755 "$BIN_DIR/.kvist-web-bridge.new"
	mv -f "$BIN_DIR/.kvist-web-bridge.new" "$BIN_DIR/kvist-web-bridge"
	if [[ "$OS_NAME" == "Darwin" ]]; then
		# A binary saved by a browser is quarantined, and Gatekeeper refuses it unless notarized.
		xattr -d com.apple.quarantine "$LIB_DIR/kvist-core" 2>/dev/null || true
	fi
	step "Installed $LIB_DIR/kvist-core and launchers kvist, kvist-web, kvist-web-bridge"
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
	local root dir name settings data existing
	while IFS= read -r root; do
		[[ -d "$root" ]] || continue
		for dir in "$root"/*/; do
			name="$(basename "$dir")"
			if [[ "$name" =~ ^($JETBRAINS_PRODUCTS)[0-9] ]]; then
				printf '%s\n' "${dir%/}"
			fi
		done
	done < <(jetbrains_roots) | sort -u

	# A fresh Linux IDE may have settings but no plugin data directory yet.
	# Find it by settings and return the data destination, never the settings path.
	if [[ "$OS_NAME" == "Linux" ]]; then
		settings="${XDG_CONFIG_HOME:-$HOME/.config}/JetBrains"
		data="${XDG_DATA_HOME:-$HOME/.local/share}/JetBrains"
		for dir in "$settings"/*/; do
			[[ -d "$dir" ]] || continue
			name="$(basename "$dir")"
			[[ "$name" =~ ^($JETBRAINS_PRODUCTS)[0-9] ]] || continue
			existing=0
			while IFS= read -r root; do
				if [[ -d "$root/$name" ]]; then
					existing=1
					break
				fi
			done < <(jetbrains_roots)
			if [[ "$existing" == 0 ]]; then
				printf '%s\n' "$data/$name"
			fi
		done
	fi
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

# The file the PATH line goes into for the login shell, as an absolute path.
path_rc_file() {
	case "$(basename "${SHELL:-sh}")" in
		fish) printf '%s\n' "$HOME/.config/fish/conf.d/kvist.fish" ;;
		zsh) printf '%s\n' "$HOME/.zshrc" ;;
		bash)
			if [[ "$OS_NAME" == "Darwin" ]]; then
				printf '%s\n' "$HOME/.bash_profile"
			else
				printf '%s\n' "$HOME/.bashrc"
			fi
			;;
		*) printf '%s\n' "$HOME/.profile" ;;
	esac
}

path_rc_line() {
	case "$(basename "${SHELL:-sh}")" in
		fish) printf 'fish_add_path %s\n' "$BIN_DIR" ;;
		*) printf 'export PATH="%s:$PATH"\n' "$BIN_DIR" ;;
	esac
}

on_path() {
	case ":$PATH:" in *":$BIN_DIR:"*) return 0 ;; *) return 1 ;; esac
}

# Appends the PATH line once; a file that already has it is left alone.
install_path() {
	local rc line
	rc="$(path_rc_file)"
	line="$(path_rc_line)"
	mkdir -p "$(dirname "$rc")"
	if [[ -f "$rc" && "$(<"$rc")" == *"$line"* ]]; then
		step "$BIN_DIR is already added in $rc"
		return
	fi
	printf '\n# Added by the Kvist installer\n%s\n' "$line" >>"$rc"
	step "Added $BIN_DIR to PATH in $rc (open a new terminal)"
}

# --plan: what each component would do on this machine, as facts (paths and commands); the
# words around them belong to whoever shows them.
json_array() {
	local first=1 item
	printf '['
	for item in "$@"; do
		[[ "$first" == 1 ]] || printf ','
		first=0
		json_string "$item"
	done
	printf ']'
}

print_plan() {
	local dir plugins cli editor pair
	local -a jetbrains_targets=() vscode_targets=() cli_targets=()
	cli_targets=("$LIB_DIR/kvist-core" "$BIN_DIR/kvist" "$BIN_DIR/kvist-web" "$BIN_DIR/kvist-web-bridge")
	if [[ "$OS_NAME" == "Darwin" ]]; then
		cli_targets+=("xattr -d com.apple.quarantine $LIB_DIR/kvist-core")
	fi
	if command -v unzip >/dev/null 2>&1; then
		while IFS= read -r dir; do
			[[ -n "$dir" ]] || continue
			plugins="$(jetbrains_plugins_dir "$dir")"
			jetbrains_targets+=("$plugins/$JETBRAINS_FOLDER")
		done < <(jetbrains_config_dirs)
	fi
	for pair in "code|Visual Studio Code" "code-insiders|Visual Studio Code - Insiders" "codium|VSCodium"; do
		cli="${pair%%|*}"
		editor="$(editor_command "$cli" "${pair##*|}")" || continue
		vscode_targets+=("$editor --install-extension $VSCODE_ASSET --force")
	done
	printf '{"os":%s,"arch":%s,"shell":%s,"components":[' "$(json_string "$OS_NAME")" "$(json_string "$ARCH")" "$(json_string "$(basename "${SHELL:-sh}")")"
	printf '{"id":"cli","available":true,"targets":%s}' "$(json_array "${cli_targets[@]}")"
	printf ',{"id":"node","available":true,"targets":%s}' \
		"$(json_array "$NODE_URL/$NODE_ASSET" "$LIB_DIR/node")"
	printf ',{"id":"jetbrains","available":%s,"targets":%s}' \
		"$([[ ${#jetbrains_targets[@]} -gt 0 ]] && echo true || echo false)" "$(json_array ${jetbrains_targets[@]+"${jetbrains_targets[@]}"})"
	printf ',{"id":"vscode","available":%s,"targets":%s}' \
		"$([[ ${#vscode_targets[@]} -gt 0 ]] && echo true || echo false)" "$(json_array ${vscode_targets[@]+"${vscode_targets[@]}"})"
	printf ',{"id":"path","available":%s,"targets":%s}' \
		"$(on_path && echo false || echo true)" "$(json_array "$(path_rc_file): $(path_rc_line)")"
	printf ']}\n'
}

if [[ "$PLAN" == 1 ]]; then
	print_plan
	exit 0
fi

# The installer screen: the agent and Node.js go to a temporary folder first (the screen runs on
# both), the person chooses there, and the screen starts the setup when it is done. This script
# does the installing itself, driven by the screen through --plan and --only … --events.
bootstrap_screen() {
	local core script
	printf 'Kvist installer: downloading the agent and Node.js %s into a temporary folder (about 100 MB).\n' "$NODE_VERSION"
	printf 'Nothing is installed until you choose it on the next screen.\n'
	fetch "$BINARY_ASSET"
	core="$FETCHED"
	chmod +x "$core"
	if [[ "$OS_NAME" == "Darwin" ]]; then
		xattr -d com.apple.quarantine "$core" 2>/dev/null || true
	fi
	fetch_node
	# The binary needs the C library of the machine it was built on; say so here, not as a
	# loader error from inside the temporary folder.
	"$core" --help >/dev/null 2>&1 || die "the downloaded kvist-core cannot run on this system (a C library older than the one it was built against?); install from source instead (see the README)"
	# Run from a pipe (`curl | bash`) there is no file to hand over: the script is fetched again.
	if [[ -f "${BASH_SOURCE[0]:-}" ]]; then
		script="${BASH_SOURCE[0]}"
	else
		curl -fsSL -o "$WORK_DIR/install.sh" "$RELEASE_URL/install.sh" || die "could not download $RELEASE_URL/install.sh"
		script="$WORK_DIR/install.sh"
	fi
	KVIST_INSTALL_SCRIPT="$script" KVIST_INSTALL_CACHE="$WORK_DIR" KVIST_NODE_HOME="$NODE_HOME" \
		KVIST_NODE="$NODE_HOME/bin/node" "$core" --installer </dev/tty
}

# A terminal to draw on, and nobody asked for a particular install: show the screen. stdin is the
# script itself under `curl | bash`, so the terminal is opened by name.
if [[ "$ONLY_GIVEN" == 0 && -z "$FROM_DIR" && -z "${KVIST_INSTALL_PLAIN:-}" && "$EVENTS" == 0 ]] && { : </dev/tty; } 2>/dev/null; then
	bootstrap_screen
	exit $?
fi

# Every selected asset is fetched before anything is installed, so a missing one stops the run
# with the machine untouched.
binary="" jetbrains_zip="" vsix=""
if wanted cli; then
	fetch "$BINARY_ASSET"
	binary="$FETCHED"
fi
if wanted node; then
	fetch_node
fi
if wanted jetbrains; then
	fetch "$JETBRAINS_ASSET"
	jetbrains_zip="$FETCHED"
fi
if wanted vscode; then
	fetch "$VSCODE_ASSET"
	vsix="$FETCHED"
fi

# One component: announces it, runs it, reports it. A failure inside a component is a warning
# of its own, so the others still run.
run_component() {
	local id="$1"
	shift
	emit component id "$id" status start
	"$@"
	emit component id "$id" status done
}

if wanted cli; then run_component cli install_cli "$binary"; fi
if wanted node; then run_component node install_node; fi
if wanted jetbrains; then run_component jetbrains install_jetbrains "$jetbrains_zip"; fi
if wanted vscode; then run_component vscode install_vscode "$vsix"; fi
if wanted path; then run_component path install_path; fi

if [[ "$EVENTS" == 1 ]]; then
	emit done
	exit 0
fi

if on_path; then
	printf '\nDone. Run kvist in any directory to start the agent there.\n'
else
	printf '\nDone. %s is not on your PATH. Add it, then open a new terminal and run kvist:\n  %s\n' "$BIN_DIR" "$(path_hint)"
fi

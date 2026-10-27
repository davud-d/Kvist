#!/usr/bin/env bash
# Installs Kvist from the release assets, on Linux and macOS (best-effort), with
# nothing but bash and curl (the system bash 3.2 of macOS is enough):
#   * the agent binary as ~/.local/share/kvist/kvist-core plus the `kvist` launcher
#     in ~/.local/bin (the layout scripts/installer/install_cli.py produces);
#   * the JetBrains plugin into every JetBrains IDE found;
#   * the VS Code extension into VS Code, VS Code Insiders and VSCodium;
#   * Node.js, which the terminal interface runs on, as ~/.local/share/kvist/node
#     (downloaded from nodejs.org and checked against a pinned SHA-256);
#   * a static ffmpeg as ~/.local/share/kvist/ffmpeg (pinned SHA-256) that plays the preview
#     video of the setup screen;
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
# Other servers with the same files. The pinned SHA-256 is what makes using them safe: whatever a
# server sends, only the exact pinned file is kept. The fastest one for this machine is used, since
# routes to a single server can be slow or blocked for a whole provider.
NODE_MIRRORS=("https://cdn.npmmirror.com/binaries/node/v${NODE_VERSION}" "https://mirrors.ustc.edu.cn/node/v${NODE_VERSION}")
# The Bun the OpenCode-style interface runs on, pinned: the SHA-256 sums are those of the
# oven-sh/bun release's own SHASUMS256.txt. It lives beside the other private tools, never on PATH.
BUN_VERSION="1.3.14"
BUN_URL="https://github.com/oven-sh/bun/releases/download/bun-v${BUN_VERSION}"
FFMPEG_MIRRORS=("https://cdn.npmmirror.com/binaries/ffmpeg-static/b6.0")
# The ffmpeg that plays the preview video of the setup screen: the static FFmpeg 6.0 builds that
# the ffmpeg-static npm package ships, from its tagged GitHub release (a fast CDN, and the files
# of a tag do not change). Each is a gzipped single binary, pinned by the SHA-256 recorded when
# it was chosen; the publisher gives no SHA-256 of its own, so this pins the file, not its origin.
FFMPEG_VERSION="6.0"
FFMPEG_URL="https://github.com/eugeneware/ffmpeg-static/releases/download/b6.0"

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
FFMPEG_ASSET=""
FFMPEG_SHA256=""
BUN_ASSET=""
BUN_SHA256=""
case "$OS_NAME/$ARCH" in
	Linux/x86_64)
		FFMPEG_ASSET="ffmpeg-linux-x64.gz"
		FFMPEG_SHA256="17c1ae10b52ac499180679fe6ba77e17642390c4eedb0f1e3b0ac045da55128f"
		BUN_ASSET="bun-linux-x64.zip"
		BUN_SHA256="951ee2aee855f08595aeec6225226a298d3fea83a3dcd6465c09cbccdf7e848f"
		BINARY_ASSET="kvist-linux-x64"
		NODE_ASSET="node-v${NODE_VERSION}-linux-x64.tar.gz"
		NODE_SHA256="1084aa36196bba4c3a5e69a1ee388a6e4ff729dad09445fbcd434b28fe3c24af"
		;;
	Linux/aarch64 | Linux/arm64)
		FFMPEG_ASSET="ffmpeg-linux-arm64.gz"
		FFMPEG_SHA256="2b708b2d15041d2a192c1db24c7a8a1d24f645a8242dce1c744ff2392b86ada1"
		BUN_ASSET="bun-linux-aarch64.zip"
		BUN_SHA256="a27ffb63a8310375836e0d6f668ae17fa8d8d18b88c37c821c65331973a19a3b"
		BINARY_ASSET="kvist-linux-arm64"
		NODE_ASSET="node-v${NODE_VERSION}-linux-arm64.tar.gz"
		NODE_SHA256="5ced2d48d1d7198739b7f86804de0171aefb6823b684b12341d3321afc3cb0b2"
		;;
	Darwin/arm64)
		FFMPEG_ASSET="ffmpeg-darwin-arm64.gz"
		FFMPEG_SHA256="6be74d6f449889c2e87a75873894f8520cad56c08ac76f2a628d85b0519daaca"
		BUN_ASSET="bun-darwin-aarch64.zip"
		BUN_SHA256="d8b96221828ad6f97ac7ac0ab7e95872341af763001e8803e8267652c2652620"
		BINARY_ASSET="kvist-macos-arm64"
		NODE_ASSET="node-v${NODE_VERSION}-darwin-arm64.tar.gz"
		NODE_SHA256="23b25245dcfb9af7262f8ff142e9e2e0af025368117329e7a7458a51e5922f53"
		;;
	Darwin/x86_64)
		FFMPEG_ASSET="ffmpeg-darwin-x64.gz"
		FFMPEG_SHA256="a12354fce7eb62361473bbe10d53a1893695babd35869ec8e92e5dfea8d0440b"
		BUN_ASSET="bun-darwin-x64.zip"
		BUN_SHA256="4183df3374623e5bab315c547cfa0974533cd457d86b73b639f7a87974cd6633"
		BINARY_ASSET="kvist-macos-x64"
		NODE_ASSET="node-v${NODE_VERSION}-darwin-x64.tar.gz"
		NODE_SHA256="8a677b0219178efd6eb0e475457c4afb452b521a92f6e67845a73bd85727f2a8"
		;;
	Linux/* | Darwin/*) die "no release binary for $OS_NAME on $ARCH" ;;
	*) die "this script supports Linux and macOS only; use install.ps1 on Windows" ;;
esac

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/kvist-install.XXXXXX")"
trap 'rm -rf "$WORK_DIR"' EXIT

# Downloads $2 to $1 and returns curl's status. A transfer under 5 kB/s for 30 s is given up on and
# a refused connection is retried, so a dead mirror ends the step instead of hanging it. Under
# --events curl's progress bar has nowhere to go, so the size so far is reported once a second.
curl_download() {
	local dest="$1" url="$2" label="$3" pid size
	local -a options=(-fSL --connect-timeout 20 --retry 2 --speed-limit 5000 --speed-time 30)
	if [[ "$EVENTS" != 1 ]]; then
		curl "${options[@]}" --progress-bar -o "$dest" "$url"
		return
	fi
	curl "${options[@]}" --silent --show-error -o "$dest" "$url" &
	pid=$!
	while kill -0 "$pid" 2>/dev/null; do
		if [[ -f "$dest" ]]; then
			size="$(wc -c <"$dest")"
			emit step message "Downloading $label: $((size / 1048576)) MB"
		fi
		sleep 1
	done
	wait "$pid"
}

# Sets RANKED to the given URLs, fastest first. Each is asked for its first megabyte, all at once and
# for at most 8 s, so a server that is slow or unreachable from here costs seconds and goes last.
rank_mirrors() {
	local probe_dir="$WORK_DIR/probe" i j n best code speed
	local -a urls=("$@") speeds=() used=()
	RANKED=()
	n=${#urls[@]}
	if [[ "$n" -le 1 ]]; then
		RANKED=("${urls[@]}")
		return 0
	fi
	rm -rf "$probe_dir"
	mkdir -p "$probe_dir"
	for ((i = 0; i < n; i++)); do
		curl -sL -o /dev/null -r 0-1048575 --connect-timeout 5 --max-time 8 \
			-w '%{http_code} %{speed_download}' "${urls[i]}" >"$probe_dir/$i" 2>/dev/null &
	done
	wait
	for ((i = 0; i < n; i++)); do
		code=0
		speed=0
		read -r code speed <"$probe_dir/$i" 2>/dev/null || true
		speed="${speed%%.*}"
		[[ "$code" == 200 || "$code" == 206 ]] || speed=-1
		speeds+=("${speed:-0}")
		used+=(0)
	done
	for ((i = 0; i < n; i++)); do
		best=-1
		for ((j = 0; j < n; j++)); do
			[[ "${used[j]}" == 0 ]] || continue
			if [[ "$best" == -1 ]] || [[ "${speeds[j]}" -gt "${speeds[best]}" ]]; then
				best=$j
			fi
		done
		used[best]=1
		RANKED+=("${urls[best]}")
		if [[ "$i" == 0 ]]; then
			BEST_SPEED="${speeds[best]}"
		fi
	done
}

# Tries the servers fastest first until one delivers a file with the expected SHA-256.
# Usage: download_verified DEST LABEL SHA256 URL... ; returns 1 when none did.
download_verified() {
	local dest="$1" label="$2" sha="$3" url host first=1
	shift 3
	BEST_SPEED=0
	if [[ $# -gt 1 ]]; then
		step "Choosing the fastest server for $label"
	fi
	rank_mirrors "$@"
	for url in "${RANKED[@]}"; do
		host="${url#*://}"
		host="${host%%/*}"
		if [[ "$first" == 1 && "${BEST_SPEED:-0}" -gt 0 ]]; then
			step "Downloading $label from $host ($((BEST_SPEED / 1000000)).$((BEST_SPEED / 100000 % 10)) MB/s)"
		else
			step "Downloading $label from $host"
		fi
		first=0
		if curl_download "$dest" "$url" "$label" && [[ "$(sha256_of "$dest")" == "$sha" ]]; then
			return 0
		fi
		warn "$label from $host did not arrive intact; trying the next server"
		rm -f "$dest"
	done
	return 1
}

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
	curl_download "$WORK_DIR/$name" "$RELEASE_URL/$name" "$name" || die "could not download $RELEASE_URL/$name"
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
		local -a node_urls=("$NODE_URL/$NODE_ASSET")
		local mirror
		for mirror in "${NODE_MIRRORS[@]}"; do
			node_urls+=("$mirror/$NODE_ASSET")
		done
		download_verified "$tarball" "Node.js $NODE_VERSION" "$NODE_SHA256" "${node_urls[@]}" || die "could not download Node.js $NODE_VERSION from any server"
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

# Sets FFMPEG_HOME to a folder holding the unpacked ffmpeg: downloaded, checked against the pinned
# SHA-256, and only then unpacked. ffmpeg only plays a preview, so a failure here is a
# warning and a non-zero status, never the end of the install.
fetch_ffmpeg() {
	local tarball="$WORK_DIR/$FFMPEG_ASSET"
	if [[ -z "$FFMPEG_ASSET" ]]; then
		warn "there is no pinned ffmpeg build for $OS_NAME on $ARCH"
		return 1
	fi
	if [[ -n "$FROM_DIR" ]]; then
		if [[ ! -f "$FROM_DIR/$FFMPEG_ASSET" ]]; then
			warn "$FFMPEG_ASSET not found in $FROM_DIR"
			return 1
		fi
		tarball="$FROM_DIR/$FFMPEG_ASSET"
	elif [[ -n "$CACHE_DIR" && -f "$CACHE_DIR/$FFMPEG_ASSET" ]]; then
		tarball="$CACHE_DIR/$FFMPEG_ASSET"
	else
		if ! command -v curl >/dev/null 2>&1; then
			warn "curl is required to download ffmpeg"
			return 1
		fi
		local -a ffmpeg_urls=("$FFMPEG_URL/$FFMPEG_ASSET")
		local mirror
		for mirror in "${FFMPEG_MIRRORS[@]}"; do
			ffmpeg_urls+=("$mirror/$FFMPEG_ASSET")
		done
		if ! download_verified "$tarball" "ffmpeg $FFMPEG_VERSION" "$FFMPEG_SHA256" "${ffmpeg_urls[@]}"; then
			warn "could not download ffmpeg from any server; the preview video will not play"
			return 1
		fi
	fi
	if [[ "$(sha256_of "$tarball")" != "$FFMPEG_SHA256" ]]; then
		warn "$FFMPEG_ASSET does not match the pinned SHA-256; refusing to use it"
		return 1
	fi
	mkdir -p "$WORK_DIR/ffmpeg"
	if ! gzip -dc "$tarball" >"$WORK_DIR/ffmpeg/ffmpeg"; then
		warn "could not unpack $FFMPEG_ASSET"
		return 1
	fi
	FFMPEG_HOME="$WORK_DIR/ffmpeg"
}

install_ffmpeg() {
	mkdir -p "$LIB_DIR"
	rm -rf "$LIB_DIR/.ffmpeg.new"
	mkdir -p "$LIB_DIR/.ffmpeg.new"
	cp "$FFMPEG_HOME/ffmpeg" "$LIB_DIR/.ffmpeg.new/"
	chmod 755 "$LIB_DIR/.ffmpeg.new/ffmpeg"
	rm -rf "$LIB_DIR/ffmpeg"
	mv "$LIB_DIR/.ffmpeg.new" "$LIB_DIR/ffmpeg"
	step "Installed ffmpeg $FFMPEG_VERSION as $LIB_DIR/ffmpeg/ffmpeg"
}

# Sets BUN_HOME to a folder holding the unpacked bun: downloaded, checked against the pinned
# SHA-256, and only then unpacked. Without Bun only the OpenCode-style interface is lost (the
# launcher falls back to Ink), so a failure is a warning and a non-zero status.
fetch_bun() {
	local archive="$WORK_DIR/$BUN_ASSET"
	if [[ -z "$BUN_ASSET" ]]; then
		warn "there is no pinned Bun build for $OS_NAME on $ARCH"
		return 1
	fi
	if ! command -v unzip >/dev/null 2>&1; then
		warn "unzip not found, skipping Bun"
		return 1
	fi
	if [[ -n "$FROM_DIR" ]]; then
		if [[ ! -f "$FROM_DIR/$BUN_ASSET" ]]; then
			warn "$BUN_ASSET not found in $FROM_DIR"
			return 1
		fi
		archive="$FROM_DIR/$BUN_ASSET"
	elif [[ -n "$CACHE_DIR" && -f "$CACHE_DIR/$BUN_ASSET" ]]; then
		archive="$CACHE_DIR/$BUN_ASSET"
	else
		if ! command -v curl >/dev/null 2>&1; then
			warn "curl is required to download Bun"
			return 1
		fi
		if ! download_verified "$archive" "Bun $BUN_VERSION" "$BUN_SHA256" "$BUN_URL/$BUN_ASSET"; then
			warn "could not download Bun; the OpenCode-style interface will use the Ink one"
			return 1
		fi
	fi
	if [[ "$(sha256_of "$archive")" != "$BUN_SHA256" ]]; then
		warn "$BUN_ASSET does not match the pinned SHA-256; refusing to use it"
		return 1
	fi
	mkdir -p "$WORK_DIR/bun"
	if ! unzip -q -o "$archive" -d "$WORK_DIR/bun" || [[ ! -f "$WORK_DIR/bun/${BUN_ASSET%.zip}/bun" ]]; then
		warn "could not unpack $BUN_ASSET"
		return 1
	fi
	BUN_HOME="$WORK_DIR/bun/${BUN_ASSET%.zip}"
}

install_bun() {
	mkdir -p "$LIB_DIR"
	rm -rf "$LIB_DIR/.bun.new"
	mkdir -p "$LIB_DIR/.bun.new"
	cp "$BUN_HOME/bun" "$LIB_DIR/.bun.new/"
	chmod 755 "$LIB_DIR/.bun.new/bun"
	rm -rf "$LIB_DIR/bun"
	mv "$LIB_DIR/.bun.new" "$LIB_DIR/bun"
	step "Installed Bun $BUN_VERSION as $LIB_DIR/bun/bun"
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

# Build tools: the system packages `scripts/installer/install_cli.py` (a build from source)
# needs. Opt-in only, never among the defaults, and they are the one part of this script that
# uses administrator rights.
package_manager() {
	local pm
	for pm in apt-get dnf zypper pacman; do
		if command -v "$pm" >/dev/null 2>&1; then
			printf '%s\n' "$pm"
			return
		fi
	done
}

# How packages can be installed here: as root, with a sudo that needs no password (cached or
# configured), with a sudo that asks for one, or not at all.
privilege() {
	if [[ "$EUID" == 0 ]]; then
		echo root
	elif ! command -v sudo >/dev/null 2>&1; then
		echo none
	elif sudo -n true 2>/dev/null; then
		echo cached
	else
		echo password
	fi
}

# Runs a command with administrator rights when that needs no question; 126 otherwise.
privileged() {
	case "$(privilege)" in
		root) "$@" ;;
		cached) sudo -n "$@" ;;
		*) return 126 ;;
	esac
}

# Rust is what Kvist Classic and the voice screen are built with (a release binary already carries
# them). The toolchain comes from rustup, which needs a C compiler to link and no administrator
# rights of its own.
cargo_path() {
	command -v cargo 2>/dev/null && return 0
	local candidate="${CARGO_HOME:-$HOME/.cargo}/bin/cargo"
	[[ -x "$candidate" ]] && printf '%s\n' "$candidate"
}

has_c_compiler() {
	command -v cc >/dev/null 2>&1 || command -v gcc >/dev/null 2>&1 || command -v clang >/dev/null 2>&1
}

# Like build_item, for Rust: the C compiler is a system package where there is a package manager;
# a Mac gets one from the Xcode command line tools, which only the person can install.
rust_item() {
	local pm
	pm="$(package_manager)"
	if [[ -n "$(cargo_path)" ]]; then
		BUILD_STATE="present"
		return 0
	fi
	if ! has_c_compiler; then
		[[ -n "$pm" ]] || return 0
		case "$pm" in
			apt-get) BUILD_PACKAGES=(build-essential) ;;
			*) BUILD_PACKAGES=(gcc) ;;
		esac
	fi
	BUILD_STATE="ready"
}

# Sets BUILD_STATE (ready, present or unsupported) and BUILD_PACKAGES for one build tool.
build_item() {
	local id="$1" pm
	BUILD_STATE="unsupported"
	BUILD_PACKAGES=()
	if [[ "$id" == "rust" ]]; then
		rust_item
		return 0
	fi
	pm="$(package_manager)"
	[[ -n "$pm" ]] || return 0
	case "$id" in
		python)
			if command -v python3 >/dev/null 2>&1 && python3 -c 'import venv, ensurepip' >/dev/null 2>&1; then
				BUILD_STATE="present"
				return 0
			fi
			case "$pm" in
				apt-get) BUILD_PACKAGES=(python3 python3-venv python3-pip) ;;
				dnf | zypper) BUILD_PACKAGES=(python3 python3-pip) ;;
				pacman) BUILD_PACKAGES=(python python-pip) ;;
			esac
			;;
		libpython)
			# PyInstaller bundles the interpreter's shared library; pacman's python ships it.
			case "$pm" in
				apt-get) BUILD_PACKAGES=(python3-dev) ;;
				dnf | zypper) BUILD_PACKAGES=(python3-devel) ;;
				pacman) return 0 ;;
			esac
			if command -v python3 >/dev/null 2>&1 && python3 -c 'import os, sys, sysconfig as s; sys.exit(0 if s.get_config_var("Py_ENABLE_SHARED") == 1 and os.path.isfile(os.path.join(s.get_config_var("LIBDIR") or "", s.get_config_var("LDLIBRARY") or "")) else 1)' >/dev/null 2>&1; then
				BUILD_STATE="present"
				BUILD_PACKAGES=()
				return 0
			fi
			;;
		binutils)
			if command -v objdump >/dev/null 2>&1; then
				BUILD_STATE="present"
				return 0
			fi
			BUILD_PACKAGES=(binutils)
			;;
	esac
	if [[ ${#BUILD_PACKAGES[@]} -gt 0 ]]; then
		BUILD_STATE="ready"
	fi
}

# The commands that install BUILD_PACKAGES, one per line, without a sudo prefix.
build_commands() {
	[[ ${#BUILD_PACKAGES[@]} -gt 0 ]] || return 0
	case "$(package_manager)" in
		apt-get) printf 'apt-get update\napt-get install -y %s\n' "${BUILD_PACKAGES[*]}" ;;
		dnf) printf 'dnf install -y %s\n' "${BUILD_PACKAGES[*]}" ;;
		zypper) printf 'zypper --non-interactive install %s\n' "${BUILD_PACKAGES[*]}" ;;
		pacman) printf 'pacman -S --needed --noconfirm %s\n' "${BUILD_PACKAGES[*]}" ;;
	esac
}

RUSTUP_COMMAND="curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal"

# rustup's own installer (minimal profile); it adds ~/.cargo/bin to the shell's startup file.
install_rustup() {
	command -v curl >/dev/null 2>&1 || {
		warn "curl is required to install Rust"
		return 1
	}
	step "Running: $RUSTUP_COMMAND"
	if ! curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal >/dev/null 2>&1; then
		warn "rustup did not finish; run it yourself: $RUSTUP_COMMAND"
		return 1
	fi
}

# "sudo " in front of what a person would type, unless they are root already.
sudo_prefix() {
	if [[ "$EUID" == 0 ]]; then printf ''; else printf 'sudo '; fi
}

# Returns 1 when a command could not run, after telling the person exactly what to run themselves.
install_build_item() {
	local line
	build_item "$1"
	if [[ "$BUILD_STATE" != "ready" ]]; then
		step "Nothing to install for $1 on this system"
		return 0
	fi
	# The whole sequence, as one line the person can paste if a step cannot run here.
	local sequence=""
	while IFS= read -r line; do
		sequence="${sequence:+$sequence && }$(sudo_prefix)$line"
	done < <(build_commands)
	while IFS= read -r line; do
		step "Running: $(sudo_prefix)$line"
		# The command is a fixed line of package names: splitting it into words is the intent.
		# shellcheck disable=SC2086
		if ! privileged $line >/dev/null 2>&1; then
			warn "could not run it here; run it yourself: $sequence"
			return 1
		fi
	done < <(build_commands)
	if [[ "$1" == "rust" ]]; then
		if ! has_c_compiler; then
			warn "no C compiler found; on a Mac run: xcode-select --install"
			return 1
		fi
		install_rustup
	fi
}

# A failed build tool is reported and the others still run.
run_build_item() {
	emit component id "$1" status start
	if install_build_item "$1"; then
		emit component id "$1" status done
	else
		emit component id "$1" status failed
	fi
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
	printf '{"os":%s,"arch":%s,"shell":%s,"privilege":%s,"components":[' "$(json_string "$OS_NAME")" "$(json_string "$ARCH")" "$(json_string "$(basename "${SHELL:-sh}")")" "$(json_string "$(privilege)")"
	printf '{"id":"cli","available":true,"targets":%s}' "$(json_array "${cli_targets[@]}")"
	printf ',{"id":"node","available":true,"targets":%s}' \
		"$(json_array "$NODE_URL/$NODE_ASSET" "$LIB_DIR/node")"
	local ffmpeg_state="" ffmpeg_available=false
	if [[ -z "$FFMPEG_ASSET" ]]; then
		ffmpeg_state="unsupported"
	elif command -v ffmpeg >/dev/null 2>&1 || [[ -x "$LIB_DIR/ffmpeg/ffmpeg" ]]; then
		ffmpeg_state="present"
	else
		ffmpeg_available=true
	fi
	printf ',{"id":"ffmpeg","available":%s,"reason":%s,"targets":%s}' \
		"$ffmpeg_available" "$(json_string "$ffmpeg_state")" "$(json_array "$FFMPEG_URL/$FFMPEG_ASSET" "$LIB_DIR/ffmpeg")"
	local bun_state="" bun_available=false
	if [[ -z "$BUN_ASSET" ]]; then
		bun_state="unsupported"
	elif command -v bun >/dev/null 2>&1 || [[ -x "$LIB_DIR/bun/bun" ]]; then
		bun_state="present"
	else
		bun_available=true
	fi
	printf ',{"id":"bun","available":%s,"reason":%s,"targets":%s}' \
		"$bun_available" "$(json_string "$bun_state")" "$(json_array "$BUN_URL/$BUN_ASSET" "$LIB_DIR/bun")"
	printf ',{"id":"jetbrains","available":%s,"targets":%s}' \
		"$([[ ${#jetbrains_targets[@]} -gt 0 ]] && echo true || echo false)" "$(json_array ${jetbrains_targets[@]+"${jetbrains_targets[@]}"})"
	printf ',{"id":"vscode","available":%s,"targets":%s}' \
		"$([[ ${#vscode_targets[@]} -gt 0 ]] && echo true || echo false)" "$(json_array ${vscode_targets[@]+"${vscode_targets[@]}"})"
	printf ',{"id":"path","available":%s,"targets":%s}' \
		"$(on_path && echo false || echo true)" "$(json_array "$(path_rc_file): $(path_rc_line)")"
	local id line reason available
	local -a commands
	for id in python libpython binutils rust; do
		build_item "$id"
		available=false
		reason=""
		commands=()
		case "$BUILD_STATE" in
			ready)
				available=true
				while IFS= read -r line; do
					commands+=("$(sudo_prefix)$line")
				done < <(build_commands)
				;;
			present) reason="present" ;;
			*) reason="unsupported" ;;
		esac
		[[ "$id" == "rust" && "$BUILD_STATE" == "ready" ]] && commands+=("$RUSTUP_COMMAND")
		printf ',{"id":%s,"available":%s,"reason":%s,"targets":%s}' \
			"$(json_string "$id")" "$available" "$(json_string "$reason")" "$(json_array ${commands[@]+"${commands[@]}"})"
	done
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
# with the machine untouched. Each component announces itself as it is fetched, so the screen
# shows what is being waited for.
binary="" jetbrains_zip="" vsix="" ffmpeg_ok=0 bun_ok=0
if wanted cli; then
	emit component id cli status start
	fetch "$BINARY_ASSET"
	binary="$FETCHED"
fi
if wanted node; then
	emit component id node status start
	fetch_node
fi
if wanted ffmpeg; then
	emit component id ffmpeg status start
	if fetch_ffmpeg; then
		ffmpeg_ok=1
	else
		emit component id ffmpeg status failed
	fi
fi
if wanted bun; then
	emit component id bun status start
	if fetch_bun; then
		bun_ok=1
	else
		emit component id bun status failed
	fi
fi
if wanted jetbrains; then
	emit component id jetbrains status start
	fetch "$JETBRAINS_ASSET"
	jetbrains_zip="$FETCHED"
fi
if wanted vscode; then
	emit component id vscode status start
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
if wanted ffmpeg && [[ "$ffmpeg_ok" == 1 ]]; then run_component ffmpeg install_ffmpeg; fi
if wanted bun && [[ "$bun_ok" == 1 ]]; then run_component bun install_bun; fi
if wanted jetbrains; then run_component jetbrains install_jetbrains "$jetbrains_zip"; fi
if wanted vscode; then run_component vscode install_vscode "$vsix"; fi
if wanted path; then run_component path install_path; fi
for build_tool in python libpython binutils rust; do
	if wanted "$build_tool"; then run_build_item "$build_tool"; fi
done

if [[ "$EVENTS" == 1 ]]; then
	emit done
	exit 0
fi

if on_path; then
	printf '\nDone. Run kvist in any directory to start the agent there.\n'
else
	printf '\nDone. %s is not on your PATH. Add it, then open a new terminal and run kvist:\n  %s\n' "$BIN_DIR" "$(path_hint)"
fi

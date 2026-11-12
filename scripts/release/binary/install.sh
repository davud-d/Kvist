#!/usr/bin/env bash
# Installs Kvist from the single-binary release, on Linux and macOS, with nothing but bash and
# curl (the system bash 3.2 of macOS is enough). The binary carries the core, the terminal UI, the
# Node.js it runs on and the native session program, so this is the whole install:
#   * ~/.local/share/kvist/kvist-core, checked against the release's SHA256SUMS;
#   * the `kvist` launcher in ~/.local/bin (the layout the full installer also produces);
#   * on a first install with a terminal, the first-run setup screen right away.
#
#   curl -fsSL https://github.com/davud-d/Kvist/releases/download/latest-binary-build/install.sh | bash
#   KVIST_INSTALL_NO_RUN=1 bash install.sh     # install only, do not open the setup screen
#
# KVIST_RELEASE_URL points the download at a mirror or a local test server (file:// works).
set -euo pipefail

RELEASE_URL="${KVIST_RELEASE_URL:-https://github.com/davud-d/Kvist/releases/download/latest-binary-build}"
LIB_DIR="${HOME}/.local/share/kvist"
BIN_DIR="${HOME}/.local/bin"

die() {
	printf 'error: %s\n' "$1" >&2
	exit 1
}

step() {
	printf '==> %s\n' "$1"
}

case "$(uname -s)" in
	Linux) os=linux ;;
	Darwin) os=macos ;;
	*) die "this installer is for Linux and macOS; on Windows use install.ps1" ;;
esac
case "$(uname -m)" in
	x86_64 | amd64) arch=x64 ;;
	aarch64 | arm64) arch=arm64 ;;
	*) die "there is no Kvist binary for the processor $(uname -m)" ;;
esac
asset="kvist-${os}-${arch}"

command -v curl >/dev/null 2>&1 || die "curl is required"

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/kvist-install.XXXXXX")"
trap 'rm -rf "$WORK_DIR"' EXIT

sha256_of() {
	if command -v sha256sum >/dev/null 2>&1; then
		sha256sum "$1" | cut -d' ' -f1
	elif command -v shasum >/dev/null 2>&1; then
		shasum -a 256 "$1" | cut -d' ' -f1
	else
		die "neither sha256sum nor shasum is available to check the download"
	fi
}

step "Downloading $asset (the one file that holds everything, about 180 MB)"
curl -fSL --retry 3 --progress-bar -o "$WORK_DIR/$asset" "$RELEASE_URL/$asset" || die "could not download $RELEASE_URL/$asset"
curl -fsSL --retry 3 -o "$WORK_DIR/SHA256SUMS" "$RELEASE_URL/SHA256SUMS" || die "could not download $RELEASE_URL/SHA256SUMS"

# The sums file lists `<hash>  <name>`; only this system's line is compared.
expected="$(awk -v name="$asset" '$2 == name { print $1 }' "$WORK_DIR/SHA256SUMS")"
[[ -n "$expected" ]] || die "SHA256SUMS has no entry for $asset"
[[ "$(sha256_of "$WORK_DIR/$asset")" == "$expected" ]] || die "the downloaded $asset does not match its SHA-256 sum; nothing was installed"

chmod +x "$WORK_DIR/$asset"
if [[ "$os" == "macos" ]]; then
	# A file fetched by a browser is quarantined, and Gatekeeper refuses it unless notarized.
	xattr -d com.apple.quarantine "$WORK_DIR/$asset" 2>/dev/null || true
fi
# Say so here if the C library is too old, not as a loader error from inside the install.
"$WORK_DIR/$asset" --version >/dev/null 2>&1 || die "the downloaded binary cannot run on this system (a C library older than the one it was built against?)"

first_install=1
[[ -e "$LIB_DIR/kvist-core" ]] && first_install=0

mkdir -p "$LIB_DIR" "$BIN_DIR"
# Copy to a temporary name first: replacing the running binary in place fails with ETXTBSY.
install -m 755 "$WORK_DIR/$asset" "$LIB_DIR/.kvist-core.new"
mv -f "$LIB_DIR/.kvist-core.new" "$LIB_DIR/kvist-core"
# --workspace comes first so extra flags do not have to repeat it; argparse keeps the last
# occurrence, so an explicit --workspace still wins.
cat >"$BIN_DIR/.kvist.new" <<EOF
#!/usr/bin/env bash
# Installed by the Kvist single-binary installer.
exec '$LIB_DIR/kvist-core' --workspace "\$PWD" "\$@"
EOF
chmod 755 "$BIN_DIR/.kvist.new"
mv -f "$BIN_DIR/.kvist.new" "$BIN_DIR/kvist"
step "Installed $LIB_DIR/kvist-core and the kvist launcher in $BIN_DIR"

case ":$PATH:" in
	*":$BIN_DIR:"*) ;;
	*) printf '%s is not on your PATH. Add it, then open a new terminal and run kvist:\n  export PATH="%s:$PATH"\n' "$BIN_DIR" "$BIN_DIR" ;;
esac

# The first run has no provider yet, so kvist opens the setup screen by itself. stdin is the
# script under `curl | bash`, so the terminal is opened by name.
if [[ "$first_install" == 1 && -z "${KVIST_INSTALL_NO_RUN:-}" ]] && { : </dev/tty; } 2>/dev/null; then
	# exec skips the EXIT trap, so the download is removed first.
	rm -rf "$WORK_DIR"
	exec "$BIN_DIR/kvist" </dev/tty
fi
printf 'Done. Run kvist in a project folder to start.\n'

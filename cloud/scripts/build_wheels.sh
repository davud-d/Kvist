#!/usr/bin/env bash
# Builds the wheels a customer's VM installs offline (setup_vm.sh: pip install --no-index): kvist, kvist-runner
# and every dependency. Built in a Debian 12 container because the guest is Debian 12 (Python 3.11): wheels
# made against this computer's Python would not load there.
#
#   scripts/build_wheels.sh <out-dir>
#
# Needs podman or docker. The result is cached by a hash of the sources, so an unchanged core costs nothing.
set -euo pipefail

CLOUD="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO="$(cd "$CLOUD/.." && pwd)"
OUT="${1:-}"
[[ -n "$OUT" ]] || { echo "usage: build_wheels.sh <out-dir>" >&2; exit 2; }
ENGINE="$(command -v podman || command -v docker || true)"
[[ -n "$ENGINE" ]] || { echo "error: podman or docker is needed to build the guest's wheels" >&2; exit 1; }
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"

# What the two packages are built from. Not the whole repository: it holds gigabytes of frontends and build output.
SOURCES=(pyproject.toml README.md LICENSE kernel frontends/gui/electron/renderer frontends/tui/classic
         frontends/tui/ink/dist-ui frontends/zed frontends/web-local cloud/runner)
TAR_ARGS=(--sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner
          --exclude=__pycache__ --exclude=node_modules --exclude=.pytest_cache --exclude=.ruff_cache
          --exclude=kernel/session-rs --exclude=kernel/tests --exclude='*.egg-info' --exclude=.venv)

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
tar -C "$REPO" "${TAR_ARGS[@]}" -cf "$WORK/src.tar" "${SOURCES[@]}"
HASH="$(sha256sum "$WORK/src.tar" | cut -d' ' -f1)"
if [[ -f "$OUT/.source-sha256" && "$(cat "$OUT/.source-sha256")" == "$HASH" ]] && ls "$OUT"/kvist-*.whl >/dev/null 2>&1; then
    echo "wheels are up to date ($(ls "$OUT"/*.whl | wc -l) files)"
    exit 0
fi

cat > "$WORK/build.sh" <<'EOF'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3-venv python3-dev build-essential libffi-dev libssl-dev >/dev/null
mkdir /src && tar -C /src -xf /work/src.tar
python3 -m venv /venv
/venv/bin/pip install -q --upgrade pip wheel
rm -f /out/*.whl
/venv/bin/pip wheel -q -w /out /src /src/cloud/runner
EOF
echo "Building the guest's wheels in Debian 12 (the first time takes a few minutes)"
"$ENGINE" run --rm -v "$WORK:/work:ro,Z" -v "$OUT:/out:Z" docker.io/library/debian:12 bash /work/build.sh
echo "$HASH" > "$OUT/.source-sha256"
echo "ok: $(ls "$OUT"/*.whl | wc -l) wheels in $OUT"

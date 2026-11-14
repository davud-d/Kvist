#!/usr/bin/env bash
# Ships the working tree to the server and switches to it. Run on your computer:
#
#   scripts/deploy.sh user@server
#
# Builds the frontend, copies cloud/ to /opt/kvist-cloud/releases/<id> over ssh, then runs activate_release.sh there
# (it checks the configuration, backs up, migrates, switches, restarts, checks health and rolls back if it fails).
# The user needs sudo on the server. Nothing is pushed to any git remote.
set -euo pipefail

CLOUD="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER="${1:-}"
[[ -n "$SERVER" ]] || { echo "usage: deploy.sh user@server" >&2; exit 2; }
ROOT=/opt/kvist-cloud

command -v rsync >/dev/null || { echo "error: rsync is needed" >&2; exit 1; }

REVISION="$(git -C "$CLOUD" rev-parse --short HEAD 2>/dev/null || echo nogit)"
if [[ -n "$(git -C "$CLOUD" status --porcelain -- . 2>/dev/null)" ]]; then
    echo "warning: the working tree has uncommitted changes; the release will contain them" >&2
    REVISION="$REVISION-dirty"
fi
RELEASE="$(date -u +%Y%m%dT%H%M%SZ)-$REVISION"

echo "Building the frontend"
(cd "$CLOUD/frontend" && { [[ -d node_modules ]] || npm install --no-audit --no-fund --silent; } && node build.mjs)

echo "Vendoring the core files the server imports"
"$CLOUD/scripts/vendor_kernel.sh" "$CLOUD/vendor"

echo "Building the wheels customers' VMs install (cached while the sources are unchanged)"
"$CLOUD/scripts/build_wheels.sh" "$CLOUD/.wheels"

echo "Copying release $RELEASE"
ssh "$SERVER" "sudo install -d -o \"\$(id -un)\" $ROOT/releases/$RELEASE"
rsync -a --delete \
    --exclude '.venv' --exclude 'node_modules' --exclude '.cloud-data' --exclude '.env' --exclude '.wheels' \
    --exclude '__pycache__' --exclude '*.egg-info' --exclude '.lighthouse' --exclude '.pytest_cache' --exclude '.ruff_cache' \
    "$CLOUD/" "$SERVER:$ROOT/releases/$RELEASE/"

# Shared by all releases and root-owned: the VM helper puts these onto every customer's guest.
rsync -a --delete --chown=root:root --exclude '.source-sha256' --rsync-path="sudo rsync" \
    "$CLOUD/.wheels/" "$SERVER:$ROOT/wheels/"

echo "Activating"
ssh -t "$SERVER" "sudo $ROOT/releases/$RELEASE/scripts/activate_release.sh $ROOT/releases/$RELEASE"

#!/usr/bin/env bash
# Ships the working tree to the server and switches to it. Run on your computer:
#
#   scripts/deploy.sh user@server           a frontend-only update when that is all that changed, else a full release
#   scripts/deploy.sh --full user@server    always a full release (say, after editing cloud.env on the server)
#
# Full: builds the frontend, copies cloud/ to /opt/kvist-cloud/releases/<id> over ssh, then runs activate_release.sh
# there (it checks the configuration, backs up, migrates, switches, restarts, checks health and rolls back if it fails).
# Frontend-only: when every change since the live revision is in the web or phone frontend (cloud/frontend,
# cloud/mobile, cloud/workspace) or outside what the server and the guests run, only the built frontend is uploaded
# and swapped in by activate_frontend.sh: seconds, not the minutes of a venv, a backup and a guest image.
# The user needs sudo on the server. Nothing is pushed to any git remote.
set -euo pipefail

CLOUD="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO="$(dirname "$CLOUD")"
FULL=0
if [[ "${1:-}" == --full ]]; then FULL=1; shift; fi
SERVER="${1:-}"
[[ -n "$SERVER" ]] || { echo "usage: deploy.sh [--full] user@server" >&2; exit 2; }
ROOT=/opt/kvist-cloud

command -v rsync >/dev/null || { echo "error: rsync is needed" >&2; exit 1; }

REVISION="$(git -C "$CLOUD" rev-parse --short HEAD 2>/dev/null || echo nogit)"
if [[ -n "$(git -C "$CLOUD" status --porcelain -- . 2>/dev/null)" ]]; then
    echo "warning: the working tree has uncommitted changes; the release will contain them" >&2
    REVISION="$REVISION-dirty"
fi
RELEASE="$(date -u +%Y%m%dT%H%M%SZ)-$REVISION"

# True when nothing the server or a guest runs differs from the live revision: only the frontends changed.
frontend_only() {
    local live="$1" path source
    [[ "$live" =~ ^[0-9a-f]{7,40}$ ]] || return 1  # "-dirty" or unknown: what runs there is not a commit
    git -C "$REPO" cat-file -e "$live^{commit}" 2>/dev/null || return 1
    local -a sources
    mapfile -t sources < <("$CLOUD/scripts/build_wheels.sh" --sources)
    while IFS= read -r path; do
        [[ -n "$path" ]] || continue
        case "$path" in
            cloud/frontend/* | cloud/mobile/* | cloud/workspace/*) continue ;;
            cloud/*) return 1 ;;
        esac
        for source in "${sources[@]}"; do
            [[ "$path" == "$source" || "$path" == "$source/"* ]] && return 1
        done
    done < <(git -C "$REPO" diff --name-only "$live" HEAD; git -C "$REPO" status --porcelain -- cloud | cut -c4-)
    return 0
}

echo "Building the frontend"
(cd "$CLOUD/frontend" && { [[ -d node_modules ]] || npm install --no-audit --no-fund --silent; } && node build.mjs)

if [[ $FULL == 0 ]]; then
    LIVE_DIR="$(ssh "$SERVER" "readlink -f $ROOT/current")"
    LIVE="$(ssh "$SERVER" "cat $LIVE_DIR/REVISION 2>/dev/null || true")"
    if [[ -z "$LIVE" ]]; then
        LIVE="$(basename "$LIVE_DIR")"
        LIVE="${LIVE#*Z-}"  # releases are named <UTC time>Z-<revision>
    fi
    if frontend_only "$LIVE"; then
        # The uploaded frontend replaces the whole of the old one, so the next comparison starts from HEAD.
        HEAD="$(git -C "$REPO" rev-parse --short HEAD)"
        echo "Only the frontends changed since $LIVE: updating the frontend of $(basename "$LIVE_DIR")"
        rsync -a --delete --link-dest="$LIVE_DIR/frontend/dist/" "$CLOUD/frontend/dist/" "$SERVER:$LIVE_DIR/frontend/dist.new/"
        rsync -a "$CLOUD/scripts/activate_frontend.sh" "$SERVER:$LIVE_DIR/scripts/"
        ssh -t "$SERVER" "sudo $LIVE_DIR/scripts/activate_frontend.sh $LIVE_DIR $HEAD"
        exit 0
    fi
    echo "Changes since ${LIVE:-the live release} reach beyond the frontends: a full release"
fi

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

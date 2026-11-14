#!/usr/bin/env bash
# Runs on the server, as root, from deploy.sh: makes a copied release the live one, or leaves the old one running.
#
#   activate_release.sh /opt/kvist-cloud/releases/<id>
#
# Order, and why: the new code is installed and its configuration checked BEFORE anything running is touched; the
# database is backed up before it is migrated; the switch is one symlink, so going back is one symlink too.
# Migrations must therefore stay compatible with the release that is still live (add first, remove a release later).
# Environment: KVIST_ENV_FILE (/etc/kvist-cloud/cloud.env), KVIST_SKIP_BACKUP=1 to deploy without the pre-migration dump.
set -Eeuo pipefail

ROOT=/opt/kvist-cloud
ENV_FILE="${KVIST_ENV_FILE:-/etc/kvist-cloud/cloud.env}"
KEEP=5

die() { echo "error: $*" >&2; exit 1; }

RELEASE="${1:-}"
[[ $EUID -eq 0 ]] || die "run as root"
[[ -d "$RELEASE/backend" && "$RELEASE" == "$ROOT/releases/"* ]] || die "usage: activate_release.sh $ROOT/releases/<id>"
[[ -f "$ENV_FILE" ]] || die "$ENV_FILE not found (scripts/gen-secrets.sh makes it)"
id kvist-cloud >/dev/null 2>&1 || die "the user kvist-cloud does not exist (see docs/DEPLOY.md)"

echo "== installing"
python3 -m venv "$RELEASE/.venv"
"$RELEASE/.venv/bin/pip" install -q --upgrade pip
"$RELEASE/.venv/bin/pip" install -q -e "$RELEASE/backend[postgres]"
export KVIST_CLOUD_ENV_FILE="$ENV_FILE"
# The few core files the server imports travel inside the release (scripts/vendor_kernel.sh).
export PYTHONPATH="$RELEASE/vendor"
[[ -f "$RELEASE/vendor/kernel/llm/provider_registry.py" ]] || die "$RELEASE/vendor is missing: deploy with scripts/deploy.sh"
PY="$RELEASE/.venv/bin/python"

echo "== checking the configuration"
"$PY" -m kvist_cloud.prodcheck --env-file "$ENV_FILE"

if grep -qx 'MACHINE_PROVIDER=libvirt' "$ENV_FILE"; then
    echo "== preparing the VM host"
    "$RELEASE/scripts/install_vm_host.sh"
fi

if [[ "${KVIST_SKIP_BACKUP:-}" == 1 ]]; then
    echo "== backup SKIPPED (KVIST_SKIP_BACKUP=1)"
elif [[ -f /etc/kvist-cloud/backup.env ]]; then
    echo "== backing up the database"
    "$RELEASE/scripts/backup.sh" db
else
    die "no /etc/kvist-cloud/backup.env: set up backups first (docs/BACKUP.md) or KVIST_SKIP_BACKUP=1"
fi

echo "== migrating"
"$PY" - <<'PY'
from kvist_cloud.config import load_settings
from kvist_cloud.db import upgrade

upgrade(load_settings().database_url)
PY

# Only a real link counts: on the first deploy there is nothing to go back to (readlink -f would answer with the path itself).
PREVIOUS=""
if [[ -L "$ROOT/current" ]]; then PREVIOUS="$(readlink -f "$ROOT/current")"; fi

switch_to() {
    ln -sfn "$1" "$ROOT/current.new"
    mv -T "$ROOT/current.new" "$ROOT/current"
}

wait_healthy() {
    for _ in $(seq 1 60); do
        if curl -fsS -m 3 http://127.0.0.1:8740/healthz >/dev/null 2>&1; then return 0; fi
        sleep 1
    done
    return 1
}

echo "== switching"
switch_to "$RELEASE"
systemctl restart kvist-cloud
if ! wait_healthy; then
    echo "FAIL: the new release did not become healthy within 60 s" >&2
    journalctl -u kvist-cloud -n 30 --no-pager >&2 || true
    if [[ -n "$PREVIOUS" && -d "$PREVIOUS" ]]; then
        echo "== rolling back to $PREVIOUS" >&2
        switch_to "$PREVIOUS"
        systemctl restart kvist-cloud
        if wait_healthy; then
            echo "rolled back; the old release is serving again" >&2
        else
            echo "FAIL: the old release is not healthy either" >&2
        fi
    else
        echo "there is no earlier release to go back to; nothing is serving" >&2
    fi
    exit 1
fi

echo "== checking the data"
"$PY" -m kvist_cloud.integrity --env-file "$ENV_FILE" || echo "warning: the integrity check reported problems (the release stays live)" >&2

# Old releases (never the live one or the one just left) are only disk space.
ls -1dt "$ROOT"/releases/*/ | tail -n +$((KEEP + 1)) | while read -r old; do
    old="${old%/}"
    [[ "$old" == "$RELEASE" || "$old" == "$PREVIOUS" ]] || rm -rf "$old"
done
echo "ok: $(basename "$RELEASE") is live"

#!/usr/bin/env bash
# Rotates KVIST_CLOUD_MASTER_KEY in two steps, on the server, as root.
#
#   rotate_master_key.sh start    new key in front of the old one, every stored secret re-encrypted, restart
#   rotate_master_key.sh finish   drop the old key(s) from the env file, restart
#
# Between the two the server opens secrets with either key, so nothing breaks while you check. Do `finish` only
# after: (1) `start` printed ok, (2) the site works, (3) the NEW key is saved off the server.
#
# KEEP THE OLD KEY OFFLINE for as long as backups made before the rotation exist (6 months by default): those
# backups hold secrets encrypted with it, and without it they restore with unreadable model keys.
# No key is ever printed. Environment: KVIST_ENV_FILE (/etc/kvist-cloud/cloud.env).
set -Eeuo pipefail

ROOT=/opt/kvist-cloud
ENV_FILE="${KVIST_ENV_FILE:-/etc/kvist-cloud/cloud.env}"
PY="$ROOT/current/.venv/bin/python"
die() { echo "error: $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run as root"
[[ -f "$ENV_FILE" ]] || die "$ENV_FILE not found"
[[ -x "$PY" ]] || die "no live release at $ROOT/current"
export KVIST_CLOUD_ENV_FILE="$ENV_FILE"
export PYTHONPATH="$ROOT/current/vendor"

current_keys() { sed -n 's/^KVIST_CLOUD_MASTER_KEY=//p' "$ENV_FILE" | tail -n 1; }

# Rewrites the key line through a temp file in the same directory, keeping owner and mode; a half-written env file
# would stop the server from starting.
set_keys() {
    local value="$1" tmp
    tmp="$(mktemp "$ENV_FILE.XXXXXX")"
    chmod --reference="$ENV_FILE" "$tmp"
    chown --reference="$ENV_FILE" "$tmp"
    awk -v v="$value" 'BEGIN{done=0} /^KVIST_CLOUD_MASTER_KEY=/ {print "KVIST_CLOUD_MASTER_KEY=" v; done=1; next} {print} END{if(!done) exit 3}' \
        "$ENV_FILE" > "$tmp"
    mv "$tmp" "$ENV_FILE"
}

restart_and_wait() {
    systemctl restart kvist-cloud
    for _ in $(seq 1 60); do
        curl -fsS -m 3 http://127.0.0.1:8740/healthz >/dev/null 2>&1 && return 0
        sleep 1
    done
    die "the server did not become healthy after the restart; the env file is in its new state, look at the journal"
}

start() {
    local old new
    old="$(current_keys)"
    [[ -n "$old" ]] || die "KVIST_CLOUD_MASTER_KEY is not set in $ENV_FILE"
    [[ "$old" != *,* ]] || die "a rotation is already in progress (several keys listed); run finish first"
    if [[ -f /etc/kvist-cloud/backup.env ]]; then
        echo "== backing up the database first"
        "$ROOT/current/scripts/backup.sh" db
    else
        die "no /etc/kvist-cloud/backup.env: a rotation without a fresh backup is not worth the risk"
    fi
    new="$(head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '\n')"
    cp -p "$ENV_FILE" "$ENV_FILE.before-rotation"
    set_keys "$new,$old"
    echo "== re-encrypting (dry run)"
    "$PY" -m kvist_cloud.rotate_key --dry-run
    echo "== re-encrypting"
    "$PY" -m kvist_cloud.rotate_key
    restart_and_wait
    echo "ok: every secret is on the new key; the old one is still accepted."
    echo "Now: save the NEW key (the first one on the KVIST_CLOUD_MASTER_KEY line of $ENV_FILE) off this server,"
    echo "keep the OLD one too (see the note at the top of this script), check the site, then run: $0 finish"
    echo "$ENV_FILE.before-rotation holds the old settings and the old key: delete it once the old key is stored offline."
}

finish() {
    local keys
    keys="$(current_keys)"
    [[ "$keys" == *,* ]] || die "no rotation in progress (a single key is listed)"
    set_keys "${keys%%,*}"
    restart_and_wait
    echo "ok: the old key is no longer accepted."
}

case "${1:-}" in
    start) start ;;
    finish) finish ;;
    *) die "usage: rotate_master_key.sh <start|finish>" ;;
esac

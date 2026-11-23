#!/usr/bin/env bash
# Kvist Cloud backup. Runs on the production host, from systemd timers (see deploy/systemd).
#
#   backup.sh db     pg_dump of the database           -> borg archive db-<utc>   (hourly)
#   backup.sh data   the data dir (exports, ...)       -> borg archive data-<utc> (daily)
#
# The repository is on the backup host and append-only for this host's key: a hacked production server
# can add archives but cannot delete or overwrite them. The model-key master key is deliberately NOT
# backed up here; keep it somewhere else (docs/BACKUP.md).
set -Eeuo pipefail

# shellcheck source=backup_lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/backup_lib.sh"

KIND="${1:-}"
[[ "$KIND" == db || "$KIND" == data ]] || die "usage: backup.sh <db|data>"

load_backup_env
alert_on_failure
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

backup_db() {
    export_pg_env
    # Global, not local: the EXIT trap runs after this function has returned.
    WORK="$(mktemp -d /var/tmp/kvist-backup.XXXXXX)"
    trap 'rm -rf "$WORK"' EXIT
    # A file first, not a pipe into borg: a pipe would store a truncated dump as a valid archive if pg_dump died.
    pg_dump --format=custom --no-owner --file="$WORK/db.dump"
    pg_restore --list "$WORK/db.dump" >/dev/null
    cd "$WORK"
    borg create --lock-wait 60 "::db-$STAMP" db.dump
}

backup_data() {
    local data rc=0
    data="$(env_value KVIST_CLOUD_DATA_DIR)"
    data="${data:-$CLOUD/.cloud-data}"
    [[ -d "$data" ]] || die "data dir not found: $data"
    cd "$data"
    # Exports are written while this runs; borg's exit code 1 means "a file changed", which is not a failure.
    borg create --lock-wait 60 --compression zstd,3 --exclude '*.env' "::data-$STAMP" . || rc=$?
    (( rc <= 1 )) || die "borg create failed with exit code $rc"
}

"backup_$KIND"
echo "ok: $KIND-$STAMP"
# A quiet tick for every backup that went through; a failure says what broke instead (alert).
telegram "✅"

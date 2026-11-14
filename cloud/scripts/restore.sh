#!/usr/bin/env bash
# Kvist Cloud restore and restore test. Needs the backup settings (KVIST_BACKUP_ENV, default /etc/kvist-cloud/backup.env).
#
#   restore.sh list                          archives, oldest first
#   restore.sh verify [db-archive]           restore into a throw-away database and check it (newest if omitted)
#   restore.sh db <db-archive> <database>    restore into an existing EMPTY database (disaster recovery)
#   restore.sh data <data-archive> <dir>     extract the data dir into an empty directory
#
# verify and db use the local PostgreSQL; the connection comes from the PG* variables, or from DATABASE_URL
# of the server's env file when those are not set (db only).
set -Eeuo pipefail

# shellcheck source=backup_lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/backup_lib.sh"

usage() {
    sed -n '2,11p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2
    exit 2
}

valid_archive() { [[ "$1" =~ ^(db|data)-[0-9]{8}T[0-9]{6}Z$ ]] || die "not an archive name of ours: $1"; }

newest_db_archive() {
    local name
    name="$(borg list --last 1 --glob-archives 'db-*' --short)"
    [[ -n "$name" ]] || die "the repository has no database archive"
    printf '%s' "$name"
}

cmd_list() { borg list --short; }

cmd_verify() {
    local archive="${1:-}" count
    [[ -n "$archive" ]] || archive="$(newest_db_archive)"
    valid_archive "$archive"
    # Global, not local: the EXIT trap runs after this function has returned.
    TMPDB="kvist_verify_$$"
    createdb "$TMPDB"
    trap 'dropdb --if-exists "$TMPDB"' EXIT

    borg extract --stdout "::$archive" db.dump | pg_restore --no-owner --exit-on-error --dbname="$TMPDB"

    count="$(psql -tA -d "$TMPDB" -c "SELECT count(*) FROM alembic_version")"
    [[ "$count" == 1 ]] || die "alembic_version has $count rows, expected 1"
    # The ledger's append-only guard is what makes the hours trustworthy; a dump that lost it is not a good backup.
    count="$(psql -tA -d "$TMPDB" -c "SELECT count(*) FROM pg_trigger WHERE tgname = 'ledger_no_change' AND NOT tgisinternal")"
    [[ "$count" == 1 ]] || die "the ledger_no_change trigger is missing from $archive"

    echo "ok: $archive restored and checked:" \
        "$(psql -tA -d "$TMPDB" -c "SELECT count(*) FROM users") users," \
        "$(psql -tA -d "$TMPDB" -c "SELECT count(*) FROM ledger_entries") ledger entries"
}

cmd_db() {
    [[ $# -eq 2 ]] || usage
    valid_archive "$1"
    [[ "$1" == db-* ]] || die "$1 is not a database archive"
    if [[ -z "${PGHOST:-}${PGUSER:-}" ]]; then export_pg_env; fi
    borg extract --stdout "::$1" db.dump | pg_restore --no-owner --exit-on-error --dbname="$2"
    echo "ok: $1 restored into $2"
}

cmd_data() {
    [[ $# -eq 2 ]] || usage
    valid_archive "$1"
    [[ "$1" == data-* ]] || die "$1 is not a data archive"
    mkdir -p "$2"
    [[ -z "$(ls -A "$2")" ]] || die "$2 is not empty"
    cd "$2"
    borg extract "::$1"
    echo "ok: $1 extracted into $2"
}

COMMAND="${1:-}"
[[ -n "$COMMAND" ]] || usage
shift
load_backup_env
alert_on_failure
case "$COMMAND" in
    list) cmd_list ;;
    verify) cmd_verify "$@" ;;
    db) cmd_db "$@" ;;
    data) cmd_data "$@" ;;
    *) usage ;;
esac

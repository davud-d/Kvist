#!/usr/bin/env bash
# Runs on the backup host, as the user that owns the repository (systemd timers, see deploy/systemd).
#
#   backup_maintenance.sh prune   apply the retention and free the space
#   backup_maintenance.sh check   alert when the newest backup is older than it should be
#
# Pruning happens here and not on the production host: production's key is append-only, so only this
# host can delete anything. That is the point of the split.
set -Eeuo pipefail

# shellcheck source=backup_lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/backup_lib.sh"

prune() {
    # Hourly database dumps are kept for a day, then one a day for two weeks, then one a month for half a year.
    borg prune --lock-wait 60 --glob-archives 'db-*' --keep-hourly 24 --keep-daily 14 --keep-monthly 6
    borg prune --lock-wait 60 --glob-archives 'data-*' --keep-daily 14 --keep-monthly 6
    borg compact --lock-wait 60
}

# check_fresh <prefix> <max-age-minutes>: the age comes from the archive's own name, written by backup.sh in UTC.
check_fresh() {
    local prefix="$1" max="$2" name stamp taken age
    name="$(borg list --last 1 --glob-archives "$prefix-*" --short)"
    if [[ -z "$name" ]]; then
        alert "kvist-cloud backup: the repository has no $prefix archive at all"
        return 1
    fi
    stamp="${name#*-}"
    taken="$(date -u -d "${stamp:0:4}-${stamp:4:2}-${stamp:6:2}T${stamp:9:2}:${stamp:11:2}:${stamp:13:2}Z" +%s)"
    age=$(( ($(date -u +%s) - taken) / 60 ))
    if (( age > max )); then
        alert "kvist-cloud backup: the newest $prefix archive is $age minutes old (limit $max)"
        return 1
    fi
    echo "ok: $name is $age minutes old"
}

check() {
    local rc=0
    # An hourly job gets 130 minutes, a daily one 26 hours: one missed run is noise, two is a problem.
    check_fresh db 130 || rc=1
    check_fresh data 1560 || rc=1
    return "$rc"
}

load_backup_env
alert_on_failure
case "${1:-}" in
    prune) prune ;;
    # check_fresh has already alerted with the specific reason; `||` keeps the generic failure alert from repeating it.
    check) check || exit 1 ;;
    *) die "usage: backup_maintenance.sh <prune|check>" ;;
esac

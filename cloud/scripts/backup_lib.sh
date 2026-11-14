# shellcheck shell=bash
# Shared by backup.sh, restore.sh, backup_maintenance.sh and setup_backup_host.sh. Sourced, never run.

LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLOUD="$(dirname "$LIB_DIR")"

die() { echo "error: $*" >&2; exit 1; }

# Loads the backup settings (BORG_REPO, BORG_PASSPHRASE, BORG_RSH, ALERT_URL). The file is ours, so it is sourced;
# the passphrase in it is why it must not be readable by anyone else.
load_backup_env() {
    local file="${KVIST_BACKUP_ENV:-/etc/kvist-cloud/backup.env}"
    [[ -f "$file" ]] || die "$file not found (see docs/BACKUP.md)"
    [[ "$(stat -c %a "$file")" =~ ^[0-7]00$ ]] || die "$file must not be readable by group or others (chmod 600)"
    set -a
    # shellcheck disable=SC1090
    source "$file"
    set +a
    : "${BORG_REPO:?BORG_REPO is not set in $file}"
    : "${BORG_PASSPHRASE:?BORG_PASSPHRASE is not set in $file}"
}

# The server's own settings are read, never sourced: a real environment variable wins, then cloud/.env
# (or the file named by KVIST_CLOUD_ENV_FILE), the same order the server uses.
env_value() {
    local key="$1" file="${KVIST_CLOUD_ENV_FILE:-$CLOUD/.env}"
    if [[ -n "${!key:-}" ]]; then
        printf '%s' "${!key}"
        return 0
    fi
    [[ -f "$file" ]] || return 0
    sed -n "s/^${key}=//p" "$file" | tail -n 1 | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'\$/\1/"
}

# Turns DATABASE_URL into PG* variables so the password never appears in a process's arguments.
export_pg_env() {
    local url
    url="$(env_value DATABASE_URL)"
    [[ "$url" == postgres* ]] || die "DATABASE_URL is not a PostgreSQL address"
    eval "$(DB_URL="$url" python3 - <<'PY'
import os
import shlex
from urllib.parse import parse_qs, unquote, urlsplit

u = urlsplit(os.environ["DB_URL"])
values = {
    "PGHOST": u.hostname,
    "PGPORT": u.port,
    "PGUSER": unquote(u.username or ""),
    "PGPASSWORD": unquote(u.password or ""),
    "PGDATABASE": u.path.lstrip("/"),
    "PGSSLMODE": (parse_qs(u.query).get("sslmode") or [""])[0],
}
for key, value in values.items():
    if value:
        print(f"export {key}={shlex.quote(str(value))}")
PY
)"
}

# Writes to stderr (the journal) and, when ALERT_URL is set, POSTs the text there (an ntfy-style endpoint).
alert() {
    echo "ALERT: $*" >&2
    [[ -n "${ALERT_URL:-}" ]] || return 0
    curl -fsS -m 10 -d "$*" "$ALERT_URL" >/dev/null || echo "warning: the alert could not be delivered to ALERT_URL" >&2
}

# Needs `set -E` in the caller so the trap also covers functions.
alert_on_failure() {
    # shellcheck disable=SC2064
    trap 'alert "kvist-cloud backup: $(basename "$0") failed at line $LINENO"' ERR
}

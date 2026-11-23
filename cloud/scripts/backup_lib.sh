# shellcheck shell=bash
# Shared by backup.sh, restore.sh, backup_maintenance.sh and setup_backup_host.sh. Sourced, never run.

LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLOUD="$(dirname "$LIB_DIR")"

# Once alert_on_failure is on, a deliberate stop is a failure to report too: the ERR trap does not see `exit 1`.
die() {
    echo "error: $*" >&2
    [[ -z "${ALERTING:-}" ]] || alert "$(basename "$0"): $*"
    exit 1
}

# Loads the backup settings (BORG_REPO, BORG_PASSPHRASE, BORG_RSH, ALERT_URL, TELEGRAM_BOT_TOKEN,
# TELEGRAM_CHAT_ID, TELEGRAM_LABEL). The file is ours, so it is sourced;
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

# Sends one message to the Telegram chat when TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set. The token goes to
# curl on stdin, not on its command line, where any process on the host could read it. A message that cannot be
# delivered is said in the journal and never fails the backup itself.
telegram() {
    [[ -n "${TELEGRAM_BOT_TOKEN:-}" && -n "${TELEGRAM_CHAT_ID:-}" ]] || return 0
    printf 'url = "https://api.telegram.org/bot%s/sendMessage"\n' "$TELEGRAM_BOT_TOKEN" |
        curl -fsS -m 10 -K - --data-urlencode "chat_id=$TELEGRAM_CHAT_ID" --data-urlencode "text=$1" >/dev/null ||
        echo "warning: the Telegram message could not be delivered" >&2
}

# Writes to stderr (the journal) and, when set, to ALERT_URL (an ntfy-style endpoint) and to Telegram.
alert() {
    echo "ALERT: $*" >&2
    # The label says which side broke ("prod", "backup"); the machines' own names say little.
    telegram "❌ ${TELEGRAM_LABEL:-$(hostname)}: $*"
    [[ -n "${ALERT_URL:-}" ]] || return 0
    curl -fsS -m 10 -d "$*" "$ALERT_URL" >/dev/null || echo "warning: the alert could not be delivered to ALERT_URL" >&2
}

# Needs `set -E` in the caller so the trap also covers functions.
alert_on_failure() {
    ALERTING=1
    # shellcheck disable=SC2064
    trap 'alert "kvist-cloud backup: $(basename "$0") failed at line $LINENO"' ERR
}

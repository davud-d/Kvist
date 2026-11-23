#!/usr/bin/env bash
# Sets up the backup host once. Debian 12, as root, from a checkout (or a copy) of cloud/:
#
#   [PG_MAJOR=17] scripts/setup_backup_host.sh "<contents of the production host's backup_key.pub>"
#
# PG_MAJOR must equal production's PostgreSQL major version when that is not Debian 12's own 15.
#
# Installs borg and PostgreSQL (the weekly restore test needs a server), makes the user "borg", creates the
# repository, lets production's key ADD archives only, and installs the maintenance timers.
# Re-running is safe: it changes nothing that exists, except that it rewrites the authorized key.
set -Eeuo pipefail

# shellcheck source=backup_lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/backup_lib.sh"

PUBKEY="${1:-}"
[[ "$PUBKEY" == ssh-ed25519\ * ]] || die 'usage: setup_backup_host.sh "ssh-ed25519 AAAA... comment"'
[[ $EUID -eq 0 ]] || die "run as root"
# shellcheck disable=SC1091
[[ "$(. /etc/os-release && echo "${VERSION_ID:-}")" == 12 ]] || die "this script is written for Debian 12"

HOME_DIR=/var/lib/kvist-backup
REPO="$HOME_DIR/repo"
ENV_FILE=/etc/kvist-cloud/backup-host.env
LIB_TARGET=/usr/local/lib/kvist-backup
UNITS="$CLOUD/deploy/systemd"

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq borgbackup curl ca-certificates openssl openssh-server
if [[ -n "${PG_MAJOR:-}" ]]; then
    # Production on another Debian (13 ships PostgreSQL 17) dumps with its own major version, and a dump only
    # restores on the same or a newer server: take that major from the PostgreSQL project's repository.
    install -d /usr/share/postgresql-common/pgdg
    curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc https://www.postgresql.org/media/keys/ACCC4CF8.asc
    echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list
    apt-get update -qq
    apt-get install -y -qq "postgresql-$PG_MAJOR" "postgresql-client-$PG_MAJOR"
else
    apt-get install -y -qq postgresql postgresql-client
fi

id borg >/dev/null 2>&1 || adduser --system --group --home "$HOME_DIR" --shell /bin/bash borg
install -d -o borg -g borg -m 700 "$HOME_DIR/.ssh" "$REPO"
# Append-only and confined to the repository: this key cannot delete, rewrite or read anything else.
printf 'command="borg serve --append-only --restrict-to-path %s",restrict %s\n' "$REPO" "$PUBKEY" > "$HOME_DIR/.ssh/authorized_keys"
chown borg:borg "$HOME_DIR/.ssh/authorized_keys"
chmod 600 "$HOME_DIR/.ssh/authorized_keys"

# The weekly restore test makes a database as the user "borg" (peer authentication over the local socket).
if ! runuser -u postgres -- psql -tA -c "SELECT 1 FROM pg_roles WHERE rolname = 'borg'" | grep -q 1; then
    runuser -u postgres -- createuser --createdb borg
fi

if [[ ! -f "$ENV_FILE" ]]; then
    install -d -m 755 "$(dirname "$ENV_FILE")"
    (
        umask 077
        {
            echo "BORG_REPO=$REPO"
            echo "BORG_PASSPHRASE=$(openssl rand -base64 32)"
            echo "# ALERT_URL=https://ntfy.sh/<a-long-random-topic>"
        } > "$ENV_FILE"
    )
    chown borg:borg "$ENV_FILE"
fi

if [[ ! -f "$REPO/config" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
    # --preserve-environment keeps root's HOME, which borg (running as "borg") cannot write its cache into.
    runuser -u borg --preserve-environment -- env HOME="$HOME_DIR" borg init --encryption=repokey-blake2
fi

install -d "$LIB_TARGET"
install -m 755 "$LIB_DIR/backup_lib.sh" "$LIB_DIR/restore.sh" "$LIB_DIR/backup_maintenance.sh" "$LIB_TARGET/"
install -m 644 "$UNITS"/kvist-backup-*.service "$UNITS"/kvist-backup-*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now kvist-backup-prune.timer

echo
echo "Backup host is ready."
echo "SSH host key to compare when production first connects:"
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
echo
echo "Next:"
echo "  1. Copy BORG_PASSPHRASE from $ENV_FILE into production's /etc/kvist-cloud/backup.env"
echo "     (and into your password manager: without it the backups cannot be opened)."
echo "     Production's BORG_REPO is: ssh://borg@<this host>$REPO"
echo "  2. Run the first backup on production, then enable the freshness and restore-test timers here:"
echo "       systemctl enable --now kvist-backup-check.timer kvist-backup-verify.timer"
echo "     (they are not enabled yet: before the first backup they would only raise a false alarm)."
echo "  3. Optionally set ALERT_URL, or TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID (+ TELEGRAM_LABEL), in $ENV_FILE."

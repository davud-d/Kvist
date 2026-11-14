# Kvist Cloud backups

*(Русская версия: [BACKUP.ru.md](BACKUP.ru.md))*

PostgreSQL on Debian 12. Two machines: the **production host** (the server) and a small **backup host** at a
*different provider* (disk matters, not memory: 1–2 GB of RAM and 100–200 GB of disk is plenty).

```
production host                                   backup host
  backup.sh db    (hourly)   ── ssh, borg ──►      repository (append-only for production's key)
  backup.sh data  (daily)                          backup_maintenance.sh prune   (daily: retention, frees space)
                                                   backup_maintenance.sh check   (every 30 min: alert if stale)
                                                   restore.sh verify             (weekly: restore into a scratch DB)
```

**Append-only is the point.** Production's key can add archives and read them, but cannot delete or overwrite
anything. A hacked server, or a bad script, cannot erase the history. Deleting old archives (retention) is done
only by the backup host itself.

What is kept: hourly database dumps for a day, then one a day for 14 days, then one a month for 6 months; the data dir
(exports) daily with the same 14 days / 6 months. What is **not** backed up, on purpose: `*.env` files and the master
key (`KVIST_CLOUD_MASTER_KEY`).

## The master key lives elsewhere

Model keys in the database are encrypted with the master key. Without it the backup restores, but every customer's
model key is unreadable; with it stored next to the backups, a leaked backup would open them. Keep the master key
(and the rest of `.env`) in a password manager and on paper, **not** on these two servers' backups.

## Set up the backup host (once)

1. Rent a small Debian 12 VPS at a provider other than the production host's. Copy the `cloud/scripts` and
   `cloud/deploy` folders to it.
2. On production, make the key (step 1 of the next section) and take the contents of `backup_key.pub`.
3. On the backup host, as root:

   ```bash
   scripts/setup_backup_host.sh "ssh-ed25519 AAAA… kvist-cloud-backup"
   ```

   It installs borg and PostgreSQL, creates the user `borg` and the repository, makes the passphrase in
   `/etc/kvist-cloud/backup-host.env` (0600) and enables the daily retention timer. At the end it prints the SSH
   host key's fingerprint and what to do next.
4. Copy `BORG_PASSPHRASE` into production's `backup.env` **and into your password manager**. Without the passphrase
   the backups cannot be opened.

## Set up production (once)

```bash
sudo apt install borgbackup postgresql-client curl
sudo install -d -m 700 /etc/kvist-cloud
# 1. the key; give backup_key.pub to setup_backup_host.sh
sudo ssh-keygen -t ed25519 -N '' -C kvist-cloud-backup -f /etc/kvist-cloud/backup_key
# 2. trust the backup host: compare the fingerprint with the one setup_backup_host.sh printed
ssh-keyscan -t ed25519 <backup-host> | tee /tmp/hk | ssh-keygen -lf - && sudo install -D -m 644 /tmp/hk /root/.ssh/known_hosts
```

`/etc/kvist-cloud/backup.env` (`chmod 600`, the scripts refuse a looser file):

```
BORG_REPO=ssh://borg@<backup-host>/var/lib/kvist-backup/repo
BORG_PASSPHRASE=<from the backup host>
BORG_RSH='ssh -i /etc/kvist-cloud/backup_key -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes'
```

The file is read with `source`, so a value with spaces needs the quotes.

Then install the timers (the units expect the release layout of [DEPLOY.md](DEPLOY.md): the scripts under `/opt/kvist-cloud/current`, the settings in `/etc/kvist-cloud/cloud.env`):

```bash
sudo cp deploy/systemd/kvist-cloud-backup* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start kvist-cloud-backup@db.service && journalctl -u kvist-cloud-backup@db.service -n 20
sudo systemctl enable --now kvist-cloud-backup-db.timer kvist-cloud-backup-data.timer
```

The scripts read `DATABASE_URL` and `KVIST_CLOUD_DATA_DIR` from the same env file the server uses
(`/etc/kvist-cloud/cloud.env`, set by the unit through `KVIST_CLOUD_ENV_FILE`).

After the first backup succeeded, on the **backup host**:

```bash
systemctl enable --now kvist-backup-check.timer kvist-backup-verify.timer
```

For alerts, put `ALERT_URL=…` (a POST endpoint, e.g. an ntfy topic) in both `backup.env` files. Without it,
failures only reach the journal; with systemd, `systemctl --failed` shows them too.

## Restore

`restore.sh` reads the same `KVIST_BACKUP_ENV` file (default `/etc/kvist-cloud/backup.env`).

```bash
scripts/restore.sh list                                   # archives, oldest first
scripts/restore.sh verify                                 # newest dump into a scratch database, checked and dropped
scripts/restore.sh db   db-20261114T020000Z kvist         # into an existing EMPTY database
scripts/restore.sh data data-20261114T022000Z /srv/data   # into an empty directory
```

New server after a disaster: install Debian 12 and PostgreSQL 15, create an empty database, put the saved `.env`
(with the master key) back, run `restore.sh db` and `restore.sh data`, start the server. The migrations are already
applied inside the dump; the server checks them on start.

`verify` checks that the dump restores, that `alembic_version` has exactly one row and that the ledger's
`ledger_no_change` trigger survived. It does not check the *contents* against the live database.

## Things to know

- **PostgreSQL major versions must match** between the production client tools and wherever you restore
  (Debian 12 gives 15 on both). A dump made by a newer `pg_dump` may not restore on an older server. When
  production is on Debian 13 (PostgreSQL 17), run the backup host's setup as `PG_MAJOR=17 scripts/setup_backup_host.sh …`
  and it installs that major from the PostgreSQL project's repository.
- **The backup host holds the passphrase.** Pruning and the restore test need to open the repository, so it can
  read the data. Secrets in the database (model keys, tokens) are encrypted with the master key it does not have,
  but emails and usage are readable there: treat that host like production.
- **Test it yourself once.** Run `restore.sh verify` by hand, then a full `restore.sh db` on a spare machine, and
  only then trust the timers.

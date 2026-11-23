# Бэкапы Kvist Cloud

*(English version: [BACKUP.md](BACKUP.md))*

PostgreSQL на Debian 12. Две машины: **прод** (сам сервер) и небольшой **бэкап-сервер** у *другого
провайдера* (важен диск, а не память: 1–2 ГБ RAM и 100–200 ГБ диска более чем достаточно).

```
прод                                              бэкап-сервер
  backup.sh db    (каждый час)  ── ssh, borg ──►   репозиторий (для ключа прода только дозапись)
  backup.sh data  (раз в сутки)                    backup_maintenance.sh prune   (раз в сутки: хранение, место)
                                                   backup_maintenance.sh check   (каждые 30 мин: алерт, если бэкап устарел)
                                                   restore.sh verify             (раз в неделю: восстановление во временную БД)
```

**Весь смысл в append-only.** Ключ прода может добавлять архивы и читать их, но не может ничего удалить или
перезаписать. Взломанный сервер или кривой скрипт не сотрут историю. Старые архивы удаляет только сам
бэкап-сервер.

Что хранится: почасовые дампы БД за сутки, потом по одному в день за 14 дней, потом по одному в месяц за 6 месяцев;
каталог данных (экспорты) раз в сутки, с теми же 14 днями и 6 месяцами. Что **намеренно не копируется**: файлы
`*.env` и мастер-ключ (`KVIST_CLOUD_MASTER_KEY`).

## Мастер-ключ хранится отдельно

Ключи моделей в базе зашифрованы мастер-ключом. Без него бэкап восстановится, но ключи моделей всех клиентов
будут нечитаемы; а если хранить его рядом с бэкапами, утечка бэкапа раскроет их. Держи мастер-ключ (и остальной
`.env`) в менеджере паролей и на бумаге, **не** в бэкапах этих двух серверов.

## Настройка бэкап-сервера (один раз)

1. Возьми небольшой VPS с Debian 12 у провайдера, отличного от прода. Скопируй на него папки `cloud/scripts` и
   `cloud/deploy`.
2. На проде сделай ключ (шаг 1 следующего раздела) и возьми содержимое `backup_key.pub`.
3. На бэкап-сервере от root:

   ```bash
   scripts/setup_backup_host.sh "ssh-ed25519 AAAA… kvist-cloud-backup"
   ```

   Скрипт ставит borg и PostgreSQL, создаёт пользователя `borg` и репозиторий, генерирует пароль в
   `/etc/kvist-cloud/backup-host.env` (0600) и включает ежедневный таймер хранения. В конце он печатает
   отпечаток SSH-ключа сервера и список следующих шагов.
4. Скопируй `BORG_PASSPHRASE` в `backup.env` на проде **и в менеджер паролей**. Без пароля бэкапы не открыть.

## Настройка прода (один раз)

```bash
sudo apt install borgbackup postgresql-client curl
sudo install -d -m 700 /etc/kvist-cloud
# 1. ключ; backup_key.pub отдай в setup_backup_host.sh
sudo ssh-keygen -t ed25519 -N '' -C kvist-cloud-backup -f /etc/kvist-cloud/backup_key
# 2. доверие к бэкап-серверу: сверь отпечаток с тем, что напечатал setup_backup_host.sh
ssh-keyscan -t ed25519 <backup-host> | tee /tmp/hk | ssh-keygen -lf - && sudo install -D -m 644 /tmp/hk /root/.ssh/known_hosts
```

`/etc/kvist-cloud/backup.env` (`chmod 600`, скрипты откажутся работать с более открытым файлом):

```
BORG_REPO=ssh://borg@<backup-host>/var/lib/kvist-backup/repo
BORG_PASSPHRASE=<с бэкап-сервера>
BORG_RSH='ssh -i /etc/kvist-cloud/backup_key -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes'
```

Файл читается через `source`, поэтому значение с пробелами нужно брать в кавычки.

Дальше таймеры (юниты рассчитывают на раскладку релизов из [DEPLOY.ru.md](DEPLOY.ru.md): скрипты в `/opt/kvist-cloud/current`, настройки в `/etc/kvist-cloud/cloud.env`):

```bash
sudo cp deploy/systemd/kvist-cloud-backup* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start kvist-cloud-backup@db.service && journalctl -u kvist-cloud-backup@db.service -n 20
sudo systemctl enable --now kvist-cloud-backup-db.timer kvist-cloud-backup-data.timer
```

Скрипты берут `DATABASE_URL` и `KVIST_CLOUD_DATA_DIR` из того же env-файла, что и сервер
(`/etc/kvist-cloud/cloud.env`, его задаёт юнит через `KVIST_CLOUD_ENV_FILE`).

После первого успешного бэкапа, на **бэкап-сервере**:

```bash
systemctl enable --now kvist-backup-check.timer kvist-backup-verify.timer
```

Для оповещений впиши `ALERT_URL=…` (адрес для POST, например тема ntfy) в оба файла `backup.env`. Без него сбои
попадают только в журнал; в systemd их показывает и `systemctl --failed`.

Telegram: впиши `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` и короткую метку `TELEGRAM_LABEL` («prod», «backup») в оба
файла (`backup.env` на продакшене, `backup-host.env` на бэкап-сервере). Каждый прошедший бэкап присылает «✅»;
сбой — «❌ <метка>: что сломалось», и так же бэкап-сервер, когда последний архив слишком старый. Токен живёт только
в этих файлах, не в репозитории; curl получает его через stdin, поэтому в списке процессов его не видно.

## Восстановление

`restore.sh` читает тот же файл `KVIST_BACKUP_ENV` (по умолчанию `/etc/kvist-cloud/backup.env`).

```bash
scripts/restore.sh list                                   # архивы, старые первыми
scripts/restore.sh verify                                 # свежий дамп во временную БД, проверка, удаление
scripts/restore.sh db   db-20261114T020000Z kvist         # в существующую ПУСТУЮ базу
scripts/restore.sh data data-20261114T022000Z /srv/data   # в пустой каталог
```

Новый сервер после аварии: поставь Debian 12 и PostgreSQL 15, создай пустую базу, верни сохранённый `.env`
(с мастер-ключом), выполни `restore.sh db` и `restore.sh data`, запусти сервер. Миграции уже применены внутри
дампа; сервер проверяет их при старте.

`verify` проверяет, что дамп восстанавливается, что в `alembic_version` ровно одна строка и что триггер
`ledger_no_change` на месте. Содержимое с живой базой он не сравнивает.

## Что нужно знать

- **Мажорные версии PostgreSQL должны совпадать** у клиентских утилит на проде и там, где восстанавливаешь
  (в Debian 12 на обоих 15). Дамп новым `pg_dump` может не восстановиться на старом сервере. Если прод на
  Debian 13 (PostgreSQL 17), запускай настройку бэкап-сервера как `PG_MAJOR=17 scripts/setup_backup_host.sh …`:
  скрипт поставит эту версию из репозитория проекта PostgreSQL.
- **Пароль хранится на бэкап-сервере.** Чистке и проверке восстановления нужно открывать репозиторий, значит,
  он может читать данные. Секреты в базе (ключи моделей, токены) зашифрованы мастер-ключом, которого там нет, но
  почта и статистика читаются: относись к этому серверу как к проду.
- **Проверь сам один раз.** Запусти `restore.sh verify` вручную, затем полный `restore.sh db` на запасной машине,
  и только после этого доверяй таймерам.

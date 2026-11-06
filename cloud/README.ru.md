# Kvist Cloud

*(English version: [README.md](README.md))*

Подписка, где у каждого клиента своя облачная машина с Kvist. Клиент вставляет свой ключ модели,
пишет задачу, агент выполняет её на машине. Между задачами машина спит; клиент платит за часы, пока
агент работает.

В этой папке MVP: сервер управления, раннер, который живёт на каждой машине, и веб-фронтенд. Всё
работает на одном компьютере; для запуска на настоящем хостинге остаётся написать один адаптер
провайдера (`backend/kvist_cloud/providers/sprites.py`). Устройство описано в
[docs/ARCHITECTURE.ru.md](docs/ARCHITECTURE.ru.md).

## Запуск локально

```bash
./cloud/scripts/dev.sh
```

Затем открой <http://127.0.0.1:8740>. Скрипт создаёт `cloud/.venv`, собирает фронтенд, один раз
генерирует локальные секреты (в `cloud/.cloud-data/dev-secrets.env`, права 0600, никогда не
печатаются), запускает фейковую модель на порту 8799 и сервер на порту 8740.

* **Вход:** кнопка GitHub входит локальным пользователем `dev` (`AUTH_DEV_LOGIN`, только на
  loopback).
* **Карта:** биллинг выключен, поэтому шаг карты — заглушка: нажми «Пропустить (dev)».
* **Модель:** выбери «Другой», base URL `http://127.0.0.1:8799/v1`, модель `fake-model`, любой
  ключ от 8 символов. Фейковая модель проигрывает короткий сценарий через **настоящее ядро Kvist**:
  записывает `CLOUD_NOTES.md`, читает его и отвечает. Ничего не уходит с компьютера.
* **Админка:** <http://127.0.0.1:8740/admin> показывает конверсию воронки и открытые споры.

`./cloud/scripts/dev.sh --fake-kvist` вместо ядра использует сценарную заглушку Kvist
(`runner/tests/fake_kvist.py`) — чтобы работать над интерфейсом без окружения ядра.

Настоящее ядро запускается из собственного `.venv` репозитория (`python -m kernel`). Если его нет:

```bash
python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt
```

Нужны Python 3.11+, Node 20+, git. Для лимитов CPU и памяти машины используется
`systemd-run --user`, если он работает; без него раннер пишет предупреждение в лог и работает без
лимитов.

## Устройство папки

```
backend/kvist_cloud/   сервер управления (FastAPI, SQLAlchemy 2, Alembic)
  main.py              сборка приложения        config.py   настройки, только из окружения
  auth.py              GitHub OAuth, cookie     secrets.py  хранилище Fernet, чистка логов
  keys.py              ключи моделей, проверка  machines.py машины, фоновое создание, сон
  gateway.py           WebSocket раннеров       tasks.py    API задач, статус из событий Kvist
  events.py            живая рассылка           ledger.py   журнал часов, лента, споры
  limits.py            очередь, конец часов     billing.py  вебхуки Paddle (по умолчанию выкл.)
  funnel.py            шаги воронки, конверсия  exports.py  экспорт архива
  web.py               собранный фронтенд, CSP, gzip        providers/  base, local, sprites
backend/migrations/    Alembic; здесь же триггеры журнала против UPDATE/DELETE
runner/kvist_runner/   процесс на машине: WebSocket, heartbeat, свой Kvist на задачу
frontend/              ванильный JS + esbuild; build.mjs проверяет локали, контраст и бюджеты
pixel/                 пиксельное окно (копия Kvist Pixel) на облаке: отдаётся по /pixel/, см. pixel/README.ru.md
scripts/dev.sh         локальный запуск одной командой   scripts/fake_model.py  фейковая модель
```

## Настройка

Только переменные окружения. Обязательные:

| Переменная | Что это |
| --- | --- |
| `KVIST_CLOUD_SESSION_SECRET` | подпись cookie сессий, от 32 символов |
| `KVIST_CLOUD_MASTER_KEY` | ключ(и) Fernet для ключей моделей в базе; через запятую для ротации, первый шифрует |
| `PUBLIC_URL` | собственный адрес сервера (`https://…` включает secure-cookie) |

Необязательные: `DATABASE_URL` (по умолчанию SQLite в `KVIST_CLOUD_DATA_DIR`), `GITHUB_CLIENT_ID` /
`GITHUB_CLIENT_SECRET`, `BILLING_ENABLED` вместе с `PADDLE_WEBHOOK_SECRET`, `PADDLE_API_KEY`,
`PADDLE_CLIENT_TOKEN`, `PADDLE_ENVIRONMENT`, `PADDLE_PRICE_TRIAL|START|PRO|MAX|HOURS`;
`MACHINE_PROVIDER` (`local`), `KVIST_COMMAND`, `RUNNER_COMMAND`, `HEARTBEAT_SECONDS` (15),
`HEARTBEAT_MAX_GAP_SECONDS` (45), `MACHINE_IDLE_SLEEP_SECONDS` (900), `PROVIDER_OUTAGE_SECONDS`
(300), `HOURS_GRACE_SECONDS` (300), `ADMIN_GITHUB_LOGINS` и только для разработки
`AUTH_DEV_LOGIN`, `ALLOW_LOCAL_ENDPOINTS` (обе запрещены, если `PUBLIC_URL` не loopback).

Для Paddle нужна отдельная цена `PADDLE_PRICE_TRIAL`: Start за $15 в месяц с 7-дневным
**платным пробным периодом за $1** и обязательной платёжной картой
(`trial_period.unit_price.amount="100"`, валюта `USD`). Обычные Start/Pro/Max — без trial.
Все цены — USD, с включённым налогом (`tax_mode=internal`), без региональных переопределений,
количество 1, суммы из `plans.py`. Перед созданием checkout backend проверяет настройки цены.
Для выдачи доступа требуется списание точной суммы; скидки и кредиты не подходят.
Браузер получает только серверный `transactionId`; `custom_data`, событие успешной оплаты
в браузере и поля профиля не меняют тариф или баланс.

Адрес вебхука: `<PUBLIC_URL>/billing/paddle/webhook`. Нужны события `transaction.completed`,
`subscription.*`, `adjustment.created`, `adjustment.updated`. API-ключу нужны права создания
транзакций, чтения цен и подписок, отмены подписок, чтения и создания adjustments.
Trial открывается после фактического списания $1 и **одобренного полного возврата**.
Авторизации карты недостаточно. Одобрение возврата и зачисление банком могут занимать время;
отклонённый возврат требует вмешательства оператора. Обычные возвраты и chargeback отзывают
купленные часы и доступ к возвращённому периоду подписки. Отмена chargeback не восстанавливает
доступ автоматически: нужна сверка оператором.

Возраст берётся из `created_at`, полученного от GitHub после OAuth: аккаунт должен быть строго
старше календарного месяца. Trial доступен один раз; неизвестный возраст запрещает его до
повторного входа. Dev-оплата, бесплатные часы и искусственные продления работают только при
`AUTH_DEV_LOGIN=true`, выключенном биллинге и loopback `PUBLIC_URL`.
`MACHINE_PROVIDER=local` ограничен loopback: он **не изолирует** недоверенных пользователей.
Адаптер Sprites ещё не реализован, поэтому публичный хостинг пока не готов.

OAuth запрашивает `read:user repo`. Для приватных репозиториев существующим пользователям
нужно переподключить GitHub и разрешить `repo`, а при необходимости — доступ организации.
Scope `repo` широкий. Токен зашифрован в базе; при клонировании передаётся через Git-заголовок,
ограниченный github.com, не попадает в URL remote или окружение агента. Принимаются только
GitHub HTTPS URL; локальные file URL разрешены лишь в dev-режиме. Авторизованные fetch/push
из агента эта интеграция клонирования не предоставляет; экспорт архива сохранён.

Миграция `0005` сохраняет факт использования старых trial и приостанавливает старые Paddle-подписки.
Подписки, созданные
через доверие к клиентскому `custom_data`, перед продлением требуют сверки оператором с Paddle
и привязки к серверным checkout/payment-записям. Нельзя восстанавливать владельца из `custom_data`.
Перед live-оплатой нужно пройти списание, возврат и продление в Paddle sandbox: локальные тесты
используют заглушки и не проверяют настройки вашего merchant-аккаунта.

Контракты провайдеров: [платный trial](https://developer.paddle.com/build/trials/create-trial/),
[возвраты](https://developer.paddle.com/build/transactions/create-transaction-adjustments/),
[GitHub OAuth scopes](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps).

## Проверки

```bash
cd cloud/backend  && ../.venv/bin/python -m pytest && ../.venv/bin/ruff check .
cd cloud/runner   && ../.venv/bin/python -m pytest && ../.venv/bin/ruff check .
cd cloud/frontend && npm test && npm run build
node --test "cloud/pixel/tests/*.test.mjs"      # логика страницы пиксельного окна
./cloud/frontend/tools/lighthouse.sh          # при запущенном dev.sh; METHOD=simulate — модель Lighthouse
```

`backend/tests/test_e2e_local.py` — сквозной тест: настоящий сервер на настоящем порту,
local-провайдер с настоящим раннером, воронка по HTTP, поток задачи через браузерный WebSocket,
проверка списания и воронки. Его вариант `real_core` проходит то же через настоящее ядро Kvist;
без окружения ядра отключи его через `-m "not real_core"`.

## Лицензия

GNU AGPL v3 или новее, см. [LICENSE](LICENSE) и [LICENSE-NOTIFY.md](LICENSE-NOTIFY.md).
Шрифт Kvist Mono (подмножество JetBrains Mono) — под SIL OFL, `frontend/src/fonts/OFL.txt`.

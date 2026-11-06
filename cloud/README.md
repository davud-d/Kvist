# Kvist Cloud

*(Русская версия: [README.ru.md](README.ru.md))*

A subscription where every customer gets a cloud machine running Kvist. The customer pastes their
own model key, writes a task, and the agent does it on the machine. The machine sleeps between
tasks; the customer pays for the hours the agent works.

This folder is the MVP: the control server, the runner that lives on each machine, and the web
frontend. Everything runs on one computer; going live means writing one provider adapter
(`backend/kvist_cloud/providers/sprites.py`). The design is in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Run it locally

```bash
./cloud/scripts/dev.sh
```

Then open <http://127.0.0.1:8740>. The script creates `cloud/.venv`, builds the frontend, makes
local secrets once (in `cloud/.cloud-data/dev-secrets.env`, mode 0600, never printed), starts a
fake model on port 8799 and the server on port 8740.

* **Sign in:** the GitHub button signs you in as the local user `dev` (`AUTH_DEV_LOGIN`, loopback
  only).
* **Card:** billing is off, so the card step is a stub: press "Skip (dev)".
* **Model:** pick "Other", base URL `http://127.0.0.1:8799/v1`, model `fake-model`, any key of
  8+ characters. The fake model plays a short script through the **real Kvist core**: it writes
  `CLOUD_NOTES.md`, reads it back and answers. Nothing leaves the computer.
* **Admin:** <http://127.0.0.1:8740/admin> shows funnel conversion and open disputes.

`./cloud/scripts/dev.sh --fake-kvist` uses a scripted stand-in for Kvist instead of the core
(`runner/tests/fake_kvist.py`), for working on the UI without the core's environment.

The real core runs from the repository's own `.venv` (`python -m kernel`). If it is missing:

```bash
python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt
```

Needs Python 3.11+, Node 20+, git. `systemd-run --user` is used for the machine's CPU and
memory limits when it works; without it the runner logs a warning and runs without them.

## Layout

```
backend/kvist_cloud/   the control server (FastAPI, SQLAlchemy 2, Alembic)
  main.py              app assembly          config.py   settings, from the environment only
  auth.py              GitHub OAuth, cookies secrets.py  Fernet vault, log scrubbing
  keys.py              model keys, key check machines.py machines, background creation, idle sleep
  gateway.py           runners' WebSocket    tasks.py    tasks API, status from Kvist's events
  events.py            live fan-out          ledger.py   append-only hours ledger, feed, disputes
  limits.py            queue, hours running out          billing.py  Paddle webhooks (off by default)
  funnel.py            funnel steps, conversion          exports.py  archive export
  web.py               the built frontend, CSP, gzip     providers/  base, local, sprites (stub)
backend/migrations/    Alembic; the ledger's no-UPDATE/no-DELETE triggers live here
runner/kvist_runner/   the process on a machine: WebSocket, heartbeat, one Kvist per task
frontend/              vanilla JS + esbuild; build.mjs checks locales, contrast and budgets
pixel/                 the pixel-game window (a copy of Kvist Pixel) on the cloud: served at /pixel/, see pixel/README.md
scripts/dev.sh         the one-command local run    scripts/fake_model.py  the fake model
```

## Configuration

Environment variables only. The required ones:

| Variable | Meaning |
| --- | --- |
| `KVIST_CLOUD_SESSION_SECRET` | signs session cookies, 32+ characters |
| `KVIST_CLOUD_MASTER_KEY` | Fernet key(s) for model keys at rest; comma-separated to rotate, the first encrypts |
| `PUBLIC_URL` | the server's own address (`https://…` turns on secure cookies) |

Optional: `DATABASE_URL` (default SQLite in `KVIST_CLOUD_DATA_DIR`), `GITHUB_CLIENT_ID` /
`GITHUB_CLIENT_SECRET`, `BILLING_ENABLED` with `PADDLE_WEBHOOK_SECRET`, `PADDLE_API_KEY`,
`PADDLE_CLIENT_TOKEN`, `PADDLE_ENVIRONMENT`, `PADDLE_PRICE_START|PRO|MAX|HOURS`;
`MACHINE_PROVIDER` (`local`), `KVIST_COMMAND`, `RUNNER_COMMAND`, `HEARTBEAT_SECONDS` (15),
`HEARTBEAT_MAX_GAP_SECONDS` (45), `MACHINE_IDLE_SLEEP_SECONDS` (900), `PROVIDER_OUTAGE_SECONDS`
(300), `HOURS_GRACE_SECONDS` (300), `ADMIN_GITHUB_LOGINS`, and the dev-only `AUTH_DEV_LOGIN`,
`ALLOW_LOCAL_ENDPOINTS` (both refused unless `PUBLIC_URL` is loopback).

For Paddle the Start price needs a 7-day trial configured in Paddle; the webhook URL is
`<PUBLIC_URL>/billing/paddle/webhook`.

## Checks

```bash
cd cloud/backend  && ../.venv/bin/python -m pytest && ../.venv/bin/ruff check .
cd cloud/runner   && ../.venv/bin/python -m pytest && ../.venv/bin/ruff check .
cd cloud/frontend && npm test && npm run build
node --test "cloud/pixel/tests/*.test.mjs"      # the pixel window's page logic
./cloud/frontend/tools/lighthouse.sh          # against a running dev.sh; METHOD=simulate for Lighthouse's model
```

`backend/tests/test_e2e_local.py` is the end-to-end test: a real server on a real port, the local
provider with a real runner, the funnel walked over HTTP, the task streamed over the browser's
WebSocket, the charge and the funnel checked. Its `real_core` variant does the same through the
real Kvist core; deselect it with `-m "not real_core"` when the core's environment is missing.

## Licence

GNU AGPL v3 or later, see [LICENSE](LICENSE) and [LICENSE-NOTIFY.md](LICENSE-NOTIFY.md).
The Kvist Mono font (a JetBrains Mono subset) is under the SIL OFL, `frontend/src/fonts/OFL.txt`.

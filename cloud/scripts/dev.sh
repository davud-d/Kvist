#!/usr/bin/env bash
# Kvist Cloud on this computer, one command: the server, the local machine provider, the real
# Kvist core, and a fake model so nothing is spent. Dev sign-in. Billing is off unless cloud/.env turns it on.
#
#   ./cloud/scripts/dev.sh               the real Kvist core when installed here, else a scripted stand-in
#   ./cloud/scripts/dev.sh --fake-kvist  always the scripted stand-in
#
# Environment: PORT (8740), MODEL_PORT (8799), KVIST_CLOUD_DATA_DIR (cloud/.cloud-data).
set -euo pipefail

CLOUD="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO="$(dirname "$CLOUD")"
DATA="${KVIST_CLOUD_DATA_DIR:-$CLOUD/.cloud-data}"
PORT="${PORT:-8740}"
MODEL_PORT="${MODEL_PORT:-8799}"
PY="$CLOUD/.venv/bin/python"

say() { printf '\033[1m%s\033[0m\n' "$*"; }

if [[ ! -x "$PY" ]]; then
  say "Creating cloud/.venv"
  python3 -m venv "$CLOUD/.venv"
  "$PY" -m pip install -q --upgrade pip
  "$PY" -m pip install -q -e "$CLOUD/backend[dev]" -e "$CLOUD/runner[dev]"
fi

# The real Kvist core when it is installed here, otherwise the scripted stand-in: no flag to remember.
# (--fake-kvist still forces the stand-in.)
if [[ "${1:-}" == "--fake-kvist" || ! -x "$REPO/.venv/bin/python" ]]; then
  [[ "${1:-}" == "--fake-kvist" ]] || echo "The Kvist core is not installed here ($REPO/.venv): using the scripted stand-in."
  export KVIST_COMMAND="[\"$PY\", \"$CLOUD/runner/tests/fake_kvist.py\"]"
fi

if [[ ! -d "$CLOUD/frontend/node_modules" ]]; then
  say "Installing the frontend's build tool"
  (cd "$CLOUD/frontend" && npm install --no-audit --no-fund --silent)
fi
say "Building the frontend"
(cd "$CLOUD/frontend" && node build.mjs)

# Secrets for this computer only, made once, readable by you alone, never printed.
mkdir -p "$DATA"
SECRETS="$DATA/dev-secrets.env"
if [[ ! -f "$SECRETS" ]]; then
  umask 077
  {
    echo "KVIST_CLOUD_SESSION_SECRET=$("$PY" -c 'import secrets; print(secrets.token_urlsafe(48))')"
    echo "KVIST_CLOUD_MASTER_KEY=$("$PY" -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
  } > "$SECRETS"
fi
set -a
# shellcheck disable=SC1090
source "$SECRETS"
set +a

export KVIST_CLOUD_DATA_DIR="$DATA"
export PUBLIC_URL="http://127.0.0.1:$PORT"
export AUTH_DEV_LOGIN=true
export ALLOW_LOCAL_ENDPOINTS=true
# Billing is whatever cloud/.env says (off when there is no such file): no variable to pass.
export ADMIN_GITHUB_LOGINS="${ADMIN_GITHUB_LOGINS:-dev}"

"$PY" "$CLOUD/scripts/fake_model.py" --port "$MODEL_PORT" &
MODEL_PID=$!
trap 'kill "$MODEL_PID" 2>/dev/null || true' EXIT

say "Kvist Cloud: http://127.0.0.1:$PORT"
echo "  Sign in: GitHub OAuth when configured in cloud/.env; otherwise the local user 'dev'."
if grep -qE '^[[:space:]]*BILLING_ENABLED[[:space:]]*=[[:space:]]*true' "$CLOUD/.env" 2>/dev/null; then
  echo "  Payments: ON, real Platega from cloud/.env."
else
  echo "  Card: press 'Skip (dev)'."
fi
echo "  Model: pick 'Other', base URL http://127.0.0.1:$MODEL_PORT/v1, model fake-model, any key of 8+ characters."
echo "  Admin page: http://127.0.0.1:$PORT/admin"
cd "$CLOUD/backend"
"$PY" -m uvicorn --factory kvist_cloud.main:create_app --host 127.0.0.1 --port "$PORT" --log-level info

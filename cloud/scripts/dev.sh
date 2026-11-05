#!/usr/bin/env bash
# Kvist Cloud on this computer, one command: the server, the local machine provider, the real
# Kvist core, and a fake model so nothing is spent. Dev sign-in, billing off.
#
#   ./cloud/scripts/dev.sh               the real Kvist core against the fake model
#   ./cloud/scripts/dev.sh --fake-kvist  a scripted stand-in for Kvist (no core needed)
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

if [[ "${1:-}" == "--fake-kvist" ]]; then
  export KVIST_COMMAND="[\"$PY\", \"$CLOUD/runner/tests/fake_kvist.py\"]"
elif [[ ! -x "$REPO/.venv/bin/python" ]]; then
  echo "The Kvist core needs its own environment first:" >&2
  echo "  python3 -m venv $REPO/.venv && $REPO/.venv/bin/python -m pip install -r $REPO/requirements.txt" >&2
  echo "or run with --fake-kvist to use the scripted stand-in." >&2
  exit 1
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
export BILLING_ENABLED=false
export ADMIN_GITHUB_LOGINS="${ADMIN_GITHUB_LOGINS:-dev}"

"$PY" "$CLOUD/scripts/fake_model.py" --port "$MODEL_PORT" &
MODEL_PID=$!
trap 'kill "$MODEL_PID" 2>/dev/null || true' EXIT

say "Kvist Cloud: http://127.0.0.1:$PORT"
echo "  Sign in: the GitHub button signs you in as the local user 'dev'."
echo "  Card: press 'Skip (dev)'."
echo "  Model: pick 'Other', base URL http://127.0.0.1:$MODEL_PORT/v1, model fake-model, any key of 8+ characters."
echo "  Admin page: http://127.0.0.1:$PORT/admin"
cd "$CLOUD/backend"
"$PY" -m uvicorn --factory kvist_cloud.main:create_app --host 127.0.0.1 --port "$PORT" --log-level info

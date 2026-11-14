#!/usr/bin/env bash
# Makes the server's env file with fresh secrets, once.
#
#   gen-secrets.sh /etc/kvist-cloud/cloud.env
#
# Starts from env.example, fills KVIST_CLOUD_SESSION_SECRET and KVIST_CLOUD_MASTER_KEY, adds the production
# settings you still have to fill in. The file is written 0600 (0640 root:kvist-cloud when that group exists, so
# the service can read it). It refuses to overwrite and prints no secret: save the master key from the file into
# your password manager and on paper, because without it every customer's model key is unreadable.
set -euo pipefail

CLOUD="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${1:-}"
[[ -n "$TARGET" ]] || { echo "usage: gen-secrets.sh <env-file>" >&2; exit 2; }
[[ ! -e "$TARGET" ]] || { echo "error: $TARGET exists; not overwriting (rotate with rotate_master_key.sh)" >&2; exit 1; }
[[ -f "$CLOUD/env.example" ]] || { echo "error: $CLOUD/env.example not found" >&2; exit 1; }

# A Fernet key is 32 random bytes in URL-safe base64.
master_key="$(head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '\n')"
session_secret="$(head -c 48 /dev/urandom | base64 | tr -d '\n=' | tr '+/' '-_')"

mkdir -p "$(dirname "$TARGET")"
(
    umask 077
    sed -e "s|^KVIST_CLOUD_SESSION_SECRET=.*|KVIST_CLOUD_SESSION_SECRET=${session_secret}|" \
        -e "s|^KVIST_CLOUD_MASTER_KEY=.*|KVIST_CLOUD_MASTER_KEY=${master_key}|" \
        "$CLOUD/env.example" > "$TARGET"
    cat >> "$TARGET" <<'EOF'

# --- production settings: fill in, then check with `python -m kvist_cloud.prodcheck --env-file <this file>` ---
# PUBLIC_URL=https://cloud.example.org
# DATABASE_URL=postgresql+psycopg://kvist_cloud:<password>@127.0.0.1:5432/kvist_cloud
# KVIST_CLOUD_DATA_DIR=/var/lib/kvist-cloud
# MACHINE_PROVIDER=sprites
# ADMIN_GITHUB_LOGINS=<your-github-login>
EOF
)
if getent group kvist-cloud >/dev/null 2>&1; then
    chgrp kvist-cloud "$TARGET"
    chmod 640 "$TARGET"
fi
echo "ok: $TARGET written (secrets generated, not shown)."
echo "Next: save KVIST_CLOUD_MASTER_KEY from it somewhere that is not this server or its backups, then fill in the rest."

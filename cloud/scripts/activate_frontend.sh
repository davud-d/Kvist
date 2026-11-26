#!/usr/bin/env bash
# Runs on the server, as root, from deploy.sh when only the frontends changed since the live release: puts the
# uploaded frontend (<release>/frontend/dist.new) in place of the live release's one and restarts the service (the
# phone page is read once at start), or puts the old frontend back if the service does not come up.
#
#   activate_frontend.sh /opt/kvist-cloud/releases/<live id> <revision>
#
# Nothing else is touched: no venv, no configuration check, no backup or migration, no guest image. That is what
# makes it seconds instead of minutes, and why deploy.sh takes this path only when nothing else changed.
set -Eeuo pipefail

ROOT=/opt/kvist-cloud

die() { echo "error: $*" >&2; exit 1; }

RELEASE="${1:-}"
REVISION="${2:-}"
[[ $EUID -eq 0 ]] || die "run as root"
[[ -n "$REVISION" && "$RELEASE" == "$ROOT/releases/"* ]] || die "usage: activate_frontend.sh $ROOT/releases/<id> <revision>"
[[ "$(readlink -f "$ROOT/current")" == "$RELEASE" ]] || die "$RELEASE is not the live release"
FRONT="$RELEASE/frontend"
[[ -f "$FRONT/dist.new/mobile/index.html" ]] || die "no uploaded frontend in $FRONT/dist.new"

wait_healthy() {
    for _ in $(seq 1 60); do
        if curl -fsS -m 3 http://127.0.0.1:8740/healthz >/dev/null 2>&1; then return 0; fi
        sleep 1
    done
    return 1
}

rm -rf "$FRONT/dist.old"
mv -T "$FRONT/dist" "$FRONT/dist.old"
mv -T "$FRONT/dist.new" "$FRONT/dist"
systemctl restart kvist-cloud
if ! wait_healthy; then
    echo "FAIL: the service did not become healthy within 60 s; putting the old frontend back" >&2
    journalctl -u kvist-cloud -n 30 --no-pager >&2 || true
    mv -T "$FRONT/dist" "$FRONT/dist.new"
    mv -T "$FRONT/dist.old" "$FRONT/dist"
    systemctl restart kvist-cloud
    wait_healthy && echo "the old frontend is serving again" >&2 || echo "FAIL: not healthy with the old frontend either" >&2
    exit 1
fi
# deploy.sh compares the next deploy with this revision; the release's name still says what its backend is.
echo "$REVISION" > "$RELEASE/REVISION"
rm -rf "$FRONT/dist.old"
echo "ok: the frontend of $REVISION is live in $(basename "$RELEASE")"

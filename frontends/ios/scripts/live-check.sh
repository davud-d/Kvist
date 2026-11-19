#!/usr/bin/env bash
# Runs LiveServerTests against a real kvist_web beacon and HTTP server on loopback (no agent, no network
# beyond 127.0.0.1). Needs the repo's .venv (or PYTHON) and a Swift toolchain (or `swift` in a container).
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
repo="$(cd "$here/../.." && pwd)"
python="${PYTHON:-$repo/.venv/bin/python}"
ports="$(mktemp)"
trap 'kill "$server" 2>/dev/null || true; rm -f "$ports"' EXIT

PYTHONPATH="$repo:$repo/frontends/web-local" "$python" -u "$here/scripts/live_server.py" >"$ports" &
server=$!
for _ in $(seq 1 50); do
  grep -q READY "$ports" && break
  sleep 0.1
done
grep -q READY "$ports" || { echo "the live server did not start" >&2; cat "$ports" >&2; exit 1; }
read -r _ beacon http _ <<<"$(grep READY "$ports")"

cd "$here"
export KVIST_LIVE_BEACON_PORT="$beacon" KVIST_LIVE_HTTP_PORT="$http"
if [ -n "${SWIFT_IN_PODMAN:-}" ]; then
  # --network host: the container reaches the loopback servers above.
  podman run --rm --network host -v "$here":/pkg:Z -w /pkg -e KVIST_LIVE_BEACON_PORT -e KVIST_LIVE_HTTP_PORT \
    "$SWIFT_IN_PODMAN" swift test --scratch-path /tmp/b --filter LiveServerTests
else
  swift test --filter LiveServerTests
fi

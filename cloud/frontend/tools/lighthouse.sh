#!/usr/bin/env bash
# Lighthouse over the funnel's screens on slow 3G, against a running dev server (scripts/dev.sh).
#
#   ./cloud/frontend/tools/lighthouse.sh [http://127.0.0.1:8740]
#
# Slow 3G as in Chrome DevTools: 400 ms round trip, 400 kbit/s down, a 4x slower CPU.
# Reports go to cloud/.lighthouse/; a summary is printed. Needs Chrome or Chromium and npx.
set -euo pipefail

BASE="${1:-http://127.0.0.1:8740}"
CLOUD="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT="$CLOUD/.lighthouse"
mkdir -p "$OUT"
CHROME="$(command -v chromium || command -v google-chrome-stable || command -v google-chrome)"

# METHOD=devtools (default): Chrome really throttles the network and CPU, so font-display and
# the paint order are what a phone gets. METHOD=simulate: Lighthouse's model on top of a fast
# load; it counts a font as blocking the first paint whatever font-display says.
METHOD="${METHOD:-devtools}"
if [[ "$METHOD" == "simulate" ]]; then
  THROTTLE=(--throttling-method=simulate --throttling.rttMs=400 --throttling.throughputKbps=400
            --throttling.cpuSlowdownMultiplier=4)
else
  THROTTLE=(--throttling-method=devtools --throttling.requestLatencyMs=400
            --throttling.downloadThroughputKbps=400 --throttling.uploadThroughputKbps=400
            --throttling.cpuSlowdownMultiplier=4)
fi

# A session for the screens behind sign-in: the dev sign-in, with a cookie jar of our own.
JAR="$(mktemp)"
trap 'rm -f "$JAR"' EXIT
curl -s -o /dev/null -c "$JAR" -b "$JAR" -L "$BASE/auth/github/login?next=/start"
SESSION="$(awk '$6 == "kc_session" {print $7}' "$JAR")"
if [[ -z "$SESSION" ]]; then
  echo "No session: is the server running with AUTH_DEV_LOGIN=true?" >&2
  exit 1
fi

run() {
  local name="$1" url="$2" lang="$3" cookie="$4"
  local headers="{\"Cookie\": \"kc_lang=$lang${cookie:+; kc_session=$cookie}\"}"
  # npm would echo the command line, cookie included: keep it quiet.
  CHROME_PATH="$CHROME" NPM_CONFIG_LOGLEVEL=silent npx -y lighthouse@12.8.2 "$url" \
    --quiet --only-categories=performance,accessibility,best-practices \
    --form-factor=mobile --screenEmulation.mobile=true \
    "${THROTTLE[@]}" \
    --extra-headers="$headers" \
    --chrome-flags="--headless=new --no-sandbox" \
    --output=json --output-path="$OUT/$name.json" >/dev/null
  node -e '
    const r = require(process.argv[1]);
    const a = r.audits, c = r.categories;
    const ms = (k) => Math.round(a[k].numericValue);
    console.log([process.argv[2].padEnd(16),
      "perf " + Math.round(c.performance.score * 100),
      "a11y " + Math.round(c.accessibility.score * 100),
      "bp " + Math.round(c["best-practices"].score * 100),
      "FCP " + ms("first-contentful-paint") + " ms",
      "LCP " + ms("largest-contentful-paint") + " ms",
      "TTI " + ms("interactive") + " ms",
      "TBT " + ms("total-blocking-time") + " ms",
      "CLS " + a["cumulative-layout-shift"].numericValue.toFixed(3),
      "bytes " + Math.round(a["total-byte-weight"].numericValue / 1024) + " KB"].join("  "));
  ' "$OUT/$name.json" "$name"
}

run pricing-en "$BASE/" en ""
run pricing-ru "$BASE/" ru ""
run start-en "$BASE/start" en "$SESSION"
run onboarding-ru "$BASE/onboarding" ru "$SESSION"

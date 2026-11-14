#!/usr/bin/env bash
# Looks at a running server from the outside, the way a customer's browser and a runner would. Run it after a deploy,
# from anywhere:
#
#   scripts/smoke_prod.sh https://cloud.example.org
#   scripts/smoke_prod.sh http://127.0.0.1:8740 --allow-http     # a local server: skips the HTTPS and certificate checks
#
# Exits 1 if anything is wrong. It sends no credentials and changes nothing.
set -uo pipefail

BASE="${1:-}"
ALLOW_HTTP=0
[[ "${2:-}" == "--allow-http" ]] && ALLOW_HTTP=1
[[ -n "$BASE" ]] || { echo "usage: smoke_prod.sh <base-url> [--allow-http]" >&2; exit 2; }
BASE="${BASE%/}"

FAILED=0
pass() { printf '  ok    %s\n' "$1"; }
fail() { printf '  FAIL  %s\n' "$1"; FAILED=1; }

status() { curl -sS -o /dev/null -m 15 -w '%{http_code}' "$@" 2>/dev/null || echo 000; }
header() { curl -sS -o /dev/null -m 15 -D - "$2" 2>/dev/null | tr -d '\r' | awk -v h="$(echo "$1" | tr 'A-Z' 'a-z')" -F': ' 'tolower($1)==h {print $2; exit}'; }

echo "Smoke test of $BASE"

if [[ "$ALLOW_HTTP" == 0 ]]; then
    [[ "$BASE" == https://* ]] && pass "served over https" || fail "the address is not https://"
    host="${BASE#https://}"; host="${host%%/*}"
    if [[ "$host" != *:* ]] && echo | openssl s_client -connect "$host:443" -servername "$host" 2>/dev/null \
        | openssl x509 -noout -checkend $((14 * 86400)) >/dev/null 2>&1; then
        pass "the certificate is valid for at least 14 more days"
    else
        fail "the certificate is missing, invalid or expires within 14 days"
    fi
    redirect="$(curl -sS -o /dev/null -m 15 -w '%{http_code} %{redirect_url}' "http://$host/" 2>/dev/null || echo "000 ")"
    [[ "$redirect" == 30[1278]\ https://* ]] && pass "plain http redirects to https" || fail "plain http does not redirect to https ($redirect)"
fi

health="$(curl -sS -m 15 "$BASE/healthz" 2>/dev/null || true)"
[[ "$health" == *'"ok"'* ]] && pass "/healthz is ok" || fail "/healthz did not answer ok"

[[ "$(status "$BASE/")" == 200 ]] && pass "the front page answers 200" || fail "the front page does not answer 200"
[[ -n "$(header content-security-policy "$BASE/")" ]] && pass "Content-Security-Policy is sent" || fail "no Content-Security-Policy header"
[[ "$ALLOW_HTTP" == 1 || -n "$(header strict-transport-security "$BASE/")" ]] && pass "HSTS is sent" || fail "no Strict-Transport-Security header"

for path in /docs /openapi.json /redoc; do
    [[ "$(status "$BASE$path")" == 404 ]] && pass "$path is not exposed" || fail "$path is exposed"
done

[[ "$(status "$BASE/api/me")" == 401 ]] && pass "/api/me wants a session" || fail "/api/me does not answer 401 without a session"
location="$(curl -sS -o /dev/null -m 15 -w '%{redirect_url}' "$BASE/admin" 2>/dev/null || true)"
[[ "$location" == "$BASE/" || "$location" == "/" ]] && pass "/admin sends strangers away" || fail "/admin does not redirect an anonymous visitor ($location)"

# The sign-in button must go to GitHub. A redirect back into the app means the development sign-in is on.
login="$(curl -sS -o /dev/null -m 15 -w '%{redirect_url}' "$BASE/auth/github/login" 2>/dev/null || true)"
[[ "$login" == https://github.com/login/oauth/authorize* ]] && pass "sign-in goes to GitHub" || fail "sign-in does not go to GitHub ($login): is AUTH_DEV_LOGIN on or GitHub unset?"

# A runner's socket: a handshake with no credentials must reach the app (101 then close, or 401/403), not die in the proxy.
# HTTP/1.1 on purpose: that is what browsers and runners use for WebSockets (nginx does not proxy them over h2).
ws="$(status --http1.1 -H 'Connection: Upgrade' -H 'Upgrade: websocket' -H 'Sec-WebSocket-Version: 13' \
    -H 'Sec-WebSocket-Key: c21va2UtdGVzdC1rZXkxMg==' --max-time 5 "$BASE/gateway/ws")"
case "$ws" in
    101|401|403) pass "the runners' socket path reaches the app ($ws)" ;;
    *) fail "the runners' socket path answered $ws (proxy not passing WebSocket upgrades?)" ;;
esac

if [[ "$FAILED" == 0 ]]; then echo "All checks passed."; else echo "Some checks FAILED."; fi
exit "$FAILED"

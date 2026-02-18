#!/usr/bin/env sh
set -eu

URL="${1:-https://127.0.0.1:5000/v2/}"

echo "[diag] URL: $URL"
echo

echo "[diag] Files:"
ls -la /tls 2>/dev/null || echo "  (no /tls mounted)"
ls -la /run/secrets/registry 2>/dev/null || echo "  (no /run/secrets/registry mounted)"
echo

echo "[diag] Presence checks:"
test -s /tls/tls.crt && echo "  OK: /tls/tls.crt" || echo "  WARN: /tls/tls.crt missing/empty"
test -s /tls/tls.key && echo "  OK: /tls/tls.key" || echo "  WARN: /tls/tls.key missing/empty"
test -s /run/secrets/registry/auth-registry.crt \
  && echo "  OK: /run/secrets/registry/auth-registry.crt" \
  || echo "  WARN: /run/secrets/registry/auth-registry.crt missing/empty"
echo

echo "[diag] wget output (server response):"
set +e
OUT="$(wget -S --spider --no-check-certificate "$URL" 2>&1)"
RC=$?
set -e

echo "$OUT" | sed -n '1,120p'
echo
echo "[diag] wget exit code: $RC"
echo

# Достаём первую строку HTTP/*
HTTP_LINE="$(echo "$OUT" | tr -d '\r' | grep -m1 -E 'HTTP/[0-9.]+' || true)"
echo "[diag] First HTTP line: ${HTTP_LINE:-<none>}"

CODE="$(echo "$HTTP_LINE" | awk '{print $2}' 2>/dev/null || true)"
echo "[diag] Parsed code: ${CODE:-<none>}"
echo

if [ "$CODE" = "200" ] || [ "$CODE" = "401" ]; then
  echo "[diag] RESULT: OK (200/401 accepted)"
  exit 0
fi

echo "[diag] RESULT: FAIL (expected 200 or 401)"
exit 1

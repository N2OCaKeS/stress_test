#!/usr/bin/env bash
# Smoke-test после `make k8s-deploy`. Проверяет:
#   1. /health и /ready на каждом сервисе (auth/logging/server, worker
#      без HTTP — проверяется только через kubectl rollout status).
#   2. Login admin/<password> → 200, JWT возвращён.
#   3. GET /me с этим JWT → identity содержит ожидаемый username.
#   4. GET /api/logging/v1/events?action=user.login_success — login-event
#      попал в audit-канал (loging_service видит auth_service).
#
# Usage:
#   BASE_URL=https://dbos.example.com \
#   ADMIN_USER=admin \
#   ADMIN_PASS=1234 \
#   scripts/k8s/smoke_test.sh
#
# Дефолты: BASE_URL=https://dbos.local, ADMIN_USER=admin, ADMIN_PASS=1234
# (соответствует `make seed` для дев-стенда).
#
# curl -k оставлен по умолчанию из-за self-signed cert'а в дев-кластере.
# Для prod-проверки выставь CURL_OPTS='' и подкинь корневой CA.

set -euo pipefail

# BASE_URL: если оператор не задал явно, пытаемся восстановить из
#   1) k8s/.env.k8s (его пишет gen_secrets.sh — INGRESS_HOST=<dns-or-ip>),
#   2) kubectl -n dbos get ingress dbos-ingress -o jsonpath=… (rules[0].host
#      может быть пустым при IP-режиме — в этом случае fallback не сработает).
# Финальный дефолт — https://dbos.local (исторический dev-стенд).
if [[ -z "${BASE_URL:-}" ]]; then
    SCRIPT_DIR_SMOKE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    ENV_K8S="$SCRIPT_DIR_SMOKE/../../k8s/.env.k8s"
    if [[ -f "$ENV_K8S" ]]; then
        # shellcheck source=/dev/null
        . "$ENV_K8S"
        if [[ -n "${INGRESS_HOST:-}" ]]; then
            BASE_URL="https://${INGRESS_HOST}"
        fi
    fi
    if [[ -z "${BASE_URL:-}" ]] && command -v kubectl >/dev/null 2>&1; then
        H=$(kubectl -n dbos get ingress dbos-ingress \
            -o jsonpath='{.spec.rules[0].host}' 2>/dev/null || true)
        if [[ -n "$H" ]]; then
            BASE_URL="https://${H}"
        fi
    fi
fi
BASE_URL="${BASE_URL:-https://dbos.local}"
ADMIN_USER="${ADMIN_USER:-admin}"
# По умолчанию тянем admin-пароль из k8s-секрета dbos-secrets, чтобы smoke
# не падал на 401 после `gen_secrets.sh` (где пароль каждый раз новый).
# Fallback на dev-дефолт `1234` (соответствует `make seed`).
if [[ -z "${ADMIN_PASS:-}" ]] && command -v kubectl >/dev/null 2>&1; then
    ADMIN_PASS=$(kubectl -n dbos get secret dbos-secrets \
        -o jsonpath='{.data.INITIAL_ADMIN_PASSWORD}' 2>/dev/null | base64 -d 2>/dev/null || true)
fi
ADMIN_PASS="${ADMIN_PASS:-1234}"
CURL_OPTS="${CURL_OPTS:--k}"

pass=0
fail=0

report() {
    local status=$1
    local label=$2
    if [[ "$status" == "PASS" ]]; then
        echo "  ✓ $label"
        pass=$((pass + 1))
    else
        echo "  ✗ $label"
        fail=$((fail + 1))
    fi
}

probe() {
    local label=$1
    local url=$2
    local expected=${3:-200}
    local code
    code=$(curl $CURL_OPTS -s -o /dev/null -w "%{http_code}" "$url" || echo "000")
    if [[ "$code" == "$expected" ]]; then
        report PASS "$label ($code)"
    else
        report FAIL "$label ($code, expected $expected)"
    fi
}

echo "▶ Smoke-test против $BASE_URL"
echo

echo "1. Health/Ready probes"
probe "auth     /health"  "$BASE_URL/api/auth/v1/health"
probe "auth     /ready"   "$BASE_URL/api/auth/v1/ready"
probe "logging  /health"  "$BASE_URL/api/logging/v1/health"
probe "logging  /ready"   "$BASE_URL/api/logging/v1/ready"
probe "server   /health"  "$BASE_URL/api/server/v1/health"
probe "server   /ready"   "$BASE_URL/api/server/v1/ready"
echo

echo "2. Login admin/<password>"
LOGIN_RESPONSE=$(curl $CURL_OPTS -s -w "\n%{http_code}" \
    -X POST "$BASE_URL/api/auth/v1/login" \
    -H "Content-Type: application/json" \
    -d "{\"username\":\"$ADMIN_USER\",\"password\":\"$ADMIN_PASS\"}")
LOGIN_CODE=$(echo "$LOGIN_RESPONSE" | tail -n1)
LOGIN_BODY=$(echo "$LOGIN_RESPONSE" | head -n -1)
if [[ "$LOGIN_CODE" == "200" ]]; then
    report PASS "login → 200"
else
    report FAIL "login → $LOGIN_CODE"
    echo "  body: $LOGIN_BODY" >&2
fi

ACCESS_TOKEN=$(echo "$LOGIN_BODY" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("access_token",""))' 2>/dev/null || echo "")
if [[ -n "$ACCESS_TOKEN" ]]; then
    report PASS "access_token извлечён"
else
    report FAIL "access_token не найден в ответе"
fi
echo

echo "3. GET /me"
if [[ -n "$ACCESS_TOKEN" ]]; then
    ME_BODY=$(curl $CURL_OPTS -s \
        -H "Authorization: Bearer $ACCESS_TOKEN" \
        "$BASE_URL/api/auth/v1/me")
    ME_USER=$(echo "$ME_BODY" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("username",""))' 2>/dev/null || echo "")
    if [[ "$ME_USER" == "$ADMIN_USER" ]]; then
        report PASS "/me.username == $ADMIN_USER"
    else
        report FAIL "/me.username = '$ME_USER' (ожидали '$ADMIN_USER')"
        echo "  body: $ME_BODY" >&2
    fi
else
    report FAIL "/me пропущен — нет access_token"
fi
echo

echo "4. GET /api/logging/v1/events?action=user.login_success"
# loging_admin отдельный, но в дев `make seed` его пароль тот же, что у admin.
# В prod подмени LOGGING_ADMIN_PASS отдельной env-переменной.
LOG_USER="${LOGGING_ADMIN_USER:-loging_admin}"
LOG_PASS="${LOGGING_ADMIN_PASS:-$ADMIN_PASS}"
LOG_LOGIN=$(curl $CURL_OPTS -s -X POST "$BASE_URL/api/auth/v1/login" \
    -H "Content-Type: application/json" \
    -d "{\"username\":\"$LOG_USER\",\"password\":\"$LOG_PASS\"}")
LOG_TOKEN=$(echo "$LOG_LOGIN" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("access_token",""))' 2>/dev/null || echo "")

if [[ -n "$LOG_TOKEN" ]]; then
    EVENTS=$(curl $CURL_OPTS -s \
        -H "Authorization: Bearer $LOG_TOKEN" \
        "$BASE_URL/api/logging/v1/events?action=user.login_success&limit=5")
    COUNT=$(echo "$EVENTS" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(len(d.get("items",d) if isinstance(d,dict) else d))' 2>/dev/null || echo "0")
    if [[ "${COUNT:-0}" -gt 0 ]]; then
        report PASS "events содержит login_success (n=$COUNT)"
    else
        report FAIL "events: 0 login_success (audit не сработал?)"
    fi
else
    report FAIL "не удалось залогиниться как $LOG_USER (skip events-check)"
fi
echo

echo "─────────────────────────────"
echo "  pass: $pass    fail: $fail"
echo "─────────────────────────────"
[[ $fail -eq 0 ]]

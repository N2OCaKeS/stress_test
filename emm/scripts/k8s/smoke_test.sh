#!/usr/bin/env bash
# Smoke-test после `make k8s-deploy`. Проверяет:
#   1. /health и /ready на всех HTTP-сервисах (auth/logging/server/secret).
#      server-worker без HTTP — проверяется через kubectl rollout status.
#   2. Login admin/<password> → 200, JWT возвращён.
#   2a. Если /me отдаёт 403 PASSWORD_CHANGE_REQUIRED (initial admin прямо
#       после deploy) — выполняется self-reset через POST /users/me/password
#       с новым случайным паролем. Новый пароль пишется в
#       /tmp/dbos-smoke-new-admin-pass.txt (chmod 600). Re-login под новым
#       паролем — получаем свежий access_token. Если /me сразу 200 — шаг
#       пропускается (пароль уже сменён в предыдущем прогоне или вручную).
#   3. GET /me с этим JWT → identity содержит ожидаемый username.
#   4. GET /api/logging/v1/events?action=user.login_success — login-event
#      попал в audit-канал. В prod юзера `loging_admin` нет — шаг
#      выполняется, только если LOGGING_ADMIN_USER явно задан (для dev/seed
#      сценария). Иначе skip с PASS-маркером.
#   5. server-worker — kubectl -n dbos rollout status deploy/server-worker.
#
# Usage:
#   BASE_URL=https://dbos.example.com \
#   ADMIN_USER=admin \
#   ADMIN_PASS=<initial-or-current-admin-pass> \
#   scripts/k8s/smoke_test.sh
#
# Идемпотентность:
#   - Первый прогон на свежем prod: must_change_password=true → шаг 2a
#     меняет пароль на случайный, сохраняет в /tmp/dbos-smoke-new-admin-pass.txt.
#     Оператор обязан обновить ADMIN_PASS (или k8s-секрет
#     dbos-secrets.INITIAL_ADMIN_PASSWORD) перед следующим прогоном.
#   - Последующие прогоны: /me даёт 200 сразу, шаг 2a skip'ается.
#
# Опциональные env:
#   SMOKE_SKIP_PASSWORD_CHANGE=true — форсированно пропустить шаг 2a (если
#       оператор сам сменил пароль вне smoke'а и /me всё ещё 403 — кейс
#       нелогичный, но флаг оставлен на крайний случай).
#   LOGGING_ADMIN_USER, LOGGING_ADMIN_PASS — пара для шага 4. Без них шаг
#       skip'ается (PASS) — нормальный prod-сценарий.
#   SKIP_WORKER_ROLLOUT=true — не дёргать kubectl (например, нет доступа к
#       кластеру с хоста, на котором гоняется smoke).
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
# По умолчанию тянем admin-пароль из k8s-секрета dbos-secrets. Если smoke
# уже однажды менял пароль (см. шаг 2a — сохраняет в `dbos-smoke-new-admin-pass`),
# Secret отстаёт от реального, поэтому смотрим и в /tmp-файл, и пробуем оба
# по порядку — initial → smoke-rotated. Fallback на dev-дефолт `1234`.
SMOKE_PASS_FILE="${SMOKE_PASS_FILE:-/tmp/dbos-smoke-new-admin-pass.txt}"
if [[ -z "${ADMIN_PASS:-}" ]] && command -v kubectl >/dev/null 2>&1; then
    ADMIN_PASS=$(kubectl -n dbos get secret dbos-secrets \
        -o jsonpath='{.data.INITIAL_ADMIN_PASSWORD}' 2>/dev/null | base64 -d 2>/dev/null || true)
fi
ADMIN_PASS="${ADMIN_PASS:-1234}"
# Альтернативный пароль из /tmp (заполняется шагом 2a при первом смене).
# Используется как fallback ниже в шаге 2, если первый login отдаст 401.
ADMIN_PASS_ALT=""
if [[ -r "$SMOKE_PASS_FILE" ]]; then
    # Шаг 2a пишет файл из shell-комментариев (# ...) и одной строки пароля.
    # Берём последнюю не-комментарий, не-пустую строку.
    ADMIN_PASS_ALT=$(awk '!/^[[:space:]]*#/ && NF > 0 {p=$0} END{print p}' "$SMOKE_PASS_FILE" 2>/dev/null || true)
fi
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

# Генерим криптостойкий пароль 24 байта → base64 (~32 символа). Без openssl
# не обойтись — /dev/urandom + base64 не везде есть в одну строку.
gen_password() {
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -base64 24 | tr -d '\n' | tr -d '=' | tr '+/' '-_'
    else
        # Fallback: head -c из /dev/urandom + base64 из coreutils.
        head -c 24 /dev/urandom | base64 | tr -d '\n' | tr -d '=' | tr '+/' '-_'
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
probe "secret   /health"  "$BASE_URL/api/secret/v1/health"
probe "secret   /ready"   "$BASE_URL/api/secret/v1/ready"
echo

echo "2. Login admin/<password>"
LOGIN_RESPONSE=$(curl $CURL_OPTS -s -w "\n%{http_code}" \
    -X POST "$BASE_URL/api/auth/v1/login" \
    -H "Content-Type: application/json" \
    -d "{\"username\":\"$ADMIN_USER\",\"password\":\"$ADMIN_PASS\"}")
LOGIN_CODE=$(echo "$LOGIN_RESPONSE" | tail -n1)
LOGIN_BODY=$(echo "$LOGIN_RESPONSE" | head -n -1)
# Если Secret-пароль отдаёт 401 (например, smoke ранее уже сменил admin'у
# пароль на одноразовый и сохранил в /tmp/dbos-smoke-new-admin-pass.txt) —
# пробуем альтернативный пароль из этого файла.
if [[ "$LOGIN_CODE" != "200" && -n "$ADMIN_PASS_ALT" && "$ADMIN_PASS_ALT" != "$ADMIN_PASS" ]]; then
    echo "  ↻ Secret-пароль вернул $LOGIN_CODE; пробую сохранённый ($SMOKE_PASS_FILE)..."
    LOGIN_RESPONSE=$(curl $CURL_OPTS -s -w "\n%{http_code}" \
        -X POST "$BASE_URL/api/auth/v1/login" \
        -H "Content-Type: application/json" \
        -d "{\"username\":\"$ADMIN_USER\",\"password\":\"$ADMIN_PASS_ALT\"}")
    LOGIN_CODE=$(echo "$LOGIN_RESPONSE" | tail -n1)
    LOGIN_BODY=$(echo "$LOGIN_RESPONSE" | head -n -1)
    if [[ "$LOGIN_CODE" == "200" ]]; then
        ADMIN_PASS="$ADMIN_PASS_ALT"
    fi
fi
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

echo "2a. Self-detect must_change_password (POST /users/me/password при 403)"
# Probe /me чтобы понять, нужен ли password reset. На свежем prod initial
# admin приходит с must_change_password=true → middleware вернёт 403
# PASSWORD_CHANGE_REQUIRED на любой не-whitelist путь. Если /me даёт 200 —
# пароль уже сменён, пропускаем шаг.
if [[ -z "$ACCESS_TOKEN" ]]; then
    report FAIL "2a пропущен — нет access_token"
elif [[ "${SMOKE_SKIP_PASSWORD_CHANGE:-false}" == "true" ]]; then
    report PASS "2a skipped (SMOKE_SKIP_PASSWORD_CHANGE=true)"
else
    ME_PROBE_CODE=$(curl $CURL_OPTS -s -o /dev/null -w "%{http_code}" \
        -H "Authorization: Bearer $ACCESS_TOKEN" \
        "$BASE_URL/api/auth/v1/me" || echo "000")
    if [[ "$ME_PROBE_CODE" == "200" ]]; then
        report PASS "2a skipped (must_change_password уже сброшен)"
    elif [[ "$ME_PROBE_CODE" == "403" ]]; then
        # Generates новый пароль, меняет, сохраняет на диск, re-login.
        NEW_ADMIN_PASS=$(gen_password)
        if [[ -z "$NEW_ADMIN_PASS" ]]; then
            report FAIL "2a: не удалось сгенерировать новый пароль"
        else
            PWD_FILE="/tmp/dbos-smoke-new-admin-pass.txt"
            # Пишем пароль до запроса — иначе при сбое сети пароль может уже
            # быть применён (Argon2id-hash в БД), но оператор не узнает,
            # какой именно. Лучше: записать → попытаться сменить → если
            # ответ != 200, оставить файл с пометкой UNCONFIRMED.
            umask 077
            {
                echo "# DBOS smoke_test.sh — сгенерированный пароль для $ADMIN_USER"
                echo "# host: $BASE_URL"
                echo "# timestamp: $(date -Iseconds)"
                echo "# status: PENDING (запрос ещё не отправлен)"
                echo "$NEW_ADMIN_PASS"
            } > "$PWD_FILE"
            chmod 600 "$PWD_FILE"

            PWCHG_RESPONSE=$(curl $CURL_OPTS -s -w "\n%{http_code}" \
                -X POST "$BASE_URL/api/auth/v1/users/me/password" \
                -H "Authorization: Bearer $ACCESS_TOKEN" \
                -H "Content-Type: application/json" \
                -d "{\"old_password\":\"$ADMIN_PASS\",\"new_password\":\"$NEW_ADMIN_PASS\"}")
            PWCHG_CODE=$(echo "$PWCHG_RESPONSE" | tail -n1)
            PWCHG_BODY=$(echo "$PWCHG_RESPONSE" | head -n -1)

            if [[ "$PWCHG_CODE" == "200" ]]; then
                # Обновляем status в файле, чтобы оператор видел что пароль
                # реально применился.
                {
                    echo "# DBOS smoke_test.sh — сгенерированный пароль для $ADMIN_USER"
                    echo "# host: $BASE_URL"
                    echo "# timestamp: $(date -Iseconds)"
                    echo "# status: APPLIED (старый пароль больше не валиден)"
                    echo "# NB: обнови ADMIN_PASS или k8s secret dbos-secrets.INITIAL_ADMIN_PASSWORD"
                    echo "$NEW_ADMIN_PASS"
                } > "$PWD_FILE"
                chmod 600 "$PWD_FILE"
                report PASS "2a: password changed → $PWD_FILE"
                echo "  WARNING: admin password сменён, обнови ADMIN_PASS для следующего smoke" >&2

                # Re-login: текущая сессия revoked'нута сменой пароля, нужен
                # свежий access_token.
                RELOGIN=$(curl $CURL_OPTS -s -w "\n%{http_code}" \
                    -X POST "$BASE_URL/api/auth/v1/login" \
                    -H "Content-Type: application/json" \
                    -d "{\"username\":\"$ADMIN_USER\",\"password\":\"$NEW_ADMIN_PASS\"}")
                RELOGIN_CODE=$(echo "$RELOGIN" | tail -n1)
                RELOGIN_BODY=$(echo "$RELOGIN" | head -n -1)
                if [[ "$RELOGIN_CODE" == "200" ]]; then
                    ACCESS_TOKEN=$(echo "$RELOGIN_BODY" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("access_token",""))' 2>/dev/null || echo "")
                    if [[ -n "$ACCESS_TOKEN" ]]; then
                        report PASS "2a: re-login под новым паролем → 200"
                        # ADMIN_PASS обновляем в памяти — дальше /me-проверка
                        # и (опционально) audit-check сработают.
                        ADMIN_PASS="$NEW_ADMIN_PASS"
                    else
                        report FAIL "2a: re-login → access_token не извлечён"
                    fi
                else
                    report FAIL "2a: re-login → $RELOGIN_CODE"
                    echo "  body: $RELOGIN_BODY" >&2
                fi
            else
                report FAIL "2a: password change → $PWCHG_CODE"
                echo "  body: $PWCHG_BODY" >&2
            fi
        fi
    else
        # /me вернул не 200 и не 403 — что-то другое (401/500/...) — fail.
        report FAIL "2a: /me probe → $ME_PROBE_CODE (ожидали 200 или 403)"
    fi
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
# loging_admin — отдельный юзер с `platform_role=loging_admin`. В prod-deploy
# его НЕТ (создаётся через `make seed` для dev-стенда). account_admin к
# /events доступа не имеет (см. loging_service/.../events.py: только
# loging_admin/loging_reader, обе глобальные роли). Поэтому шаг
# выполняется ТОЛЬКО при явном LOGGING_ADMIN_USER в env — иначе skip.
if [[ -z "${LOGGING_ADMIN_USER:-}" ]]; then
    report PASS "skipped (prod mode, no LOGGING_ADMIN_USER)"
else
    LOG_USER="${LOGGING_ADMIN_USER}"
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
fi
echo

echo "5. server-worker rollout status"
if [[ "${SKIP_WORKER_ROLLOUT:-false}" == "true" ]]; then
    report PASS "skipped (SKIP_WORKER_ROLLOUT=true)"
elif ! command -v kubectl >/dev/null 2>&1; then
    report PASS "skipped (kubectl не найден в PATH)"
else
    if kubectl -n dbos rollout status deploy/server-worker --timeout=30s >/dev/null 2>&1; then
        report PASS "server-worker rollout: ready"
    else
        report FAIL "server-worker rollout не завершился за 30s"
    fi
fi
echo

echo "─────────────────────────────"
echo "  pass: $pass    fail: $fail"
echo "─────────────────────────────"
[[ $fail -eq 0 ]]

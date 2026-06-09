#!/usr/bin/env bash
# Генерация k8s/20-secrets.yaml + 50-ingress.yaml с реальными prod-значениями.
# Идемпотентный: если файлы уже существуют — спрашивает, перезаписывать ли.
#
# Использование:
#   scripts/k8s/gen_secrets.sh                  — авто-детект IP хоста по
#                                                 default-route src; интерактивно
#                                                 предложит подтвердить. При
#                                                 non-tty (вызов из make k8s-zero)
#                                                 берёт IP без вопроса.
#   scripts/k8s/gen_secrets.sh dbos.example.com — DNS-имя аргументом
#   scripts/k8s/gen_secrets.sh 10.177.103.102   — IPv4 аргументом (для closed
#                                                 network без DNS; cert.SAN=IP:,
#                                                 Ingress без host: → принимает
#                                                 любой Host header).
#
#   SAN_EXTRA="DNS:dbos.local"  scripts/k8s/gen_secrets.sh 10.177.103.102
#       — добавить второй SAN-entry (например DNS-имя, когда оператор пока
#       ходит по IP, но хочет, чтобы тот же cert валидировался и по
#       будущему DNS-имени). Формат: одно или несколько "TYPE:VALUE",
#       разделённых запятыми (DNS:host, IP:1.2.3.4).
#
# Внутри:
#   - openssl rand -base64 32 для encryption-keys
#   - openssl rand -hex 16    для HKDF salt
#   - tr-pool A-Za-z0-9       для остальных секретов (URL-safe, readable)
#   - kratкое summary с admin-паролем + перечнем сгенерированных ключей
#     дублируется в /tmp/dbos-secrets-<timestamp>.txt (chmod 600), чтобы
#     не терять его при scroll'е терминала.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
K8S_DIR="$(cd "$SCRIPT_DIR/../../k8s" && pwd)"
SECRETS_OUT="$K8S_DIR/20-secrets.yaml"
INGRESS_OUT="$K8S_DIR/50-ingress.yaml"
INGRESS_TEMPLATE="$K8S_DIR/50-ingress.yaml.template"
ENV_FILE="$K8S_DIR/.env.k8s"

TS="$(date -u +%Y%m%dT%H%M%SZ)"
SUMMARY_OUT="/tmp/dbos-secrets-${TS}.txt"

# ── Domain или IP (для Ingress + CN/SAN сертификата) ──────────────────────────
# DOMAIN на входе может быть:
#   - DNS-именем (dbos.example.com)             → SAN=DNS:<host>, host: в Ingress
#   - IPv4-адресом (10.177.103.102)             → SAN=IP:<addr>, host: пустой
#                                                 в Ingress (Traefik принимает
#                                                 любой Host header).
# Имя переменной оставлено DOMAIN ради backward-совместимости с .env.k8s.
DOMAIN="${1:-}"
if [[ -z "$DOMAIN" ]]; then
    if [[ -f "$ENV_FILE" ]]; then
        # shellcheck source=/dev/null
        . "$ENV_FILE"
        echo "→ Использую сохранённое значение INGRESS_HOST: $INGRESS_HOST"
        DOMAIN="$INGRESS_HOST"
    else
        # Автоопределение primary-IP: src адрес default-route. Работает без DNS,
        # без интерактива и стабильно для одно-NIC VM (наш типичный prod-стенд).
        AUTO_IP=$(ip -4 route get 1.1.1.1 2>/dev/null \
                  | awk '/src/ {for (i=1; i<=NF; i++) if ($i=="src") {print $(i+1); exit}}')
        if [[ -n "$AUTO_IP" ]]; then
            if [[ -t 0 ]]; then
                echo "→ Автоопределённый IP хоста: $AUTO_IP"
                echo "  (комбо IP+DNS: SAN_EXTRA=\"DNS:host.example.com\" $0)"
                read -p "  Использовать $AUTO_IP? [Enter=да / введи другой host]: " RESP
                DOMAIN="${RESP:-$AUTO_IP}"
            else
                # non-tty: вызывали из make k8s-zero — берём auto-IP без вопросов.
                echo "→ Автоопределённый IP хоста: $AUTO_IP (non-tty, принят автоматически)"
                DOMAIN="$AUTO_IP"
            fi
        else
            [[ -t 0 ]] || { echo "ОШИБКА: IP не автодетектится и нет tty для prompt'а." >&2; exit 1; }
            read -p "Домен или IP (например dbos.example.com или 10.177.103.102): " DOMAIN
        fi
        [[ -n "$DOMAIN" ]] || { echo "ОШИБКА: значение пустое." >&2; exit 1; }
    fi
fi

# Определяем тип: IP или DNS. Регэксп грубый (не проверяет 0..255), но достаточен
# для отличения от DNS-имени; openssl потом всё равно отвергнет невалидный IP.
IS_IP=0
if [[ "$DOMAIN" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
    IS_IP=1
fi

# subjectAltName: основная запись + опциональные дополнительные из $SAN_EXTRA.
if [[ "$IS_IP" -eq 1 ]]; then
    SAN_PRIMARY="IP:${DOMAIN}"
else
    SAN_PRIMARY="DNS:${DOMAIN}"
fi
if [[ -n "${SAN_EXTRA:-}" ]]; then
    SAN_ARG="${SAN_PRIMARY},${SAN_EXTRA}"
else
    SAN_ARG="${SAN_PRIMARY}"
fi

# Email для INITIAL_ADMIN: при IP-режиме у нас нет валидного домена, чтобы
# собрать admin@<host>, поэтому подставляем нейтральный плейсхолдер. При DNS —
# admin@<domain> как раньше.
if [[ "$IS_IP" -eq 1 ]]; then
    ADMIN_EMAIL="admin@dbos.local"
else
    ADMIN_EMAIL="admin@${DOMAIN}"
fi

# ── Перезапись ─────────────────────────────────────────────────────────────────
if [[ -f "$SECRETS_OUT" ]] || [[ -f "$INGRESS_OUT" ]]; then
    echo "⚠ Файлы $SECRETS_OUT / $INGRESS_OUT уже существуют."
    echo "  Перезапись СБРОСИТ admin-пароль, аннулирует JWT, заменит мастер-ключ"
    echo "  шифрования (все существующие server_account/IPMI ciphertext'ы станут"
    echo "  недешифруемыми!) и заменит TLS-сертификат."
    echo ""
    echo "  Для смены ТОЛЬКО мастер-ключа без потери ciphertext'ов используй"
    echo "  scripts/k8s/rotate_master_key.sh — он сохраняет previous-ключ"
    echo "  в Secret'е и запускает background re-encrypt."
    echo ""
    read -p "  Перегенерировать ВСЁ? (yes/no): " yn
    [[ "$yn" == "yes" ]] || { echo "Отменено."; exit 0; }
fi

# ── Генератор случайных строк ─────────────────────────────────────────────────
rand() {
    local n=$1
    LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom 2>/dev/null | head -c "$n" || true
}
rand_b64() { openssl rand -base64 "$1" | tr -d '\n='; }
rand_hex() { openssl rand -hex "$1"; }

# Postgres credentials (per-service)
AUTH_DB_PASSWORD=$(rand 32)
LOGGING_DB_PASSWORD=$(rand 32)
SERVER_DB_PASSWORD=$(rand 32)
WORKER_DB_PASSWORD=$(rand 32)
SECRET_DB_PASSWORD=$(rand 32)

# auth_service
AUTH_SECRET_KEY=$(rand 64)
INITIAL_ADMIN_PASSWORD=$(rand 16)

# loging_service service-to-service
LOGGING_SERVICE_API_KEY_AUTH=$(rand 48)
LOGGING_SERVICE_API_KEY_SERVER=$(rand 48)
LOGGING_SERVICE_API_KEY_CONFIG=$(rand 48)
LOGGING_SERVICE_API_KEY_WORKER=$(rand 48)
LOGGING_SERVICE_API_KEY_SECRET=$(rand 48)
# loging_service single outbound для backward-compat (caller'ы пока используют
# одно поле; map выше — для inbound key-separation в loging_service Settings).
LOGGING_SERVICE_API_KEY="$LOGGING_SERVICE_API_KEY_AUTH"
LOGGING_INTROSPECT_SERVICE_API_KEY=$(rand 48)

# server_service envelope encryption
SERVER_ENCRYPTION_KEY=$(rand_b64 32)
SERVER_ENCRYPTION_KEY_VERSION=2
HKDF_SALT_HEX=$(rand_hex 16)

# Redis-stash envelope encryption (общий между server_service и server_worker)
# Ключ отдельный от SERVER_ENCRYPTION_KEY: тот живёт только в server_service
# (БД ciphertext'ы), этот — симметрично в обоих сервисах (provision-stash).
REDIS_STASH_ENCRYPTION_KEY=$(rand_b64 32)
REDIS_STASH_ENCRYPTION_KEY_VERSION=1

# Legacy SERVICE_API_KEY (один общий секрет для всех caller'ов; в коде
# используется как fallback если per-service SERVICE_API_KEYS не задан).
SERVICE_API_KEY=$(rand 48)

# server_service / worker service-to-service
SERVER_SERVICE_API_KEY=$(rand 48)
WORKER_SERVICE_API_KEY=$(rand 48)
WORKER_BOT_TOKEN="dbos_bot_$(rand 48)"
# Inbound SERVICE_API_KEYS-map для server_service (worker_bot — единственный
# inbound caller сегодня; формат kv-list).
SERVER_INBOUND_SERVICE_API_KEYS="worker_bot:${WORKER_BOT_TOKEN}"

# secret_service envelope encryption (HKDF_SALT_HEX переиспользуется общий)
SECRET_ENCRYPTION_KEY=$(rand_b64 32)
SECRET_ENCRYPTION_KEY_VERSION=2

# secret_service: introspect ключ для исходящих /authorization/introspect
SECRET_INTROSPECT_SERVICE_API_KEY=$(rand 48)

# secret_service: inbound s2s map. Worker и auth дёргают /internal/* для
# управления записями и cascade-revoke; server_service — для bootstrap'а
# server_account credentials. Формат kv-list.
SECRET_INBOUND_WORKER_KEY=$(rand 48)
SECRET_INBOUND_AUTH_KEY=$(rand 48)
SECRET_INBOUND_SERVER_KEY=$(rand 48)
SECRET_INBOUND_SERVICE_API_KEYS_JSON="{\"worker_bot\":\"${SECRET_INBOUND_WORKER_KEY}\",\"auth_service\":\"${SECRET_INBOUND_AUTH_KEY}\",\"server_service\":\"${SECRET_INBOUND_SERVER_KEY}\"}"
# auth_service бьёт в /internal/* secret_service под идентичностью auth_service —
# его Bearer == ключу `auth_service` из inbound-map secret_service.
SECRET_INTERNAL_API_KEY="${SECRET_INBOUND_AUTH_KEY}"

# Redis
REDIS_PASSWORD=$(rand 32)

# ── RSA private key для Docker registry token-flow ────────────────────────────
echo "→ Генерируем RSA private key для Docker registry..."
RSA_PEM=$(openssl genrsa 2048 2>/dev/null)

# ── Self-signed TLS cert для Ingress ──────────────────────────────────────────
echo "→ Генерируем self-signed TLS-сертификат для $DOMAIN..."
TMP=$(mktemp -d)
trap "rm -rf $TMP" EXIT

openssl req -x509 -nodes -newkey rsa:2048 \
    -keyout "$TMP/tls.key" \
    -out    "$TMP/tls.crt" \
    -days   365 \
    -subj   "/CN=$DOMAIN/O=DBOS Server Manager" \
    -addext "subjectAltName=${SAN_ARG}" \
    2>/dev/null

TLS_CRT_B64=$(base64 -w0 < "$TMP/tls.crt")
TLS_KEY_B64=$(base64 -w0 < "$TMP/tls.key")

# ── JSON map для loging_service (inbound) ────────────────────────────────────
LOGGING_SERVICE_API_KEYS_JSON=$(cat <<EOF
{"auth_service":"${LOGGING_SERVICE_API_KEY_AUTH}","server_service":"${LOGGING_SERVICE_API_KEY_SERVER}","config_service":"${LOGGING_SERVICE_API_KEY_CONFIG}","server_worker":"${LOGGING_SERVICE_API_KEY_WORKER}","secret_service":"${LOGGING_SERVICE_API_KEY_SECRET}"}
EOF
)

# ── JSON map для auth_service (inbound, /authorization/introspect и др.) ─────
# Caller'ы /authorization/introspect:
#   loging_service  → bearer = LOGGING_INTROSPECT_SERVICE_API_KEY
#   server_service  → bearer = SERVER_SERVICE_API_KEY (outbound SERVICE_API_KEY pod'а)
#   secret_service  → bearer = SECRET_INTROSPECT_SERVICE_API_KEY
# server_worker introspect не зовёт (только outbound audit-emit), но если в
# будущем понадобится — добавляется здесь же.
AUTH_INBOUND_SERVICE_API_KEYS_JSON=$(cat <<EOF
{"loging_service":"${LOGGING_INTROSPECT_SERVICE_API_KEY}","server_service":"${SERVER_SERVICE_API_KEY}","secret_service":"${SECRET_INTROSPECT_SERVICE_API_KEY}"}
EOF
)

# ── 20-secrets.yaml ───────────────────────────────────────────────────────────
echo "→ Пишем $SECRETS_OUT..."
{
cat <<EOF
# СГЕНЕРИРОВАНО ${TS} скриптом scripts/k8s/gen_secrets.sh
# DO NOT COMMIT. Файл попадает в .gitignore (k8s/20-secrets.yaml).

apiVersion: v1
kind: Secret
metadata:
  name: dbos-secrets
  namespace: dbos
type: Opaque
stringData:
  # Postgres
  AUTH_DB_USER: auth_user
  AUTH_DB_PASSWORD: ${AUTH_DB_PASSWORD}
  LOGGING_DB_USER: logging_user
  LOGGING_DB_PASSWORD: ${LOGGING_DB_PASSWORD}
  SERVER_DB_USER: server_user
  SERVER_DB_PASSWORD: ${SERVER_DB_PASSWORD}
  WORKER_DB_USER: worker_user
  WORKER_DB_PASSWORD: ${WORKER_DB_PASSWORD}
  SECRET_DB_USER: secret_user
  SECRET_DB_PASSWORD: ${SECRET_DB_PASSWORD}

  # auth_service
  AUTH_SECRET_KEY: ${AUTH_SECRET_KEY}

  DOCKER_RSA_PRIVATE_KEY: |
EOF
    echo "$RSA_PEM" | sed 's/^/    /'
cat <<EOF

  INITIAL_ADMIN_USERNAME: admin
  INITIAL_ADMIN_PASSWORD: ${INITIAL_ADMIN_PASSWORD}
  INITIAL_ADMIN_EMAIL: ${ADMIN_EMAIL}

  # loging_service: outbound + inbound map + introspect
  # `LOGGING_SERVICE_API_KEY` — legacy общий outbound для backward-compat
  # (если pod не получит per-caller ключ из манифеста, env будет указывать на него).
  # Production-манифесты КАЖДОГО pod'а маппят свой LOGGING_SERVICE_API_KEY_* в
  # env `LOGGING_SERVICE_API_KEY`, чтобы audit-emit шёл с identity-aware
  # bearer'ом, и `LOGGING_SERVICE_API_KEYS_JSON` на стороне loging_service'а
  # его принимал по X-Service-Identity. Сами per-caller ключи под именами
  # LOGGING_SERVICE_API_KEY_AUTH/SERVER/WORKER/SECRET.
  LOGGING_SERVICE_API_KEY: ${LOGGING_SERVICE_API_KEY}
  LOGGING_SERVICE_API_KEY_AUTH: ${LOGGING_SERVICE_API_KEY_AUTH}
  LOGGING_SERVICE_API_KEY_SERVER: ${LOGGING_SERVICE_API_KEY_SERVER}
  LOGGING_SERVICE_API_KEY_WORKER: ${LOGGING_SERVICE_API_KEY_WORKER}
  LOGGING_SERVICE_API_KEY_SECRET: ${LOGGING_SERVICE_API_KEY_SECRET}
  LOGGING_SERVICE_API_KEYS_JSON: |
    ${LOGGING_SERVICE_API_KEYS_JSON}
  LOGGING_INTROSPECT_SERVICE_API_KEY: ${LOGGING_INTROSPECT_SERVICE_API_KEY}

  # server_service: envelope encryption
  SERVER_ENCRYPTION_KEY: ${SERVER_ENCRYPTION_KEY}
  SERVER_ENCRYPTION_KEY_VERSION: "${SERVER_ENCRYPTION_KEY_VERSION}"
  HKDF_SALT_HEX: ${HKDF_SALT_HEX}

  # Redis-stash envelope encryption (mounted в server_service + server_worker)
  REDIS_STASH_ENCRYPTION_KEY: ${REDIS_STASH_ENCRYPTION_KEY}
  REDIS_STASH_ENCRYPTION_KEY_VERSION: "${REDIS_STASH_ENCRYPTION_KEY_VERSION}"

  # Legacy shared SERVICE_API_KEY (fallback при пустых per-service maps)
  SERVICE_API_KEY: ${SERVICE_API_KEY}

  # auth_service inbound: per-caller bearer'ы для /authorization/introspect.
  # Значения совпадают с outbound-ключами caller'ов (loging_service шлёт
  # LOGGING_INTROSPECT_SERVICE_API_KEY, server_service — SERVER_SERVICE_API_KEY,
  # secret_service — SECRET_INTROSPECT_SERVICE_API_KEY). Если map пустой —
  # auth_service фолбэкается на legacy single SERVICE_API_KEY.
  AUTH_INBOUND_SERVICE_API_KEYS_JSON: '${AUTH_INBOUND_SERVICE_API_KEYS_JSON}'

  # server_service: s2s
  SERVER_SERVICE_API_KEY: ${SERVER_SERVICE_API_KEY}
  SERVER_INBOUND_SERVICE_API_KEYS: '${SERVER_INBOUND_SERVICE_API_KEYS}'

  # server_worker
  WORKER_BOT_TOKEN: ${WORKER_BOT_TOKEN}
  WORKER_SERVICE_API_KEY: ${WORKER_SERVICE_API_KEY}

  # secret_service: envelope encryption (общий HKDF_SALT_HEX переиспользуется)
  SECRET_ENCRYPTION_KEY: ${SECRET_ENCRYPTION_KEY}
  SECRET_ENCRYPTION_KEY_VERSION: "${SECRET_ENCRYPTION_KEY_VERSION}"

  # secret_service: s2s (introspect + inbound map)
  SECRET_INTROSPECT_SERVICE_API_KEY: ${SECRET_INTROSPECT_SERVICE_API_KEY}
  SECRET_INBOUND_SERVICE_API_KEYS: '${SECRET_INBOUND_SERVICE_API_KEYS_JSON}'

  # auth_service → secret_service /internal/* (Bearer == ключ auth_service в inbound-map secret)
  SECRET_INTERNAL_API_KEY: ${SECRET_INTERNAL_API_KEY}

  # Redis (taskiq broker + rate-limit storage)
  REDIS_PASSWORD: ${REDIS_PASSWORD}

---
# TLS-сертификат для Traefik (Ingress endpoint: ${DOMAIN}, SAN: ${SAN_ARG}).
# Имя `dbos-ingress-tls` совпадает с `tls.secretName` в Ingress
# (k8s/50-ingress.yaml). cert-manager-shim не дёргается (мы заранее кладём
# готовый Secret), что важно для IP-режима, где cert-manager не умеет
# выпускать leaf-сертификаты под host = IP-адрес.
apiVersion: v1
kind: Secret
metadata:
  name: dbos-ingress-tls
  namespace: dbos
type: kubernetes.io/tls
data:
  tls.crt: ${TLS_CRT_B64}
  tls.key: ${TLS_KEY_B64}
EOF
} > "$SECRETS_OUT"

chmod 600 "$SECRETS_OUT"

# ── 50-ingress.yaml ───────────────────────────────────────────────────────────
echo "→ Генерируем $INGRESS_OUT из шаблона..."
if [[ "$IS_IP" -eq 1 ]]; then
    # IP-режим: HTTP-роуты Ingress'а не могут иметь host: <ip-литерал>
    # (большинство контроллеров игнорируют такой rule). Поэтому:
    #   - в tls.hosts: оставляем IP — Traefik сматчит TLS SNI; клиенты,
    #     ходящие по IP без SNI, всё равно получат cert и проверят SAN=IP.
    #   - строки `- host: __INGRESS_HOST__` склеиваем со следующей `http:`
    #     в один `- http:`, чтобы list-структура rules сохранилась и
    #     Ingress принимал любой Host header (включая Host: <ip>). Простое
    #     удаление host-строки делает http: безродным объектом и kubectl
    #     валится с BadRequest на парсинг rules.
    python3 -c '
import sys, re
src = open(sys.argv[1]).read().replace("__INGRESS_HOST__", sys.argv[2])
# `    - host: <X>\n      http:` -> `    - http:` (сохраняем дашевый отступ).
src = re.sub(
    r"^([ \t]*)-[ \t]+host:[ \t]+\S+[ \t]*\n[ \t]+http:[ \t]*$",
    lambda m: f"{m.group(1)}- http:",
    src,
    flags=re.MULTILINE,
)
sys.stdout.write(src)
' "$INGRESS_TEMPLATE" "$DOMAIN" > "$INGRESS_OUT"
else
    sed "s|__INGRESS_HOST__|${DOMAIN}|g" "$INGRESS_TEMPLATE" > "$INGRESS_OUT"
fi

# ── .env.k8s — сохраним домен для повторных запусков ──────────────────────────
echo "INGRESS_HOST=${DOMAIN}" > "$ENV_FILE"
chmod 600 "$ENV_FILE"

# ── Summary для оператора (chmod 600 в /tmp) ──────────────────────────────────
{
cat <<EOF
DBOS Server Manager — secrets summary
generated: ${TS}
domain:    ${DOMAIN}

============================================================
ADMIN (initial bootstrap, после первого старта смени пароль)
============================================================
  username: admin
  password: ${INITIAL_ADMIN_PASSWORD}
  email:    ${ADMIN_EMAIL}

============================================================
MASTER ENCRYPTION KEY (server_service)
============================================================
  SERVER_ENCRYPTION_KEY:             ${SERVER_ENCRYPTION_KEY}
  SERVER_ENCRYPTION_KEY_VERSION:     ${SERVER_ENCRYPTION_KEY_VERSION}
  HKDF_SALT_HEX:                     ${HKDF_SALT_HEX}
  REDIS_STASH_ENCRYPTION_KEY:        ${REDIS_STASH_ENCRYPTION_KEY}
  REDIS_STASH_ENCRYPTION_KEY_VERSION:${REDIS_STASH_ENCRYPTION_KEY_VERSION}

============================================================
MASTER ENCRYPTION KEY (secret_service)
============================================================
  SECRET_ENCRYPTION_KEY:         ${SECRET_ENCRYPTION_KEY}
  SECRET_ENCRYPTION_KEY_VERSION: ${SECRET_ENCRYPTION_KEY_VERSION}

============================================================
WORKER BOT TOKEN (server_worker → server_service /internal/*)
============================================================
  ${WORKER_BOT_TOKEN}

============================================================
DB passwords (per-service)
============================================================
  auth_db    / auth_user:    ${AUTH_DB_PASSWORD}
  logging_db / logging_user: ${LOGGING_DB_PASSWORD}
  server_db  / server_user:  ${SERVER_DB_PASSWORD}
  worker_db  / worker_user:  ${WORKER_DB_PASSWORD}
  secret_db  / secret_user:  ${SECRET_DB_PASSWORD}

============================================================
REDIS
============================================================
  REDIS_PASSWORD: ${REDIS_PASSWORD}

============================================================
NOTES
============================================================
* Файл со всеми секретами: ${SECRETS_OUT} (chmod 600, gitignored).
* Этот summary: ${SUMMARY_OUT} (chmod 600). Перенеси в password
  manager и удали:  shred -u ${SUMMARY_OUT}
* Ротация мастер-ключа: scripts/k8s/rotate_master_key.sh.
EOF
} > "$SUMMARY_OUT"
chmod 600 "$SUMMARY_OUT"

echo ""
echo "✓ Готово."
echo ""
echo "  Сгенерированы:"
echo "    $SECRETS_OUT       (Secret dbos-secrets + dbos-ingress-tls, chmod 600)"
echo "    $INGRESS_OUT       (Ingress + Middleware с host=$DOMAIN)"
echo "    $ENV_FILE          (домен для повторных запусков)"
echo "    $SUMMARY_OUT       (summary — admin-пароль + master-key + DB-passwords)"
echo ""
echo "  ⚠ Запомни / перенеси в password manager:"
echo "    admin / ${INITIAL_ADMIN_PASSWORD}"
echo ""
echo "  После переноса:  shred -u ${SUMMARY_OUT}"
echo ""
echo "  Self-signed cert валиден 365 дней. Перевыпуск: ${0} ${DOMAIN}"
echo ""
if [[ "$IS_IP" -eq 1 ]]; then
    echo "  Доступ — напрямую по IP, /etc/hosts не нужен:"
    echo "    curl --cacert <ca> https://${DOMAIN}/api/auth/v1/health"
    echo "  (или curl -k, если CA ещё не импортирован)."
else
    echo "  Для доступа с локальной машины пропиши в /etc/hosts:"
    echo "    <VM_IP>  ${DOMAIN}"
fi

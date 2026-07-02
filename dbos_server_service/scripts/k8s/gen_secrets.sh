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
#   scripts/k8s/gen_secrets.sh --only-tls       — перевыпустить ТОЛЬКО TLS-cert
#                                                 (срок 365 дней). admin password,
#                                                 master-keys, s2s-ключи, DB-пароли
#                                                 остаются нетронутыми. Использовать
#                                                 для ежегодного продления cert'а.
#
#   scripts/k8s/gen_secrets.sh --keystore-only  — сгенерировать ТОЛЬКО
#                                                 21-keystore-secrets.yaml из уже
#                                                 существующих master-ключей в
#                                                 20-secrets.yaml. Для миграции
#                                                 действующего деплоя на durable
#                                                 keystore без смены ключей.
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
KEYSTORE_OUT="$K8S_DIR/21-keystore-secrets.yaml"
INGRESS_OUT="$K8S_DIR/50-ingress.yaml"
INGRESS_TEMPLATE="$K8S_DIR/50-ingress.yaml.template"
ENV_FILE="$K8S_DIR/.env.k8s"
# Operator-конфиг с предсказуемыми кредами (admin-пароль, force-change и т.д.).
# Оператор копирует его из deploy.env.example и заполняет ДО make k8s-zero.
# Скрипт этот файл только читает и НИКОГДА не перезаписывает (в отличие от .env.k8s).
DEPLOY_ENV_FILE="$K8S_DIR/deploy.env"

TS="$(date -u +%Y%m%dT%H%M%SZ)"
SUMMARY_OUT="/tmp/dbos-secrets-${TS}.txt"

# ── Длины случайных секретов (единый источник истины) ─────────────────────────
# Используются ниже в rand <N> и в rotate_*.sh (там — через _rotation_helpers.sh).
# Если меняешь — обнови rotate_db_passwords.sh / rotate_redis_password.sh /
# rotate_s2s_keys.sh, чтобы новые ключи совпадали по длине с историческими.
readonly RAND_DB_PASS_LEN=32         # AUTH/LOGGING/SERVER/WORKER/SECRET_DB_PASSWORD
readonly RAND_REDIS_PASS_LEN=32      # REDIS_PASSWORD
readonly RAND_S2S_KEY_LEN=48         # *_SERVICE_API_KEY*, *_INTROSPECT_*, WORKER_BOT_TOKEN
readonly RAND_INTROSPECT_KEY_LEN=48  # *_INTROSPECT_SERVICE_API_KEY (тот же тип)
readonly RAND_ADMIN_PASS_LEN=16      # INITIAL_ADMIN_PASSWORD (короткий — оператор печатает)
readonly RAND_AUTH_SECRET_LEN=64     # AUTH_SECRET_KEY (JWT signing)
readonly RAND_MASTER_KEY_BYTES=32    # openssl rand -base64 32 → 44-char padded base64
readonly RAND_HKDF_SALT_BYTES=16     # openssl rand -hex 16

# ── Парсинг флагов ────────────────────────────────────────────────────────────
# --only-tls       — перевыпустить только TLS-cert, не трогать остальные секреты.
# --keystore-only  — сгенерировать ТОЛЬКО 21-keystore-secrets.yaml из уже
#                    существующих master-ключей в 20-secrets.yaml (для миграции
#                    действующего деплоя на durable keystore — без регенерации
#                    остальных секретов и без смены ключей).
ONLY_TLS=0
ONLY_KEYSTORE=0
ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --only-tls|--cert-only) ONLY_TLS=1; shift ;;
        --keystore-only) ONLY_KEYSTORE=1; shift ;;
        -h|--help) sed -n '2,36p' "$0"; exit 0 ;;
        --) shift; while [[ $# -gt 0 ]]; do ARGS+=("$1"); shift; done ;;
        -*) echo "ОШИБКА: неизвестный флаг: $1" >&2; exit 1 ;;
        *) ARGS+=("$1"); shift ;;
    esac
done
set -- "${ARGS[@]+"${ARGS[@]}"}"

# Записать 21-keystore-secrets.yaml из текущих значений переменных
# SERVER_ENCRYPTION_KEY[_VERSION] / SECRET_ENCRYPTION_KEY[_VERSION].
emit_keystore_file() {
    echo "→ Пишем $KEYSTORE_OUT (bootstrap durable keystore)..."
    {
cat <<EOF
# СГЕНЕРИРОВАНО ${TS} скриптом scripts/k8s/gen_secrets.sh
# DO NOT COMMIT. Применяется deploy.sh create-only (не перетирает живой keystore).
#
# Durable keystore мастер-ключей шифрования (KEYSTORE_BACKEND=k8s).
# Поля совпадают с K8sSecretKeyStore: active_version + key_v<N>.

apiVersion: v1
kind: Secret
metadata:
  name: dbos-server-encryption-keys
  namespace: dbos
  labels:
    app: server-service
type: Opaque
stringData:
  active_version: "${SERVER_ENCRYPTION_KEY_VERSION}"
  key_v${SERVER_ENCRYPTION_KEY_VERSION}: ${SERVER_ENCRYPTION_KEY}

---
apiVersion: v1
kind: Secret
metadata:
  name: dbos-secret-encryption-keys
  namespace: dbos
  labels:
    app: secret-service
type: Opaque
stringData:
  active_version: "${SECRET_ENCRYPTION_KEY_VERSION}"
  key_v${SECRET_ENCRYPTION_KEY_VERSION}: ${SECRET_ENCRYPTION_KEY}
EOF
    } > "$KEYSTORE_OUT"
    chmod 600 "$KEYSTORE_OUT"
}

# ── --keystore-only: засеять keystore-файл из существующих ключей ─────────────
if [[ "$ONLY_KEYSTORE" -eq 1 ]]; then
    if [[ ! -f "$SECRETS_OUT" ]]; then
        echo "ОШИБКА: $SECRETS_OUT не найден — неоткуда взять master-ключи." >&2
        echo "  Для свежего деплоя запусти gen_secrets.sh без флага." >&2
        exit 1
    fi
    # Вытаскиваем значения из stringData существующего 20-secrets.yaml.
    field() { awk -v k="$1" '$1==k":"{v=$2; gsub(/^"|"$/,"",v); print v; exit}' "$SECRETS_OUT"; }
    SERVER_ENCRYPTION_KEY="$(field SERVER_ENCRYPTION_KEY)"
    SERVER_ENCRYPTION_KEY_VERSION="$(field SERVER_ENCRYPTION_KEY_VERSION)"
    SECRET_ENCRYPTION_KEY="$(field SECRET_ENCRYPTION_KEY)"
    SECRET_ENCRYPTION_KEY_VERSION="$(field SECRET_ENCRYPTION_KEY_VERSION)"
    if [[ -z "$SERVER_ENCRYPTION_KEY" || -z "$SERVER_ENCRYPTION_KEY_VERSION" \
       || -z "$SECRET_ENCRYPTION_KEY" || -z "$SECRET_ENCRYPTION_KEY_VERSION" ]]; then
        echo "ОШИБКА: не нашёл SERVER/SECRET_ENCRYPTION_KEY[_VERSION] в $SECRETS_OUT." >&2
        exit 1
    fi
    if [[ -f "$KEYSTORE_OUT" ]]; then
        echo "⚠ $KEYSTORE_OUT уже существует."
        read -p "  Перезаписать файл? (живой keystore в кластере не трогается) [yes/no]: " yn
        [[ "$yn" == "yes" ]] || { echo "Отменено."; exit 0; }
    fi
    emit_keystore_file
    echo ""
    echo "✓ $KEYSTORE_OUT сгенерирован из существующих ключей."
    echo "  Применить (create-only): make k8s-deploy  (или kubectl create -f $KEYSTORE_OUT)."
    exit 0
fi

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

# Определяем тип: IP или DNS. Проверяем каждый октет ≤255, чтобы строки вроде
# "999.999.999.999" не проходили как IP и не уходили в openssl с невалидным
# SAN'ом (там ошибка получится непрозрачная).
IS_IP=0
if [[ "$DOMAIN" =~ ^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})$ ]]; then
    if (( BASH_REMATCH[1] <= 255 && BASH_REMATCH[2] <= 255 \
       && BASH_REMATCH[3] <= 255 && BASH_REMATCH[4] <= 255 )); then
        IS_IP=1
    else
        echo "ОШИБКА: '${DOMAIN}' выглядит как IPv4, но один из октетов >255." >&2
        echo "  Если это DNS-имя — оно не должно состоять только из цифр и точек." >&2
        exit 1
    fi
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

# ── --only-tls: перевыпуск ТОЛЬКО TLS-cert (без regen остальных секретов) ─────
#
# Используется оператором каждый год для продления self-signed cert'а (срок 365).
# Патчит живой Secret dbos-ingress-tls в namespace dbos через kubectl.
# Не трогает k8s/20-secrets.yaml: admin-пароль, master-keys, s2s-ключи и DB-пароли
# остаются без изменений. После патча оператор делает:
#     kubectl -n dbos rollout restart deploy/traefik   # если traefik кеширует cert
# или просто ждёт пока ingress-controller подхватит новый Secret.
if [[ "$ONLY_TLS" -eq 1 ]]; then
    echo "→ Режим --only-tls: перевыпуск только TLS-cert (365 дней)."
    echo "  DOMAIN=${DOMAIN}, SAN=${SAN_ARG}."
    echo "  Остальные секреты (admin/master-keys/s2s/DB) НЕ затрагиваются."
    echo ""

    if ! command -v kubectl >/dev/null 2>&1; then
        echo "ОШИБКА: --only-tls требует kubectl (патчит Secret в cluster'е)." >&2
        exit 1
    fi

    TMP_TLS=$(mktemp -d)
    trap "rm -rf $TMP_TLS" EXIT

    openssl req -x509 -nodes -newkey rsa:2048 \
        -keyout "$TMP_TLS/tls.key" \
        -out    "$TMP_TLS/tls.crt" \
        -days   365 \
        -subj   "/CN=$DOMAIN/O=DBOS Server Manager" \
        -addext "subjectAltName=${SAN_ARG}" \
        2>/dev/null

    TLS_CRT_B64=$(base64 -w0 < "$TMP_TLS/tls.crt")
    TLS_KEY_B64=$(base64 -w0 < "$TMP_TLS/tls.key")

    # Бэкап старого cert'а (на случай rollback'а), потом merge-patch.
    BACKUP_FILE="/tmp/dbos-ingress-tls-backup-${TS}.yaml"
    if kubectl -n dbos get secret dbos-ingress-tls -o yaml > "$BACKUP_FILE" 2>/dev/null; then
        chmod 600 "$BACKUP_FILE"
        echo "→ Бэкап старого Secret'а dbos-ingress-tls: ${BACKUP_FILE} (chmod 600)."
    else
        echo "⚠ Старый Secret dbos-ingress-tls не найден — создам новый."
    fi

    echo "→ Patch Secret dbos-ingress-tls в namespace dbos..."
    kubectl -n dbos patch secret dbos-ingress-tls --type='merge' -p "$(jq -n \
        --arg crt "$TLS_CRT_B64" --arg key "$TLS_KEY_B64" \
        '{data: {"tls.crt": $crt, "tls.key": $key}}')" 2>/dev/null \
    || {
        # Если Secret'а ещё нет — создаём.
        kubectl -n dbos create secret tls dbos-ingress-tls \
            --cert="$TMP_TLS/tls.crt" --key="$TMP_TLS/tls.key"
    }

    echo ""
    echo "✓ TLS-cert обновлён."
    echo ""
    echo "  Срок действия — 365 дней с ${TS}."
    echo "  Если ingress-controller кеширует cert — kubectl -n dbos rollout restart deploy/traefik."
    echo "  Rollback: kubectl apply -f ${BACKUP_FILE}"
    exit 0
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

# ── Operator-конфиг deploy.env ────────────────────────────────────────────────
# Сорсим ПОСЛЕ .env.k8s, отдельным блоком. Даёт оператору задать предсказуемые
# значения (INITIAL_ADMIN_USERNAME/PASSWORD, DBOS_BOOTSTRAP_FORCE_PASSWORD_CHANGE,
# при желании — WORKER_BOT_TOKEN, *_DB_PASSWORD, master-ключи) ДО генерации.
# Всё, что оператор не задал, ниже генерится как раньше. Файла нет — пропускаем.
if [[ -f "$DEPLOY_ENV_FILE" ]]; then
    echo "→ Читаю operator-конфиг $DEPLOY_ENV_FILE"
    # shellcheck source=/dev/null
    . "$DEPLOY_ENV_FILE"
fi

# force-change при первом входе admin'а. Оператор задаёт человекочитаемый флаг
# DBOS_BOOTSTRAP_FORCE_PASSWORD_CHANGE (default true); в env сервиса кладётся
# инвертированный DBOS_BOOTSTRAP_NO_FORCE_CHANGE, который читает bootstrap_service.
if [[ "${DBOS_BOOTSTRAP_FORCE_PASSWORD_CHANGE:-true}" == "false" ]]; then
    DBOS_BOOTSTRAP_NO_FORCE_CHANGE="true"
else
    DBOS_BOOTSTRAP_NO_FORCE_CHANGE="false"
fi

# ── Генератор случайных строк ─────────────────────────────────────────────────
rand() {
    local n=$1
    LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom 2>/dev/null | head -c "$n" || true
}
rand_b64() { openssl rand -base64 "$1" | tr -d '\n'; }
rand_hex() { openssl rand -hex "$1"; }

# Postgres credentials (per-service). Оператор может запиннить любой из паролей
# в deploy.env; пустое значение → генерится случайный, как раньше.
AUTH_DB_PASSWORD="${AUTH_DB_PASSWORD:-$(rand "$RAND_DB_PASS_LEN")}"
LOGGING_DB_PASSWORD="${LOGGING_DB_PASSWORD:-$(rand "$RAND_DB_PASS_LEN")}"
SERVER_DB_PASSWORD="${SERVER_DB_PASSWORD:-$(rand "$RAND_DB_PASS_LEN")}"
WORKER_DB_PASSWORD="${WORKER_DB_PASSWORD:-$(rand "$RAND_DB_PASS_LEN")}"
SECRET_DB_PASSWORD="${SECRET_DB_PASSWORD:-$(rand "$RAND_DB_PASS_LEN")}"

# auth_service. INITIAL_ADMIN_USERNAME/PASSWORD оператор задаёт в deploy.env,
# чтобы знать креды заранее; пусто → admin + случайный 16-симв. пароль.
AUTH_SECRET_KEY="${AUTH_SECRET_KEY:-$(rand "$RAND_AUTH_SECRET_LEN")}"
INITIAL_ADMIN_USERNAME="${INITIAL_ADMIN_USERNAME:-admin}"
INITIAL_ADMIN_PASSWORD="${INITIAL_ADMIN_PASSWORD:-$(rand "$RAND_ADMIN_PASS_LEN")}"

# loging_service service-to-service
LOGGING_SERVICE_API_KEY_AUTH=$(rand "$RAND_S2S_KEY_LEN")
LOGGING_SERVICE_API_KEY_SERVER=$(rand "$RAND_S2S_KEY_LEN")
LOGGING_SERVICE_API_KEY_CONFIG=$(rand "$RAND_S2S_KEY_LEN")
LOGGING_SERVICE_API_KEY_WORKER=$(rand "$RAND_S2S_KEY_LEN")
LOGGING_SERVICE_API_KEY_SECRET=$(rand "$RAND_S2S_KEY_LEN")
# loging_service single outbound для backward-compat (caller'ы пока используют
# одно поле; map выше — для inbound key-separation в loging_service Settings).
LOGGING_SERVICE_API_KEY="$LOGGING_SERVICE_API_KEY_AUTH"
LOGGING_INTROSPECT_SERVICE_API_KEY=$(rand "$RAND_INTROSPECT_KEY_LEN")

# server_service envelope encryption. Master-ключи оператор обычно НЕ пиннит
# (deploy.env оставляет пустыми) — генерятся здесь. Запиннить можно для
# восстановления деплоя из бэкапа ключей.
SERVER_ENCRYPTION_KEY="${SERVER_ENCRYPTION_KEY:-$(rand_b64 "$RAND_MASTER_KEY_BYTES")}"
SERVER_ENCRYPTION_KEY_VERSION=2
HKDF_SALT_HEX="${HKDF_SALT_HEX:-$(rand_hex "$RAND_HKDF_SALT_BYTES")}"

# Redis-stash envelope encryption (общий между server_service и server_worker)
# Ключ отдельный от SERVER_ENCRYPTION_KEY: тот живёт только в server_service
# (БД ciphertext'ы), этот — симметрично в обоих сервисах (provision-stash).
REDIS_STASH_ENCRYPTION_KEY="${REDIS_STASH_ENCRYPTION_KEY:-$(rand_b64 "$RAND_MASTER_KEY_BYTES")}"
REDIS_STASH_ENCRYPTION_KEY_VERSION=1

# Legacy SERVICE_API_KEY (один общий секрет для всех caller'ов; в коде
# используется как fallback если per-service SERVICE_API_KEYS не задан).
SERVICE_API_KEY=$(rand "$RAND_S2S_KEY_LEN")

# server_service / worker service-to-service
SERVER_SERVICE_API_KEY=$(rand "$RAND_S2S_KEY_LEN")
WORKER_SERVICE_API_KEY=$(rand "$RAND_S2S_KEY_LEN")
WORKER_BOT_TOKEN="${WORKER_BOT_TOKEN:-dbos_bot_$(rand "$RAND_S2S_KEY_LEN")}"
# rotation_runner identity — ключ, под которым CronJob rotation-scheduler ходит
# в /internal/migration_status для гейтинга `--auto-finalize` master-ротаций.
ROTATION_RUNNER_API_KEY=$(rand "$RAND_S2S_KEY_LEN")
# Inbound SERVICE_API_KEYS-map для server_service: worker_bot + rotation_runner.
# Формат kv-list.
SERVER_INBOUND_SERVICE_API_KEYS="worker_bot:${WORKER_BOT_TOKEN},rotation_runner:${ROTATION_RUNNER_API_KEY}"

# secret_service envelope encryption (HKDF_SALT_HEX переиспользуется общий)
SECRET_ENCRYPTION_KEY="${SECRET_ENCRYPTION_KEY:-$(rand_b64 "$RAND_MASTER_KEY_BYTES")}"
SECRET_ENCRYPTION_KEY_VERSION=2

# secret_service: introspect ключ для исходящих /authorization/introspect
SECRET_INTROSPECT_SERVICE_API_KEY=$(rand "$RAND_INTROSPECT_KEY_LEN")

# secret_service: inbound s2s map. Worker и auth дёргают /internal/* для
# управления записями и cascade-revoke; server_service — для bootstrap'а
# server_account credentials. Формат kv-list.
SECRET_INBOUND_WORKER_KEY=$(rand "$RAND_S2S_KEY_LEN")
SECRET_INBOUND_AUTH_KEY=$(rand "$RAND_S2S_KEY_LEN")
SECRET_INBOUND_SERVER_KEY=$(rand "$RAND_S2S_KEY_LEN")
SECRET_INBOUND_SERVICE_API_KEYS_JSON="{\"worker_bot\":\"${SECRET_INBOUND_WORKER_KEY}\",\"auth_service\":\"${SECRET_INBOUND_AUTH_KEY}\",\"server_service\":\"${SECRET_INBOUND_SERVER_KEY}\",\"rotation_runner\":\"${ROTATION_RUNNER_API_KEY}\"}"
# auth_service бьёт в /internal/* secret_service под идентичностью auth_service —
# его Bearer == ключу `auth_service` из inbound-map secret_service.
SECRET_INTERNAL_API_KEY="${SECRET_INBOUND_AUTH_KEY}"

# Redis
REDIS_PASSWORD=$(rand "$RAND_REDIS_PASS_LEN")

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

  INITIAL_ADMIN_USERNAME: ${INITIAL_ADMIN_USERNAME}
  INITIAL_ADMIN_PASSWORD: ${INITIAL_ADMIN_PASSWORD}
  INITIAL_ADMIN_EMAIL: ${ADMIN_EMAIL}
  # Отключение форс-смены пароля admin при первом входе. "true" ← оператор
  # задал в deploy.env DBOS_BOOTSTRAP_FORCE_PASSWORD_CHANGE=false (admin входит
  # под своим паролем, smoke его не перетирает). Дефолт "false" = форс включён.
  DBOS_BOOTSTRAP_NO_FORCE_CHANGE: "${DBOS_BOOTSTRAP_NO_FORCE_CHANGE}"

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

  # CronJob rotation-scheduler → server/secret /internal/migration_status
  # (Bearer == ключ rotation_runner в inbound-map'ах server и secret сервисов).
  ROTATION_RUNNER_API_KEY: ${ROTATION_RUNNER_API_KEY}

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

# ── 21-keystore-secrets.yaml — durable keystore bootstrap ─────────────────────
#
# server_service и secret_service в prod крутятся с KEYSTORE_BACKEND=k8s: master-
# материал шифрования живёт не в env, а в отдельных k8s Secret'ах (durable, его
# патчит сервис на rotate). Bootstrap-Secret'ы засевают НАЧАЛЬНУЮ активную
# версию из тех же ключей, что выше (SERVER_ENCRYPTION_KEY / SECRET_ENCRYPTION_KEY),
# чтобы первый старт не падал на пустом keystore.
#
# Формат полей точно совпадает с K8sSecretKeyStore (core/keystore.py):
#   active_version  — номер активной версии (строкой)
#   key_v<N>        — master-материал версии N (та же base64-строка, что в env;
#                     сервис скармливает её в HKDF как есть, без декода).
#
# ВАЖНО про идемпотентность: эти Secret'ы НЕ включены в kustomization и
# применяются deploy.sh'ем строго create-only (kubectl create, не apply). Живой
# keystore в кластере (с уже ротированными key_v<N> и сдвинутым active_version)
# НИКОГДА не перетирается этим файлом — иначе потеря ключей и недешифруемые
# ciphertext'ы. Полная замена ключа делается только rotate_*.sh либо пересозданием
# namespace (k8s-destroy → re-deploy).
emit_keystore_file

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
  username: ${INITIAL_ADMIN_USERNAME}
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
echo "    $KEYSTORE_OUT  (bootstrap keystore: dbos-server/secret-encryption-keys, create-only)"
echo "    $INGRESS_OUT       (Ingress + Middleware с host=$DOMAIN)"
echo "    $ENV_FILE          (домен для повторных запусков)"
echo "    $SUMMARY_OUT       (summary — admin-пароль + master-key + DB-passwords)"
echo ""
echo "  ⚠ Запомни / перенеси в password manager:"
echo "    ${INITIAL_ADMIN_USERNAME} / ${INITIAL_ADMIN_PASSWORD}"
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

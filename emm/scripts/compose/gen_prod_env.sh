#!/usr/bin/env bash
# Генерация .env.prod для prod-подобного docker-compose стека (make prod-up).
#
# Аналог scripts/k8s/gen_secrets.sh, но пишет плоский dotenv-файл (KEY=VALUE),
# который docker-compose.prod.yml читает через `--env-file .env.prod`. Все
# случайные секреты — те же длины и типы, что в k8s-генераторе.
#
# Идемпотентность: если .env.prod уже есть — НЕ перетираем (пароли БД, мастер-
# ключи шифрования и admin-пароль персистятся вместе с volume'ами). Для полной
# регенерации оператор удаляет .env.prod вручную (это обнулит admin-пароль и
# сделает недешифруемыми уже зашифрованные секреты, если БД сохранилась).
#
# Использование:
#   scripts/compose/gen_prod_env.sh            — сгенерировать, если файла нет
#   ENV_OUT=/path/.env.prod scripts/compose/gen_prod_env.sh
#
# Внутри:
#   openssl rand -base64 32 — мастер-ключи шифрования (44-симв. base64)
#   openssl rand -hex 16    — HKDF salt
#   tr-pool A-Za-z0-9       — пароли БД, s2s-ключи, admin-пароль (URL-safe)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
ENV_OUT="${ENV_OUT:-$ROOT_DIR/.env.prod}"

if [[ -f "$ENV_OUT" ]]; then
    echo "→ $ENV_OUT уже существует — оставляю как есть (персист секретов)."
    echo "  Для полной регенерации: rm $ENV_OUT (обнулит admin-пароль и мастер-ключи)."
    exit 0
fi

# ── Длины случайных секретов (тот же источник истины, что в gen_secrets.sh) ────
readonly RAND_DB_PASS_LEN=32
readonly RAND_REDIS_PASS_LEN=32
readonly RAND_S2S_KEY_LEN=48
readonly RAND_ADMIN_PASS_LEN=24     # ≥12 по prod-политике; берём с запасом
readonly RAND_AUTH_SECRET_LEN=64
readonly RAND_MASTER_KEY_BYTES=32
readonly RAND_HKDF_SALT_BYTES=16

rand()     { LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom 2>/dev/null | head -c "$1" || true; }
rand_b64() { openssl rand -base64 "$1" | tr -d '\n'; }
rand_hex() { openssl rand -hex "$1"; }

# ── Пароли БД (per-service) + Redis ───────────────────────────────────────────
AUTH_DB_PASSWORD="$(rand "$RAND_DB_PASS_LEN")"
LOGGING_DB_PASSWORD="$(rand "$RAND_DB_PASS_LEN")"
SERVER_DB_PASSWORD="$(rand "$RAND_DB_PASS_LEN")"
WORKER_DB_PASSWORD="$(rand "$RAND_DB_PASS_LEN")"
SECRET_DB_PASSWORD="$(rand "$RAND_DB_PASS_LEN")"
TESTING_DB_PASSWORD="$(rand "$RAND_DB_PASS_LEN")"
REDIS_PASSWORD="$(rand "$RAND_REDIS_PASS_LEN")"

# ── auth_service ──────────────────────────────────────────────────────────────
AUTH_SECRET_KEY="$(rand "$RAND_AUTH_SECRET_LEN")"
INITIAL_ADMIN_USERNAME="${INITIAL_ADMIN_USERNAME:-account_admin}"
INITIAL_ADMIN_PASSWORD="$(rand "$RAND_ADMIN_PASS_LEN")"
INITIAL_ADMIN_EMAIL="${INITIAL_ADMIN_EMAIL:-admin@dbos.local}"
# Форс-смена пароля admin при первом входе включена (не выставляем no-force).
DBOS_BOOTSTRAP_NO_FORCE_CHANGE="false"

# ── loging_service: per-caller ключи + inbound map + introspect ───────────────
LOGGING_SERVICE_API_KEY_AUTH="$(rand "$RAND_S2S_KEY_LEN")"
LOGGING_SERVICE_API_KEY_SERVER="$(rand "$RAND_S2S_KEY_LEN")"
LOGGING_SERVICE_API_KEY_CONFIG="$(rand "$RAND_S2S_KEY_LEN")"
LOGGING_SERVICE_API_KEY_WORKER="$(rand "$RAND_S2S_KEY_LEN")"
LOGGING_SERVICE_API_KEY_SECRET="$(rand "$RAND_S2S_KEY_LEN")"
LOGGING_SERVICE_API_KEY_TESTING="$(rand "$RAND_S2S_KEY_LEN")"
LOGGING_INTROSPECT_SERVICE_API_KEY="$(rand "$RAND_S2S_KEY_LEN")"

# ── server_service: envelope + redis-stash ────────────────────────────────────
SERVER_ENCRYPTION_KEY="$(rand_b64 "$RAND_MASTER_KEY_BYTES")"
SERVER_ENCRYPTION_KEY_VERSION=2
HKDF_SALT_HEX="$(rand_hex "$RAND_HKDF_SALT_BYTES")"
REDIS_STASH_ENCRYPTION_KEY="$(rand_b64 "$RAND_MASTER_KEY_BYTES")"
REDIS_STASH_ENCRYPTION_KEY_VERSION=1

# ── testing_service: Redis-стэш кред тестового пользователя ───────────────────
CREDS_STASH_ENCRYPTION_KEY="$(rand_b64 "$RAND_MASTER_KEY_BYTES")"
CREDS_STASH_ENCRYPTION_KEY_VERSION=1

# ── Legacy shared SERVICE_API_KEY (fallback при пустых per-service map'ах) ─────
SERVICE_API_KEY="$(rand "$RAND_S2S_KEY_LEN")"

# ── server_service / worker s2s ───────────────────────────────────────────────
SERVER_SERVICE_API_KEY="$(rand "$RAND_S2S_KEY_LEN")"
WORKER_SERVICE_API_KEY="$(rand "$RAND_S2S_KEY_LEN")"
WORKER_BOT_TOKEN="dbos_bot_$(rand "$RAND_S2S_KEY_LEN")"
ROTATION_RUNNER_API_KEY="$(rand "$RAND_S2S_KEY_LEN")"
# Бронь стенда от имени testing_service (acquire-for-service/release-for-
# service/service-status/prepare-for-test, §5.1/§5.2 плана миграции).
# Bearer == SERVER_SERVICE_INTERNAL_API_KEY у testing_service ниже.
SERVER_SERVICE_INTERNAL_API_KEY="$(rand "$RAND_S2S_KEY_LEN")"
SERVER_INBOUND_SERVICE_API_KEYS="worker_bot:${WORKER_BOT_TOKEN},rotation_runner:${ROTATION_RUNNER_API_KEY},testing_service:${SERVER_SERVICE_INTERNAL_API_KEY}"

# ── secret_service ────────────────────────────────────────────────────────────
SECRET_ENCRYPTION_KEY="$(rand_b64 "$RAND_MASTER_KEY_BYTES")"
SECRET_ENCRYPTION_KEY_VERSION=2
SECRET_INTROSPECT_SERVICE_API_KEY="$(rand "$RAND_S2S_KEY_LEN")"
SECRET_INBOUND_WORKER_KEY="$(rand "$RAND_S2S_KEY_LEN")"
SECRET_INBOUND_AUTH_KEY="$(rand "$RAND_S2S_KEY_LEN")"
SECRET_INBOUND_SERVER_KEY="$(rand "$RAND_S2S_KEY_LEN")"
SECRET_INBOUND_SERVICE_API_KEYS="{\"worker_bot\":\"${SECRET_INBOUND_WORKER_KEY}\",\"auth_service\":\"${SECRET_INBOUND_AUTH_KEY}\",\"server_service\":\"${SECRET_INBOUND_SERVER_KEY}\",\"rotation_runner\":\"${ROTATION_RUNNER_API_KEY}\"}"
# auth_service бьёт в secret_service /internal/* под идентичностью auth_service.
SECRET_INTERNAL_API_KEY="${SECRET_INBOUND_AUTH_KEY}"

# ── testing_service ───────────────────────────────────────────────────────────
TESTING_INTROSPECT_SERVICE_API_KEY="$(rand "$RAND_S2S_KEY_LEN")"
TESTING_INBOUND_AUTH_KEY="$(rand "$RAND_S2S_KEY_LEN")"
# server_service шлёт сюда callback завершения prepare-for-test (§5.1 плана
# миграции). Bearer == TESTING_SERVICE_API_KEY у server_service выше.
TESTING_INBOUND_SERVER_KEY="$(rand "$RAND_S2S_KEY_LEN")"
TESTING_SERVICE_API_KEY="${TESTING_INBOUND_SERVER_KEY}"
# testing_worker забирает готовые элементы очереди и отчитывается об исходе
# (§5.5 плана миграции, /internal/queue/claim и /completed).
TESTING_INBOUND_WORKER_KEY="$(rand "$RAND_S2S_KEY_LEN")"
TESTING_SERVICE_INTERNAL_API_KEY="${TESTING_INBOUND_WORKER_KEY}"
TESTING_INBOUND_SERVICE_API_KEYS="{\"auth_service\":\"${TESTING_INBOUND_AUTH_KEY}\",\"server_service\":\"${TESTING_INBOUND_SERVER_KEY}\",\"testing_worker\":\"${TESTING_INBOUND_WORKER_KEY}\"}"
# Бот testing_service (auth_service заводит его на старте, роль
# guest@server_service) — нужен choices_source dynamic-резолверам, чтобы
# читать каталог OS-версий у server_service (introspect-based, не whitelist).
TESTING_SERVICE_BOT_TOKEN="dbos_bot_$(rand "$RAND_S2S_KEY_LEN")"

# ── inbound JSON map'ы ────────────────────────────────────────────────────────
LOGGING_SERVICE_API_KEYS_JSON="{\"auth_service\":\"${LOGGING_SERVICE_API_KEY_AUTH}\",\"server_service\":\"${LOGGING_SERVICE_API_KEY_SERVER}\",\"config_service\":\"${LOGGING_SERVICE_API_KEY_CONFIG}\",\"server_worker\":\"${LOGGING_SERVICE_API_KEY_WORKER}\",\"secret_service\":\"${LOGGING_SERVICE_API_KEY_SECRET}\",\"testing_service\":\"${LOGGING_SERVICE_API_KEY_TESTING}\"}"
AUTH_INBOUND_SERVICE_API_KEYS_JSON="{\"loging_service\":\"${LOGGING_INTROSPECT_SERVICE_API_KEY}\",\"server_service\":\"${SERVER_SERVICE_API_KEY}\",\"secret_service\":\"${SECRET_INTROSPECT_SERVICE_API_KEY}\",\"testing_service\":\"${TESTING_INTROSPECT_SERVICE_API_KEY}\"}"

# ── RSA private key для Docker registry token-flow (auth_service, prod-обязателен) ─
echo "→ Генерирую RSA private key для Docker registry..."
DOCKER_RSA_PRIVATE_KEY="$(openssl genrsa 2048 2>/dev/null)"

# ── Пишем .env.prod ───────────────────────────────────────────────────────────
# Значения с JSON/спецсимволами — в одинарных кавычках (compose-go dotenv их
# снимает и НЕ интерполирует внутри). Многострочный PEM — в двойных кавычках,
# compose-go dotenv поддерживает многострочные quoted-значения.
echo "→ Пишу $ENV_OUT..."
umask 077
{
cat <<EOF
# СГЕНЕРИРОВАНО $(date -u +%Y-%m-%dT%H:%M:%SZ) скриптом scripts/compose/gen_prod_env.sh
# DO NOT COMMIT. Файл в .gitignore. Персистится вместе с volume'ами стека.

#══════════════════════════════════════════════════════════════════════════════
# ОПЕРАТОРСКИЕ НАСТРОЙКИ — правь здесь. Ниже (Postgres/Redis/ключи) — сгенери-
# рованные секреты, их обычно трогать не нужно.
#══════════════════════════════════════════════════════════════════════════════

# ── Публичный адрес и порт ────────────────────────────────────────────────────
# DBOS_PUBLIC_HOST — hostname или IP, по которому стек доступен снаружи. Идёт в
#   SAN TLS-сертификата (make prepare-docker) и в ссылку доступа. Пусто = localhost.
# DBOS_HTTPS_PORT — внешний порт reverse-proxy (внутри контейнера всегда 443).
DBOS_PUBLIC_HOST=${DBOS_PUBLIC_HOST:-${PROD_HOST:-${PROD_IP:-}}}
DBOS_HTTPS_PORT=${DBOS_HTTPS_PORT:-443}

# ── Первый администратор платформы ────────────────────────────────────────────
# Заводится auth_service при первом старте. Пароль сгенерирован — при желании
# замени (сменить рекомендуется при первом входе). NO_FORCE_CHANGE=true отключает
# принудительную смену пароля на первом логине.
INITIAL_ADMIN_USERNAME=${INITIAL_ADMIN_USERNAME}
INITIAL_ADMIN_PASSWORD=${INITIAL_ADMIN_PASSWORD}
INITIAL_ADMIN_EMAIL=${INITIAL_ADMIN_EMAIL}
DBOS_BOOTSTRAP_NO_FORCE_CHANGE=${DBOS_BOOTSTRAP_NO_FORCE_CHANGE}

# ── Начальная парольная политика server-аккаунтов (перенастраивается в UI) ────
# Дефолт «из коробки» до того, как account_admin задаст политику в UI
# (PUT /admin/password-policy → значение ложится в БД и перекрывает эти env).
# Это политика паролей server-аккаунтов / IPMI-кред, НЕ пароля админа выше.
PASSWORD_POLICY_MIN_LENGTH=${PASSWORD_POLICY_MIN_LENGTH:-8}
PASSWORD_POLICY_REQUIRE_LETTER=${PASSWORD_POLICY_REQUIRE_LETTER:-true}
PASSWORD_POLICY_REQUIRE_DIGIT=${PASSWORD_POLICY_REQUIRE_DIGIT:-true}

# ── Начальная парольная политика ЛОГИНА (auth) — перенастраивается в UI ────────
# Применяется на ПЕРВОМ запуске (пишется в БД), дальше правится в UI account_admin.
# INITIAL_ADMIN_PASSWORD этой политике НЕ подчиняется (жёсткий guard = 12 символов).
AUTH_PASSWORD_POLICY_MIN_LENGTH=${AUTH_PASSWORD_POLICY_MIN_LENGTH:-12}
AUTH_PASSWORD_POLICY_REQUIRE_LETTER=${AUTH_PASSWORD_POLICY_REQUIRE_LETTER:-true}
AUTH_PASSWORD_POLICY_REQUIRE_DIGIT=${AUTH_PASSWORD_POLICY_REQUIRE_DIGIT:-true}

#══════════════════════════════════════════════════════════════════════════════
# Ниже — сгенерированные секреты (обычно не трогаем).
#══════════════════════════════════════════════════════════════════════════════

# ── Postgres (per-service) ────────────────────────────────────────────────────
AUTH_DB_USER=auth_user
AUTH_DB_PASSWORD=${AUTH_DB_PASSWORD}
LOGGING_DB_USER=logging_user
LOGGING_DB_PASSWORD=${LOGGING_DB_PASSWORD}
SERVER_DB_USER=server_user
SERVER_DB_PASSWORD=${SERVER_DB_PASSWORD}
WORKER_DB_USER=worker_user
WORKER_DB_PASSWORD=${WORKER_DB_PASSWORD}
SECRET_DB_USER=secret_user
SECRET_DB_PASSWORD=${SECRET_DB_PASSWORD}
TESTING_DB_USER=testing_user
TESTING_DB_PASSWORD=${TESTING_DB_PASSWORD}

# ── Redis ─────────────────────────────────────────────────────────────────────
REDIS_PASSWORD=${REDIS_PASSWORD}

# ── auth_service ──────────────────────────────────────────────────────────────
# (INITIAL_ADMIN_* и DBOS_BOOTSTRAP_NO_FORCE_CHANGE — в операторском блоке выше)
AUTH_SECRET_KEY=${AUTH_SECRET_KEY}
DOCKER_RSA_PRIVATE_KEY="${DOCKER_RSA_PRIVATE_KEY}"

# ── loging_service ────────────────────────────────────────────────────────────
LOGGING_SERVICE_API_KEY_AUTH=${LOGGING_SERVICE_API_KEY_AUTH}
LOGGING_SERVICE_API_KEY_SERVER=${LOGGING_SERVICE_API_KEY_SERVER}
LOGGING_SERVICE_API_KEY_CONFIG=${LOGGING_SERVICE_API_KEY_CONFIG}
LOGGING_SERVICE_API_KEY_WORKER=${LOGGING_SERVICE_API_KEY_WORKER}
LOGGING_SERVICE_API_KEY_SECRET=${LOGGING_SERVICE_API_KEY_SECRET}
LOGGING_INTROSPECT_SERVICE_API_KEY=${LOGGING_INTROSPECT_SERVICE_API_KEY}
LOGGING_SERVICE_API_KEYS_JSON='${LOGGING_SERVICE_API_KEYS_JSON}'

# ── server_service ────────────────────────────────────────────────────────────
SERVER_ENCRYPTION_KEY=${SERVER_ENCRYPTION_KEY}
SERVER_ENCRYPTION_KEY_VERSION=${SERVER_ENCRYPTION_KEY_VERSION}
HKDF_SALT_HEX=${HKDF_SALT_HEX}
REDIS_STASH_ENCRYPTION_KEY=${REDIS_STASH_ENCRYPTION_KEY}
REDIS_STASH_ENCRYPTION_KEY_VERSION=${REDIS_STASH_ENCRYPTION_KEY_VERSION}
SERVER_SERVICE_API_KEY=${SERVER_SERVICE_API_KEY}
SERVER_INBOUND_SERVICE_API_KEYS='${SERVER_INBOUND_SERVICE_API_KEYS}'
# Исходящий callback prepare-for-test → testing_service (§5.1 плана миграции).
TESTING_SERVICE_API_KEY=${TESTING_SERVICE_API_KEY}

# ── server_worker ─────────────────────────────────────────────────────────────
WORKER_BOT_TOKEN=${WORKER_BOT_TOKEN}
WORKER_SERVICE_API_KEY=${WORKER_SERVICE_API_KEY}

# ── secret_service ────────────────────────────────────────────────────────────
SECRET_ENCRYPTION_KEY=${SECRET_ENCRYPTION_KEY}
SECRET_ENCRYPTION_KEY_VERSION=${SECRET_ENCRYPTION_KEY_VERSION}
SECRET_INTROSPECT_SERVICE_API_KEY=${SECRET_INTROSPECT_SERVICE_API_KEY}
SECRET_INBOUND_SERVICE_API_KEYS='${SECRET_INBOUND_SERVICE_API_KEYS}'
SECRET_INTERNAL_API_KEY=${SECRET_INTERNAL_API_KEY}

# ── testing_service ───────────────────────────────────────────────────────────
TESTING_INTROSPECT_SERVICE_API_KEY=${TESTING_INTROSPECT_SERVICE_API_KEY}
CREDS_STASH_ENCRYPTION_KEY=${CREDS_STASH_ENCRYPTION_KEY}
CREDS_STASH_ENCRYPTION_KEY_VERSION=${CREDS_STASH_ENCRYPTION_KEY_VERSION}
TESTING_INBOUND_SERVICE_API_KEYS='${TESTING_INBOUND_SERVICE_API_KEYS}'
TESTING_SERVICE_BOT_TOKEN=${TESTING_SERVICE_BOT_TOKEN}
# Канал брони/подготовки в server_service (acquire-for-service/release-for-
# service/service-status/prepare-for-test) — отдельный от бот-токена выше.
SERVER_SERVICE_INTERNAL_API_KEY=${SERVER_SERVICE_INTERNAL_API_KEY}
# testing_worker → testing_service (/internal/queue/claim, /completed).
TESTING_SERVICE_INTERNAL_API_KEY=${TESTING_SERVICE_INTERNAL_API_KEY}

# ── Общие s2s ─────────────────────────────────────────────────────────────────
SERVICE_API_KEY=${SERVICE_API_KEY}
ROTATION_RUNNER_API_KEY=${ROTATION_RUNNER_API_KEY}
AUTH_INBOUND_SERVICE_API_KEYS_JSON='${AUTH_INBOUND_SERVICE_API_KEYS_JSON}'
EOF
} > "$ENV_OUT"
chmod 600 "$ENV_OUT"

echo ""
echo "✓ $ENV_OUT сгенерирован (chmod 600, gitignored)."
echo "  admin: ${INITIAL_ADMIN_USERNAME} / ${INITIAL_ADMIN_PASSWORD}  (сменить при первом входе)"

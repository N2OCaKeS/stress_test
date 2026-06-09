#!/usr/bin/env bash
# Ротация REDIS_STASH_ENCRYPTION_KEY (общий ключ server_service ↔ server_worker).
#
# Redis-stash — короткоживущий контур: server_service шифрует in-flight creds
# (password + ssh_private_key) для dispatch'а в Redis под TTL (обычно <1 часа),
# server_worker их читает и применяет. Persistent-данных под этим ключом
# в БД НЕТ — поэтому после max(TTL) от момента ротации старый ключ больше
# никому не нужен и его можно безопасно дропнуть.
#
# Flow:
#   1. Прочитать текущие REDIS_STASH_ENCRYPTION_KEY / _VERSION из Secret'а.
#   2. Сгенерировать новый ключ, version = current + 1.
#   3. Пропатчить Secret:
#        - REDIS_STASH_ENCRYPTION_KEY            ← new
#        - REDIS_STASH_ENCRYPTION_KEY_VERSION    ← new_version
#        - REDIS_STASH_ENCRYPTION_KEY__v<old>    ← old (TTL-цикл для in-flight
#                                                    stash'ей под старой версией)
#   4. Rolling restart server-service + server-worker (оба читают этот ключ).
#   5. Сообщение оператору: подожди max(TTL) (обычно <1 часа) и запусти
#      --finalize. Скрипт сам sleep НЕ делает.
#   6. Финализация --finalize: удалить REDIS_STASH_ENCRYPTION_KEY__v<old>
#      + rolling restart.
#   7. --status: показать active version + список legacy versions.
#
# Скрипт ИНТЕРАКТИВНЫЙ.
#
# Использование:
#   scripts/k8s/rotate_redis_stash_master_key.sh             # полный flow
#   scripts/k8s/rotate_redis_stash_master_key.sh --finalize  # drop previous-key
#   scripts/k8s/rotate_redis_stash_master_key.sh --status    # текущее состояние
#
# Требования:
#   kubectl, jq, openssl

set -euo pipefail

NS="${DBOS_NAMESPACE:-dbos}"
SECRET="${DBOS_SECRET_NAME:-dbos-secrets}"
SERVER_DEPLOY="server-service"
WORKER_DEPLOY="server-worker"

# ── Утилиты ───────────────────────────────────────────────────────────────────

confirm() {
    local prompt="$1"
    read -p "  ${prompt} [yes/no]: " yn
    [[ "$yn" == "yes" ]]
}

require_bin() {
    command -v "$1" >/dev/null 2>&1 || { echo "ОШИБКА: нужен $1 в PATH." >&2; exit 1; }
}

require_bin kubectl
require_bin jq
require_bin openssl

# Достать поле из Secret'а в открытом виде.
secret_get() {
    local key="$1"
    kubectl -n "$NS" get secret "$SECRET" -o json \
        | jq -r ".data.\"${key}\" // empty" \
        | { local b64; b64=$(cat); [[ -n "$b64" ]] && echo "$b64" | base64 -d || true; }
}

# Пропатчить одно поле Secret'а (через stringData, чтобы не возиться с base64).
secret_set_string() {
    local key="$1"; shift
    local val="$1"
    kubectl -n "$NS" patch secret "$SECRET" --type='merge' \
        -p "$(jq -n --arg k "$key" --arg v "$val" '{stringData: {($k): $v}}')"
}

secret_unset() {
    local key="$1"
    kubectl -n "$NS" patch secret "$SECRET" --type='json' \
        -p "[{\"op\":\"remove\",\"path\":\"/data/${key}\"}]" 2>/dev/null || true
}

show_status() {
    local cur ver
    cur=$(secret_get REDIS_STASH_ENCRYPTION_KEY || true)
    ver=$(secret_get REDIS_STASH_ENCRYPTION_KEY_VERSION || true)
    echo ""
    echo "Namespace:           $NS"
    echo "Secret:              $SECRET"
    echo "Текущая версия:      ${ver:-<нет>}"
    echo "REDIS_STASH_ENCRYPTION_KEY length: ${#cur} bytes"
    echo ""
    echo "Previous-keys в Secret'е (если есть):"
    kubectl -n "$NS" get secret "$SECRET" -o json \
        | jq -r '.data | keys[]' \
        | grep -E '^REDIS_STASH_ENCRYPTION_KEY__v[0-9]+$' || echo "  (нет)"
    echo ""
    echo "Persistent-данных под этим ключом НЕТ — Redis-stash живёт под TTL"
    echo "(обычно <1 часа). После полной ротации previous-key нужен только"
    echo "на TTL-цикл, потом можно дропнуть через --finalize."
    echo ""
}

# ── --status / --finalize subcommands ─────────────────────────────────────────

if [[ "${1:-}" == "--status" ]]; then
    show_status
    exit 0
fi

if [[ "${1:-}" == "--finalize" ]]; then
    echo "=== ФИНАЛИЗАЦИЯ ротации REDIS_STASH_ENCRYPTION_KEY ==="
    echo ""
    echo "Этот шаг удалит все REDIS_STASH_ENCRYPTION_KEY__v<N> из Secret'а."
    echo "ЗАПУСКАТЬ ТОЛЬКО ПОСЛЕ того, как с момента полной ротации прошёл"
    echo "max(TTL) Redis-stash'ей (обычно <1 часа), и in-flight stash'и"
    echo "под старой версией ключа уже истекли по TTL. Если запустить раньше,"
    echo "оставшиеся в Redis stash'и v<old> станут недешифруемыми и worker"
    echo "поднимет REDIS_STASH_KEY_MISSING на dispatch'е."
    echo ""
    show_status
    confirm "Подтвердить: TTL-цикл прошёл, удалить ВСЕ previous-keys?" \
        || { echo "Отменено."; exit 0; }

    PREV_KEYS=$(kubectl -n "$NS" get secret "$SECRET" -o json \
        | jq -r '.data | keys[]' \
        | grep -E '^REDIS_STASH_ENCRYPTION_KEY__v[0-9]+$' || true)

    if [[ -z "$PREV_KEYS" ]]; then
        echo "→ Previous-keys уже не в Secret'е. Готово."
        exit 0
    fi

    while read -r key; do
        [[ -z "$key" ]] && continue
        echo "→ Удаляю $key из Secret'а..."
        secret_unset "$key"
    done <<< "$PREV_KEYS"

    echo "→ Rolling restart server-service + server-worker..."
    kubectl -n "$NS" rollout restart deploy/"$SERVER_DEPLOY"
    kubectl -n "$NS" rollout restart deploy/"$WORKER_DEPLOY"
    kubectl -n "$NS" rollout status  deploy/"$SERVER_DEPLOY" --timeout=300s
    kubectl -n "$NS" rollout status  deploy/"$WORKER_DEPLOY" --timeout=300s

    echo ""
    echo "✓ Финализация завершена. Previous-keys удалены."
    exit 0
fi

# ── Полный flow ───────────────────────────────────────────────────────────────

echo "=== РОТАЦИЯ REDIS_STASH_ENCRYPTION_KEY ==="
echo ""
show_status

CUR_KEY=$(secret_get REDIS_STASH_ENCRYPTION_KEY)
CUR_VER=$(secret_get REDIS_STASH_ENCRYPTION_KEY_VERSION)

if [[ -z "$CUR_KEY" || -z "$CUR_VER" ]]; then
    echo "ОШИБКА: не нашёл REDIS_STASH_ENCRYPTION_KEY / _VERSION в Secret'е $NS/$SECRET." >&2
    exit 1
fi

NEW_VER=$((CUR_VER + 1))
NEW_KEY=$(openssl rand -base64 32 | tr -d '\n=')

TS="$(date -u +%Y%m%dT%H%M%SZ)"
SUMMARY_OUT="/tmp/dbos-rotate-redis-stash-${TS}.txt"

cat <<EOF

Будет сделано:
  1. Текущий ключ (v${CUR_VER}) переедет в REDIS_STASH_ENCRYPTION_KEY__v${CUR_VER}
     (нужен server-service/server-worker'у, чтобы добить in-flight stash'и
     под старой версией до их TTL-expiry).
  2. REDIS_STASH_ENCRYPTION_KEY = <новый>,
     REDIS_STASH_ENCRYPTION_KEY_VERSION = ${NEW_VER}.
  3. Rolling restart server-service + server-worker.
  4. Сообщение: подожди max(TTL) Redis-stash'ей (обычно <1 часа) и потом
     ${0} --finalize.

Новый мастер-ключ (СОХРАНИ — без него восстановление невозможно):

  REDIS_STASH_ENCRYPTION_KEY (v${NEW_VER}):
  ${NEW_KEY}

Дубликат в:
  ${SUMMARY_OUT}

EOF

{
    echo "DBOS redis-stash master-key rotation"
    echo "timestamp: ${TS}"
    echo "old_version: ${CUR_VER}"
    echo "new_version: ${NEW_VER}"
    echo ""
    echo "NEW REDIS_STASH_ENCRYPTION_KEY:"
    echo "${NEW_KEY}"
    echo ""
    echo "PREVIOUS REDIS_STASH_ENCRYPTION_KEY (теперь под REDIS_STASH_ENCRYPTION_KEY__v${CUR_VER}):"
    echo "${CUR_KEY}"
} > "$SUMMARY_OUT"
chmod 600 "$SUMMARY_OUT"

confirm "Продолжить ротацию?" || { echo "Отменено. Дубликат удалён."; shred -u "$SUMMARY_OUT" 2>/dev/null || rm -f "$SUMMARY_OUT"; exit 0; }

# ── Step 1: Patch Secret ──────────────────────────────────────────────────────
echo ""
echo "→ Step 1: пишу previous-ключ в REDIS_STASH_ENCRYPTION_KEY__v${CUR_VER}..."
secret_set_string "REDIS_STASH_ENCRYPTION_KEY__v${CUR_VER}" "$CUR_KEY"

echo "→ Step 2: подменяю REDIS_STASH_ENCRYPTION_KEY и REDIS_STASH_ENCRYPTION_KEY_VERSION..."
# Объединяем оба патча в один merge, чтобы не было промежуточного состояния,
# когда server_service попытался бы зашифровать новые stash'и под v_old, но
# KDF дал бы материал нового ключа.
kubectl -n "$NS" patch secret "$SECRET" --type='merge' -p "$(jq -n \
    --arg new_key "$NEW_KEY" \
    --arg new_ver "$NEW_VER" \
    '{stringData: {REDIS_STASH_ENCRYPTION_KEY: $new_key, REDIS_STASH_ENCRYPTION_KEY_VERSION: $new_ver}}')"

# ── Step 3: Rolling restart ────────────────────────────────────────────────────
echo ""
echo "→ Step 3: rolling restart server-service + server-worker..."
confirm "Рестартовать сейчас?" || { echo "Pause. Запусти руками: kubectl -n $NS rollout restart deploy/$SERVER_DEPLOY deploy/$WORKER_DEPLOY"; exit 0; }

kubectl -n "$NS" rollout restart deploy/"$SERVER_DEPLOY"
kubectl -n "$NS" rollout restart deploy/"$WORKER_DEPLOY"
kubectl -n "$NS" rollout status  deploy/"$SERVER_DEPLOY" --timeout=300s
kubectl -n "$NS" rollout status  deploy/"$WORKER_DEPLOY" --timeout=300s

# ── Step 4: Подсказка про TTL-цикл ────────────────────────────────────────────
cat <<EOF

✓ Ключ ротирован. Активная версия = v${NEW_VER}.

Сейчас server-service шифрует НОВЫЕ stash'и под v${NEW_VER} и расшифровывает
ЛЮБЫЕ существующие (v${CUR_VER} читается через REDIS_STASH_ENCRYPTION_KEY__v${CUR_VER}).

Persistent-данных под REDIS_STASH_ENCRYPTION_KEY нет — Redis-stash короткоживущий
(под TTL). Подожди max(TTL) (обычно <1 часа от момента ротации), чтобы
in-flight stash'и под v${CUR_VER} истекли по TTL, и финализируй ротацию:

    ${0} --finalize

Это удалит REDIS_STASH_ENCRYPTION_KEY__v${CUR_VER} из Secret'а и сделает
rolling restart обоих сервисов.

Резервная копия ключей: ${SUMMARY_OUT} (chmod 600).
После переноса в password manager:  shred -u ${SUMMARY_OUT}

EOF

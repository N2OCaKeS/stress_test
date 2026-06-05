#!/usr/bin/env bash
# Ротация SERVER_ENCRYPTION_KEY без потери данных.
#
# Flow:
#   1. Прочитать текущие SERVER_ENCRYPTION_KEY / _VERSION из Secret'а.
#   2. Сгенерировать новый мастер-ключ, version = current + 1.
#   3. Пропатчить Secret:
#        - SERVER_ENCRYPTION_KEY            ← new
#        - SERVER_ENCRYPTION_KEY_VERSION    ← new_version
#        - SERVER_ENCRYPTION_KEY__v<old>    ← old (для расшифровки старых строк)
#   4. Rolling restart server-service + server-worker, чтобы pods'ы перечитали Secret.
#   5. (Опционально) Запустить seed-pass `/reencrypt_outbox/seed` через kubectl exec
#      и подождать `/migration_status` → remaining=0.
#   6. После полной миграции — подтверждение + удаление SERVER_ENCRYPTION_KEY__v<old>.
#
# Скрипт ИНТЕРАКТИВНЫЙ: на каждом шаге подтверждение. НЕ silent.
#
# Использование:
#   scripts/k8s/rotate_master_key.sh                      # полный flow
#   scripts/k8s/rotate_master_key.sh --finalize           # только финальный drop previous-ключа
#   scripts/k8s/rotate_master_key.sh --status             # показать текущую версию и outbox
#
# Требования:
#   kubectl, jq, openssl

set -euo pipefail

NS="${DBOS_NAMESPACE:-dbos}"
SECRET="${DBOS_SECRET_NAME:-dbos-secrets}"
SERVER_DEPLOY="server-service"
WORKER_DEPLOY="server-worker"

# Endpoint, чтобы дёрнуть seed (заходим в pod server-service через kubectl exec).
# admin/loging_admin токеном — добывается оператором отдельно, в скрипте только
# подсказка как это сделать. Базовый путь — `/api/server/v1/reencrypt_outbox`.

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

# Пропатчить одно поле Secret'а (stringData, через kubectl patch).
secret_set_string() {
    local key="$1"; shift
    local val="$1"
    # base64 для data.field — kubectl patch принимает либо data (base64), либо
    # stringData (plain). Идём через stringData чтобы не возиться с base64.
    kubectl -n "$NS" patch secret "$SECRET" --type='merge' \
        -p "$(jq -n --arg k "$key" --arg v "$val" '{stringData: {($k): $v}}')"
}

secret_unset() {
    local key="$1"
    # JSON-patch remove из data (kubectl-сторона хранит всё в data, не stringData).
    # Если ключ ещё не в data — игнорируем 422.
    kubectl -n "$NS" patch secret "$SECRET" --type='json' \
        -p "[{\"op\":\"remove\",\"path\":\"/data/${key}\"}]" 2>/dev/null || true
}

show_status() {
    local cur ver
    cur=$(secret_get SERVER_ENCRYPTION_KEY || true)
    ver=$(secret_get SERVER_ENCRYPTION_KEY_VERSION || true)
    echo ""
    echo "Namespace:           $NS"
    echo "Secret:              $SECRET"
    echo "Текущая версия:      ${ver:-<нет>}"
    echo "SERVER_ENCRYPTION_KEY length: ${#cur} bytes"
    echo ""
    echo "Previous-keys в Secret'е (если есть):"
    kubectl -n "$NS" get secret "$SECRET" -o json \
        | jq -r '.data | keys[]' \
        | grep -E '^SERVER_ENCRYPTION_KEY__v[0-9]+$' || echo "  (нет)"
    echo ""
    echo "Чтобы посмотреть outbox/remaining — войди в pod server-service и"
    echo "дёрни /api/server/v1/migration_status admin-токеном:"
    echo "  kubectl -n $NS exec deploy/$SERVER_DEPLOY -- \\"
    echo "      curl -s -H 'Authorization: Bearer <admin-jwt>' \\"
    echo "      http://localhost:8002/api/server/v1/migration_status | jq ."
    echo ""
}

# ── --status / --finalize subcommands ─────────────────────────────────────────

if [[ "${1:-}" == "--status" ]]; then
    show_status
    exit 0
fi

if [[ "${1:-}" == "--finalize" ]]; then
    echo "=== ФИНАЛИЗАЦИЯ ротации мастер-ключа ==="
    echo ""
    echo "Этот шаг удалит все SERVER_ENCRYPTION_KEY__v<N> из Secret'а."
    echo "ЗАПУСКАТЬ ТОЛЬКО ПОСЛЕ того, как /migration_status вернул remaining=0"
    echo "и outbox пуст (pending + processing = 0). Иначе старые ciphertext'ы"
    echo "станут недешифруемыми."
    echo ""
    show_status
    confirm "Подтвердить: re-encrypt полностью завершён, удалить ВСЕ previous-keys?" \
        || { echo "Отменено."; exit 0; }

    PREV_KEYS=$(kubectl -n "$NS" get secret "$SECRET" -o json \
        | jq -r '.data | keys[]' \
        | grep -E '^SERVER_ENCRYPTION_KEY__v[0-9]+$' || true)

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
    kubectl -n "$NS" rollout status deploy/"$SERVER_DEPLOY"  --timeout=300s
    kubectl -n "$NS" rollout status deploy/"$WORKER_DEPLOY" --timeout=300s

    echo ""
    echo "✓ Финализация завершена. Previous-keys удалены."
    exit 0
fi

# ── Полный flow ───────────────────────────────────────────────────────────────

echo "=== РОТАЦИЯ SERVER_ENCRYPTION_KEY ==="
echo ""
show_status

CUR_KEY=$(secret_get SERVER_ENCRYPTION_KEY)
CUR_VER=$(secret_get SERVER_ENCRYPTION_KEY_VERSION)

if [[ -z "$CUR_KEY" || -z "$CUR_VER" ]]; then
    echo "ОШИБКА: не нашёл SERVER_ENCRYPTION_KEY / _VERSION в Secret'е $NS/$SECRET." >&2
    exit 1
fi

NEW_VER=$((CUR_VER + 1))
NEW_KEY=$(openssl rand -base64 32 | tr -d '\n=')

TS="$(date -u +%Y%m%dT%H%M%SZ)"
SUMMARY_OUT="/tmp/dbos-rotate-${TS}.txt"

cat <<EOF

Будет сделано:
  1. Текущий ключ (v${CUR_VER}) переедет в SERVER_ENCRYPTION_KEY__v${CUR_VER}
     (нужен server_service'у для on-the-fly decrypt старых столбцов).
  2. SERVER_ENCRYPTION_KEY = <новый>, SERVER_ENCRYPTION_KEY_VERSION = ${NEW_VER}.
  3. Rolling restart server-service и server-worker.
  4. Сообщение: запусти seed `/reencrypt_outbox/seed` и подожди remaining=0.
  5. После remaining=0 → ${0} --finalize

Новый мастер-ключ (СОХРАНИ — без него восстановление невозможно):

  SERVER_ENCRYPTION_KEY (v${NEW_VER}):
  ${NEW_KEY}

Дубликат в:
  ${SUMMARY_OUT}

EOF

{
    echo "DBOS master-key rotation"
    echo "timestamp: ${TS}"
    echo "old_version: ${CUR_VER}"
    echo "new_version: ${NEW_VER}"
    echo ""
    echo "NEW SERVER_ENCRYPTION_KEY:"
    echo "${NEW_KEY}"
    echo ""
    echo "PREVIOUS SERVER_ENCRYPTION_KEY (теперь под SERVER_ENCRYPTION_KEY__v${CUR_VER}):"
    echo "${CUR_KEY}"
} > "$SUMMARY_OUT"
chmod 600 "$SUMMARY_OUT"

confirm "Продолжить ротацию?" || { echo "Отменено. Дубликат удалён."; shred -u "$SUMMARY_OUT" 2>/dev/null || rm -f "$SUMMARY_OUT"; exit 0; }

# ── Step 1: Patch Secret ──────────────────────────────────────────────────────
echo ""
echo "→ Step 1: пишу previous-ключ в SERVER_ENCRYPTION_KEY__v${CUR_VER}..."
secret_set_string "SERVER_ENCRYPTION_KEY__v${CUR_VER}" "$CUR_KEY"

echo "→ Step 2: подменяю SERVER_ENCRYPTION_KEY и SERVER_ENCRYPTION_KEY_VERSION..."
# Объединяем оба патча в один merge, чтобы не было промежуточного состояния
# (новый ключ + старая версия), когда server_service попытался бы зашифровать
# новые строки под v_old, но KDF дал бы материал нового ключа.
kubectl -n "$NS" patch secret "$SECRET" --type='merge' -p "$(jq -n \
    --arg new_key "$NEW_KEY" \
    --arg new_ver "$NEW_VER" \
    '{stringData: {SERVER_ENCRYPTION_KEY: $new_key, SERVER_ENCRYPTION_KEY_VERSION: $new_ver}}')"

# ── Step 3: Rolling restart ────────────────────────────────────────────────────
echo ""
echo "→ Step 3: rolling restart server-service + server-worker..."
confirm "Рестартовать сейчас?" || { echo "Pause. Запусти руками: kubectl -n $NS rollout restart deploy/$SERVER_DEPLOY deploy/$WORKER_DEPLOY"; exit 0; }

kubectl -n "$NS" rollout restart deploy/"$SERVER_DEPLOY"
kubectl -n "$NS" rollout restart deploy/"$WORKER_DEPLOY"
kubectl -n "$NS" rollout status deploy/"$SERVER_DEPLOY"  --timeout=300s
kubectl -n "$NS" rollout status deploy/"$WORKER_DEPLOY" --timeout=300s

# ── Step 4: Подсказка про seed ─────────────────────────────────────────────────
cat <<EOF

✓ Ключ ротирован. Активная версия = v${NEW_VER}.

Сейчас server_service шифрует НОВЫЕ строки под v${NEW_VER} и расшифровывает
ЛЮБЫЕ существующие (v${CUR_VER} читается через SERVER_ENCRYPTION_KEY__v${CUR_VER}).

Дальше:

  1. Seed outbox с pending-rows (admin-токен нужен — добудь через
     /api/auth/v1/login admin / <пароль из make k8s-secrets>):

       AUTH_TOKEN=<jwt>
       kubectl -n $NS exec deploy/$SERVER_DEPLOY -- \\
           curl -s -X POST -H "Authorization: Bearer \$AUTH_TOKEN" \\
           http://localhost:8002/api/server/v1/reencrypt_outbox/seed?limit=5000

  2. Server-worker сам запустит периодическую `secrets.reencrypt_lazy`
     (см. SECRETS_REENCRYPT_* в 60-server-worker.yaml ConfigMap'е).

  3. Подожди, пока /migration_status вернёт remaining=0 и outbox пуст:

       kubectl -n $NS exec deploy/$SERVER_DEPLOY -- \\
           curl -s -H "Authorization: Bearer \$AUTH_TOKEN" \\
           http://localhost:8002/api/server/v1/migration_status | jq .

  4. Финализируй ротацию (удалит SERVER_ENCRYPTION_KEY__v${CUR_VER} из Secret'а):

       ${0} --finalize

Резервная копия ключей: ${SUMMARY_OUT} (chmod 600).
После переноса в password manager:  shred -u ${SUMMARY_OUT}

EOF

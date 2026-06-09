#!/usr/bin/env bash
# Ротация SECRET_ENCRYPTION_KEY (мастер-ключ secret_service) без потери данных.
#
# secret_service хранит credentials.secret_encrypted в собственной БД. Каждый
# ciphertext имеет version-prefix `v<N>$<nonce>$<ct>` → при ротации ключа
# существующие строки продолжают читаться через SECRET_ENCRYPTION_KEY__v<old>,
# а новые encrypt'ы идут под актуальной версией.
#
# Flow:
#   1. Прочитать текущие SECRET_ENCRYPTION_KEY / _VERSION из Secret'а.
#   2. Сгенерировать новый ключ, version = current + 1.
#   3. Пропатчить Secret:
#        - SECRET_ENCRYPTION_KEY            ← new
#        - SECRET_ENCRYPTION_KEY_VERSION    ← new_version
#        - SECRET_ENCRYPTION_KEY__v<old>    ← old (для on-the-fly decrypt'а
#                                              старых ciphertext'ов)
#   4. Rolling restart secret-service (других потребителей этого ключа нет,
#      auth/server его не читают).
#   5. !!! GAP: в secret_service пока НЕТ /reencrypt_outbox endpoint'а как
#      в server_service. Существующие строки credentials.secret_encrypted под
#      v<old> остаются на месте и читаются через SECRET_ENCRYPTION_KEY__v<old>.
#      Перешифровать их можно только либо через app-уровень (UPDATE secret
#      перезаписывает encrypted-payload активной версией ключа), либо
#      offline-скриптом, либо дождаться, пока owner всех credential'ов сам
#      ротирует пароли через UI/API. Финализация удаления __v<old> возможна
#      только когда оператор гарантировал, что v<old> ciphertext'ов не осталось.
#   6. Финализация `--finalize`: удалить SECRET_ENCRYPTION_KEY__v<old> после
#      подтверждения, что все старые ciphertext'ы перешифрованы.
#   7. `--status`: показать active version + список legacy versions в Secret.
#
# Скрипт ИНТЕРАКТИВНЫЙ: на каждом шаге подтверждение. НЕ silent.
#
# Использование:
#   scripts/k8s/rotate_secret_master_key.sh                  # полный flow
#   scripts/k8s/rotate_secret_master_key.sh --finalize       # drop previous-key
#   scripts/k8s/rotate_secret_master_key.sh --status         # текущее состояние
#
# Требования:
#   kubectl, jq, openssl

set -euo pipefail

NS="${DBOS_NAMESPACE:-dbos}"
SECRET="${DBOS_SECRET_NAME:-dbos-secrets}"
SECRET_DEPLOY="secret-service"

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
    # JSON-patch remove из data. Если ключа нет — игнорируем 422.
    kubectl -n "$NS" patch secret "$SECRET" --type='json' \
        -p "[{\"op\":\"remove\",\"path\":\"/data/${key}\"}]" 2>/dev/null || true
}

show_status() {
    local cur ver
    cur=$(secret_get SECRET_ENCRYPTION_KEY || true)
    ver=$(secret_get SECRET_ENCRYPTION_KEY_VERSION || true)
    echo ""
    echo "Namespace:           $NS"
    echo "Secret:              $SECRET"
    echo "Текущая версия:      ${ver:-<нет>}"
    echo "SECRET_ENCRYPTION_KEY length: ${#cur} bytes"
    echo ""
    echo "Previous-keys в Secret'е (если есть):"
    kubectl -n "$NS" get secret "$SECRET" -o json \
        | jq -r '.data | keys[]' \
        | grep -E '^SECRET_ENCRYPTION_KEY__v[0-9]+$' || echo "  (нет)"
    echo ""
    echo "Reencrypt-эндпоинта в secret_service НЕТ — миграция данных под"
    echo "новый ключ делается app-уровнем (UPDATE secret перезаписывает"
    echo "ciphertext активной версией) либо offline. Запускай --finalize"
    echo "только когда уверен, что v<old> ciphertext'ов в БД не осталось."
    echo ""
}

# ── --status / --finalize subcommands ─────────────────────────────────────────

if [[ "${1:-}" == "--status" ]]; then
    show_status
    exit 0
fi

if [[ "${1:-}" == "--finalize" ]]; then
    echo "=== ФИНАЛИЗАЦИЯ ротации SECRET_ENCRYPTION_KEY ==="
    echo ""
    echo "Этот шаг удалит все SECRET_ENCRYPTION_KEY__v<N> из Secret'а."
    echo "ЗАПУСКАТЬ ТОЛЬКО ПОСЛЕ того, как все credentials.secret_encrypted"
    echo "с v<old> были перешифрованы (через UPDATE или offline-миграцию)."
    echo "Иначе старые ciphertext'ы станут недешифруемыми и secret_service"
    echo "будет отвечать 500 ENCRYPTION_KEY_MISSING на чтение этих credential'ов."
    echo ""
    show_status
    confirm "Подтвердить: все старые ciphertext'ы перешифрованы, удалить ВСЕ previous-keys?" \
        || { echo "Отменено."; exit 0; }

    PREV_KEYS=$(kubectl -n "$NS" get secret "$SECRET" -o json \
        | jq -r '.data | keys[]' \
        | grep -E '^SECRET_ENCRYPTION_KEY__v[0-9]+$' || true)

    if [[ -z "$PREV_KEYS" ]]; then
        echo "→ Previous-keys уже не в Secret'е. Готово."
        exit 0
    fi

    while read -r key; do
        [[ -z "$key" ]] && continue
        echo "→ Удаляю $key из Secret'а..."
        secret_unset "$key"
    done <<< "$PREV_KEYS"

    echo "→ Rolling restart secret-service..."
    kubectl -n "$NS" rollout restart deploy/"$SECRET_DEPLOY"
    kubectl -n "$NS" rollout status  deploy/"$SECRET_DEPLOY" --timeout=300s

    echo ""
    echo "✓ Финализация завершена. Previous-keys удалены."
    exit 0
fi

# ── Полный flow ───────────────────────────────────────────────────────────────

echo "=== РОТАЦИЯ SECRET_ENCRYPTION_KEY ==="
echo ""
show_status

CUR_KEY=$(secret_get SECRET_ENCRYPTION_KEY)
CUR_VER=$(secret_get SECRET_ENCRYPTION_KEY_VERSION)

if [[ -z "$CUR_KEY" || -z "$CUR_VER" ]]; then
    echo "ОШИБКА: не нашёл SECRET_ENCRYPTION_KEY / _VERSION в Secret'е $NS/$SECRET." >&2
    exit 1
fi

NEW_VER=$((CUR_VER + 1))
NEW_KEY=$(openssl rand -base64 32 | tr -d '\n=')

TS="$(date -u +%Y%m%dT%H%M%SZ)"
SUMMARY_OUT="/tmp/dbos-rotate-secret-${TS}.txt"

cat <<EOF

Будет сделано:
  1. Текущий ключ (v${CUR_VER}) переедет в SECRET_ENCRYPTION_KEY__v${CUR_VER}
     (нужен secret_service'у для on-the-fly decrypt старых ciphertext'ов).
  2. SECRET_ENCRYPTION_KEY = <новый>, SECRET_ENCRYPTION_KEY_VERSION = ${NEW_VER}.
  3. Rolling restart secret-service.
  4. Сообщение: перешифровать старые ciphertext'ы (UPDATE secret через UI/API
     или offline-миграция) → потом ${0} --finalize.

Новый мастер-ключ (СОХРАНИ — без него восстановление невозможно):

  SECRET_ENCRYPTION_KEY (v${NEW_VER}):
  ${NEW_KEY}

Дубликат в:
  ${SUMMARY_OUT}

EOF

{
    echo "DBOS secret-service master-key rotation"
    echo "timestamp: ${TS}"
    echo "old_version: ${CUR_VER}"
    echo "new_version: ${NEW_VER}"
    echo ""
    echo "NEW SECRET_ENCRYPTION_KEY:"
    echo "${NEW_KEY}"
    echo ""
    echo "PREVIOUS SECRET_ENCRYPTION_KEY (теперь под SECRET_ENCRYPTION_KEY__v${CUR_VER}):"
    echo "${CUR_KEY}"
} > "$SUMMARY_OUT"
chmod 600 "$SUMMARY_OUT"

confirm "Продолжить ротацию?" || { echo "Отменено. Дубликат удалён."; shred -u "$SUMMARY_OUT" 2>/dev/null || rm -f "$SUMMARY_OUT"; exit 0; }

# ── Step 1: Patch Secret ──────────────────────────────────────────────────────
echo ""
echo "→ Step 1: пишу previous-ключ в SECRET_ENCRYPTION_KEY__v${CUR_VER}..."
secret_set_string "SECRET_ENCRYPTION_KEY__v${CUR_VER}" "$CUR_KEY"

echo "→ Step 2: подменяю SECRET_ENCRYPTION_KEY и SECRET_ENCRYPTION_KEY_VERSION..."
# Объединяем оба патча в один merge, чтобы не было промежуточного состояния
# (новый ключ + старая версия), когда secret_service попытался бы зашифровать
# новые строки под v_old, но KDF дал бы материал нового ключа.
kubectl -n "$NS" patch secret "$SECRET" --type='merge' -p "$(jq -n \
    --arg new_key "$NEW_KEY" \
    --arg new_ver "$NEW_VER" \
    '{stringData: {SECRET_ENCRYPTION_KEY: $new_key, SECRET_ENCRYPTION_KEY_VERSION: $new_ver}}')"

# ── Step 3: Rolling restart ────────────────────────────────────────────────────
echo ""
echo "→ Step 3: rolling restart secret-service..."
confirm "Рестартовать сейчас?" || { echo "Pause. Запусти руками: kubectl -n $NS rollout restart deploy/$SECRET_DEPLOY"; exit 0; }

kubectl -n "$NS" rollout restart deploy/"$SECRET_DEPLOY"
kubectl -n "$NS" rollout status  deploy/"$SECRET_DEPLOY" --timeout=300s

# ── Step 4: Подсказка про миграцию данных ─────────────────────────────────────
cat <<EOF

✓ Ключ ротирован. Активная версия = v${NEW_VER}.

Сейчас secret_service шифрует НОВЫЕ ciphertext'ы под v${NEW_VER} и расшифровывает
ЛЮБЫЕ существующие (v${CUR_VER} читается через SECRET_ENCRYPTION_KEY__v${CUR_VER}).

ВНИМАНИЕ: в secret_service пока нет /reencrypt_outbox endpoint'а как в
server_service, поэтому перешифровать существующие credentials.secret_encrypted
автоматически нечем. Варианты:

  a) Дождаться, пока owner всех credential'ов ротирует их сам через UI/API
     (любой UPDATE secret перезапишет ciphertext активной версией).
  b) Запустить offline-скрипт миграции (по аналогии с
     scripts/migrate_secret_outbox.py из server_service, см. CRYPTO.md).
  c) Если credential'ов мало — вручную пройти по списку.

После того как убедился, что в credentials.secret_encrypted не осталось
строк с префиксом 'v${CUR_VER}\$' — финализируй ротацию (удалит
SECRET_ENCRYPTION_KEY__v${CUR_VER} из Secret'а):

    ${0} --finalize

Резервная копия ключей: ${SUMMARY_OUT} (chmod 600).
После переноса в password manager:  shred -u ${SUMMARY_OUT}

EOF

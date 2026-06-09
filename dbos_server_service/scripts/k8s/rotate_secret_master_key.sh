#!/usr/bin/env bash
# Ротация SECRET_ENCRYPTION_KEY (мастер-ключ secret_service) без потери данных.
#
# secret_service хранит credentials.secret_encrypted в собственной БД. Каждый
# ciphertext имеет version-prefix `v<N>$<nonce>$<ct>` → при ротации ключа
# существующие строки продолжают читаться через SECRET_ENCRYPTION_KEY__v<old>,
# а новые encrypt'ы идут под актуальной версией.
#
# Lazy re-encrypt (добавлен параллельным агентом): на каждом read-path'е
# secret_service автоматически перешифровывает row под активный ключ. Прогресс
# виден через GET /api/secret/v1/internal/migration_status.
#
# Proactive outbox: после rotate можно запустить
# POST /internal/reencrypt_outbox/seed → POST /internal/reencrypt_outbox/process,
# чтобы перешифровать «холодные» credential'ы, которые никто не reveal'ит,
# и опустить migration_status.remaining_legacy до нуля быстрее. См. --seed-outbox.
#
# Использование:
#   scripts/k8s/rotate_secret_master_key.sh                  # фаза 1: rotate + restart
#   scripts/k8s/rotate_secret_master_key.sh --finalize       # фаза 2: drop __v<old>, если 100%
#   scripts/k8s/rotate_secret_master_key.sh --auto-finalize  # CronJob: finalize prev-prev + rotate
#   scripts/k8s/rotate_secret_master_key.sh --status         # текущее состояние
#   scripts/k8s/rotate_secret_master_key.sh --seed-outbox    # publish proactive re-encrypt tasks
#
# Флаги-модификаторы:
#   --yes              non-interactive (для CronJob)
#   --force-finalize   bypass migration_status (только DR/incident)
#
# Требования: kubectl, jq, openssl, curl (в pod'е secret-service)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NS="${DBOS_NAMESPACE:-dbos}"
SECRET="${DBOS_SECRET_NAME:-dbos-secrets}"
SECRET_DEPLOY="secret-service"
SECRET_PORT="8003"
MIGRATION_STATUS_PATH="/api/secret/v1/internal/migration_status"

# ── Аргументы ─────────────────────────────────────────────────────────────────
ACTION="rotate"
ASSUME_YES="false"
FORCE_FINALIZE="false"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --status)         ACTION="status";         shift ;;
        --finalize)       ACTION="finalize";       shift ;;
        --auto-finalize)  ACTION="auto-finalize";  shift ;;
        --seed-outbox)    ACTION="seed-outbox";    shift ;;
        --force-finalize) FORCE_FINALIZE="true";   shift ;;
        --yes|-y)         ASSUME_YES="true";       shift ;;
        -h|--help)        sed -n '2,30p' "$0";     exit 0 ;;
        *) echo "ОШИБКА: неизвестный аргумент: $1" >&2; exit 1 ;;
    esac
done

# ── Утилиты ───────────────────────────────────────────────────────────────────
# confirm / require_bin / secret_get / secret_set_string / secret_unset —
# в _rotation_helpers.sh.

# shellcheck source=_rotation_helpers.sh
source "$SCRIPT_DIR/_rotation_helpers.sh"

require_bin kubectl
require_bin jq
require_bin openssl

list_legacy_keys() {
    kubectl -n "$NS" get secret "$SECRET" -o json \
        | jq -r '.data | keys[]' \
        | grep -E '^SECRET_ENCRYPTION_KEY__v[0-9]+$' \
        | sort -t v -k2 -n || true
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
    list_legacy_keys | sed 's/^/  /' || echo "  (нет)"
    echo ""
}

check_migration_complete() {
    local json
    if ! json=$(fetch_migration_status_json "$SECRET_DEPLOY" "$SECRET_PORT" "$MIGRATION_STATUS_PATH"); then
        return 1
    fi
    local reason
    if reason=$(migration_status_is_complete "$json"); then
        return 0
    fi
    echo "ОШИБКА: миграция secret_service не завершена (${reason})." >&2
    echo "" >&2
    echo "Что делать:" >&2
    echo "  1. Подождать lazy re-encrypt (любое UPDATE/чтение credential'а перешифрует row)." >&2
    echo "  2. Запустить proactive seed (если такой endpoint появится в secret_service):" >&2
    echo "     kubectl -n $NS exec deploy/$SECRET_DEPLOY -- curl -s -X POST \\" >&2
    echo "         -H \"X-Service-Identity: rotation_runner\" -H \"Authorization: Bearer \$KEY\" \\" >&2
    echo "         http://localhost:${SECRET_PORT}/api/secret/v1/reencrypt_outbox/seed" >&2
    echo "  3. Удалить мёртвые credential'ы (заброшенные dept'ы) — они не читаются, не мигрируют." >&2
    return 1
}

# ── ACTION: status ────────────────────────────────────────────────────────────

if [[ "$ACTION" == "status" ]]; then
    show_status
    exit 0
fi

# ── ACTION: finalize / auto-finalize ──────────────────────────────────────────

do_finalize() {
    echo "=== FINALIZE SECRET_ENCRYPTION_KEY ==="
    echo ""
    show_status

    local prev_keys
    prev_keys=$(list_legacy_keys)
    if [[ -z "$prev_keys" ]]; then
        echo "→ Previous-keys уже не в Secret'е. Готово."
        return 0
    fi

    if [[ "$FORCE_FINALIZE" == "true" ]]; then
        confirm_force_finalize "secret-master" || { echo "Отменено."; exit 1; }
    else
        if ! check_migration_complete; then
            echo "" >&2
            echo "ОТКАЗ: --finalize без --force-finalize требует migration_status: complete." >&2
            exit 1
        fi
        echo "✓ migration_status: complete (0 legacy ciphertext'ов)."
        echo ""
        confirm "Удалить ВСЕ previous-keys из Secret'а?" || { echo "Отменено."; exit 0; }
    fi

    while read -r key; do
        [[ -z "$key" ]] && continue
        echo "→ Удаляю $key из Secret'а..."
        secret_unset "$key"
    done <<< "$prev_keys"

    echo "→ Rolling restart secret-service..."
    kubectl -n "$NS" rollout restart deploy/"$SECRET_DEPLOY"
    kubectl -n "$NS" rollout status  deploy/"$SECRET_DEPLOY" --timeout=300s

    echo ""
    echo "✓ Финализация завершена. Previous-keys удалены."
}

drop_legacy_older_than() {
    local keep_ver="$1"
    local key ver
    while read -r key; do
        [[ -z "$key" ]] && continue
        ver="${key##*__v}"
        if [[ "$ver" -lt "$keep_ver" ]]; then
            echo "→ Удаляю $key (старее __v${keep_ver})..."
            secret_unset "$key"
        fi
    done <<< "$(list_legacy_keys)"
}

if [[ "$ACTION" == "finalize" ]]; then
    do_finalize
    exit 0
fi

# ── ACTION: rotate ────────────────────────────────────────────────────────────

do_rotate() {
    local cur_key cur_ver new_ver new_key
    cur_key=$(secret_get SECRET_ENCRYPTION_KEY)
    cur_ver=$(secret_get SECRET_ENCRYPTION_KEY_VERSION)

    if [[ -z "$cur_key" || -z "$cur_ver" ]]; then
        echo "ОШИБКА: не нашёл SECRET_ENCRYPTION_KEY / _VERSION в Secret'е $NS/$SECRET." >&2
        exit 1
    fi

    new_ver=$((cur_ver + 1))
    new_key=$(openssl rand -base64 32 | tr -d '\n=')

    local ts summary_out
    ts="$(date -u +%Y%m%dT%H%M%SZ)"
    summary_out="/tmp/dbos-rotate-secret-${ts}.txt"

    cat <<EOF

Будет сделано:
  1. Текущий ключ (v${cur_ver}) переедет в SECRET_ENCRYPTION_KEY__v${cur_ver}.
  2. SECRET_ENCRYPTION_KEY = <новый>, SECRET_ENCRYPTION_KEY_VERSION = ${new_ver}.
  3. Rolling restart secret-service.
  4. Lazy re-encrypt мигрирует данные на чтении. Прогресс — --status.

EOF

    if [[ "$ASSUME_YES" != "true" ]]; then
        cat <<EOF
Новый мастер-ключ (СОХРАНИ — без него восстановление невозможно):
  ${new_key}

Дубликат в: ${summary_out}

EOF
        {
            echo "DBOS secret-service master-key rotation"
            echo "timestamp: ${ts}"
            echo "old_version: ${cur_ver}"
            echo "new_version: ${new_ver}"
            echo ""
            echo "NEW SECRET_ENCRYPTION_KEY:"
            echo "${new_key}"
            echo ""
            echo "PREVIOUS SECRET_ENCRYPTION_KEY (теперь под SECRET_ENCRYPTION_KEY__v${cur_ver}):"
            echo "${cur_key}"
        } > "$summary_out"
        chmod 600 "$summary_out"
    fi

    confirm "Продолжить ротацию?" || {
        echo "Отменено."
        [[ -f "$summary_out" ]] && { shred -u "$summary_out" 2>/dev/null || rm -f "$summary_out"; }
        return 1
    }

    echo ""
    echo "→ Пишу previous-ключ в SECRET_ENCRYPTION_KEY__v${cur_ver}..."
    secret_set_string "SECRET_ENCRYPTION_KEY__v${cur_ver}" "$cur_key"

    echo "→ Подменяю SECRET_ENCRYPTION_KEY и SECRET_ENCRYPTION_KEY_VERSION..."
    kubectl -n "$NS" patch secret "$SECRET" --type='merge' -p "$(jq -n \
        --arg new_key "$new_key" \
        --arg new_ver "$new_ver" \
        '{stringData: {SECRET_ENCRYPTION_KEY: $new_key, SECRET_ENCRYPTION_KEY_VERSION: $new_ver}}')"

    echo ""
    echo "→ Rolling restart secret-service..."
    confirm "Рестартовать сейчас?" || {
        echo "Pause. Запусти руками: kubectl -n $NS rollout restart deploy/$SECRET_DEPLOY"
        return 0
    }

    kubectl -n "$NS" rollout restart deploy/"$SECRET_DEPLOY"
    kubectl -n "$NS" rollout status  deploy/"$SECRET_DEPLOY" --timeout=300s

    cat <<EOF

✓ Ключ ротирован. Активная версия = v${new_ver}.

Lazy re-encrypt автоматически мигрирует данные под новый ключ на чтении.
Прогресс:
    $0 --status

Финализация (после migration_status: complete):
    $0 --finalize

EOF
}

if [[ "$ACTION" == "rotate" ]]; then
    echo "=== РОТАЦИЯ SECRET_ENCRYPTION_KEY (фаза 1) ==="
    show_status
    do_rotate
    cat <<EOF

(опционально) запустить proactive re-encrypt outbox для «холодных» credential'ов:
  $0 --seed-outbox

Без него lazy re-encrypt отработает только на credential'ах, которые reveal'ят.
EOF
    exit 0
fi

# ── ACTION: seed-outbox ───────────────────────────────────────────────────────

if [[ "$ACTION" == "seed-outbox" ]]; then
    echo "=== SEED REENCRYPT-OUTBOX ==="
    if [[ -z "${ROTATION_RUNNER_API_KEY:-}" ]]; then
        echo "ОШИБКА: установи переменную окружения ROTATION_RUNNER_API_KEY." >&2
        echo "    Это значение из SERVICE_API_KEYS['rotation_runner'] для secret-service." >&2
        exit 1
    fi
    echo "→ POST /internal/reencrypt_outbox/seed ..."
    kubectl -n "$NS" exec "deploy/${SECRET_DEPLOY}" -- sh -c \
        "curl -sf -X POST \
            -H 'X-Service-Identity: rotation_runner' \
            -H 'Authorization: Bearer ${ROTATION_RUNNER_API_KEY}' \
            http://localhost:${SECRET_PORT}/api/secret/v1/internal/reencrypt_outbox/seed"
    echo ""
    echo "→ POST /internal/reencrypt_outbox/process ..."
    kubectl -n "$NS" exec "deploy/${SECRET_DEPLOY}" -- sh -c \
        "curl -sf -X POST \
            -H 'X-Service-Identity: rotation_runner' \
            -H 'Authorization: Bearer ${ROTATION_RUNNER_API_KEY}' \
            'http://localhost:${SECRET_PORT}/api/secret/v1/internal/reencrypt_outbox/process?batch_size=500'"
    echo ""
    echo "→ GET /internal/reencrypt_outbox/status ..."
    kubectl -n "$NS" exec "deploy/${SECRET_DEPLOY}" -- sh -c \
        "curl -sf \
            -H 'X-Service-Identity: rotation_runner' \
            -H 'Authorization: Bearer ${ROTATION_RUNNER_API_KEY}' \
            http://localhost:${SECRET_PORT}/api/secret/v1/internal/reencrypt_outbox/status"
    echo ""
    echo ""
    echo "✓ Готово. Повтори process до pending=0 (на больших БД может потребоваться несколько раундов)."
    exit 0
fi

# ── ACTION: auto-finalize ──────────────────────────────────────────────────────

if [[ "$ACTION" == "auto-finalize" ]]; then
    echo "=== AUTO-FINALIZE SECRET_ENCRYPTION_KEY ==="
    show_status

    cur_ver=$(secret_get SECRET_ENCRYPTION_KEY_VERSION)
    if [[ -z "$cur_ver" ]]; then
        echo "ОШИБКА: SECRET_ENCRYPTION_KEY_VERSION пуст в Secret'е." >&2
        exit 1
    fi

    legacy=$(list_legacy_keys || true)

    if [[ -n "$legacy" ]]; then
        if check_migration_complete 2>/dev/null; then
            echo "✓ migration_status: complete → finalize."
            while read -r key; do
                [[ -z "$key" ]] && continue
                echo "→ Удаляю $key..."
                secret_unset "$key"
            done <<< "$legacy"
            echo "→ Rolling restart secret-service..."
            kubectl -n "$NS" rollout restart deploy/"$SECRET_DEPLOY"
            kubectl -n "$NS" rollout status  deploy/"$SECRET_DEPLOY" --timeout=300s
        else
            echo "⚠ migration_status: не complete → finalize пропускается."
            echo "  Дроплю только __v<N-2> и старее, новейший __v<N-1> сохраняю."
            drop_legacy_older_than "$cur_ver"
        fi
    else
        echo "→ Legacy keys в Secret'е нет — finalize не требуется."
    fi

    echo ""
    echo "=== ROTATE → новая версия ==="
    do_rotate

    echo ""
    echo "✓ auto-finalize завершён."
    exit 0
fi

echo "ОШИБКА: неизвестное действие: $ACTION" >&2
exit 1

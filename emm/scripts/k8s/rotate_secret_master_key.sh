#!/usr/bin/env bash
# Ротация мастер-ключа шифрования secret_service без потери данных.
#
# Ключи живут в durable keystore-Secret'е dbos-secret-encryption-keys
# (KEYSTORE_BACKEND=k8s), поля совпадают с K8sSecretKeyStore:
#   active_version  — активная версия (строкой)
#   key_v<N>        — master-материал версии N
#
# secret_service хранит credentials.secret_encrypted с version-prefix'ом
# `v<N>$<nonce>$<ct>`: при ротации старые строки читаются по key_v<old>, новые
# encrypt'ы идут под активной версией. Сервис читает keystore вживую — рестарт
# не нужен. Lazy re-encrypt мигрирует строки на чтении; прогресс —
# GET /api/secret/v1/internal/migration_status.
#
# Proactive outbox: после rotate можно запустить seed+process (--seed-outbox),
# чтобы перешифровать «холодные» credential'ы и опустить remaining_legacy до 0.
#
# Использование:
#   scripts/k8s/rotate_secret_master_key.sh                  # фаза 1: rotate
#   scripts/k8s/rotate_secret_master_key.sh --finalize       # фаза 2: retire старых версий, если 100%
#   scripts/k8s/rotate_secret_master_key.sh --auto-finalize  # CronJob: retire prev-prev + rotate
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
# Патчим keystore-Secret, а не env-Secret dbos-secrets.
SECRET="${DBOS_SECRET_NAME:-dbos-secret-encryption-keys}"
# s2s-ключи (ROTATION_RUNNER_API_KEY) лежат в dbos-secrets — отдельно от keystore.
S2S_SECRET="${DBOS_S2S_SECRET_NAME:-dbos-secrets}"
SECRET_DEPLOY="secret-service"
SECRET_PORT="8003"
MIGRATION_STATUS_PATH="/api/secret/v1/internal/migration_status"

ACTIVE_FIELD="active_version"
KEY_PREFIX="key_v"

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

active_version() {
    secret_get "$ACTIVE_FIELD"
}

keystore_versions() {
    kubectl -n "$NS" get secret "$SECRET" -o json 2>/dev/null \
        | jq -r '.data | keys[]' \
        | grep -E "^${KEY_PREFIX}[0-9]+\$" \
        | sed "s/^${KEY_PREFIX}//" \
        | sort -n || true
}

non_active_versions() {
    local active
    active="$(active_version)"
    while read -r v; do
        [[ -z "$v" ]] && continue
        [[ "$v" == "$active" ]] && continue
        echo "$v"
    done <<< "$(keystore_versions)"
}

show_status() {
    local active
    active="$(active_version || true)"
    echo ""
    echo "Namespace:           $NS"
    echo "Keystore Secret:     $SECRET"
    echo "Активная версия:     ${active:-<нет>}"
    echo "Версии в keystore (key_v<N>):"
    keystore_versions | sed 's/^/  v/' || echo "  (нет)"
    echo ""
    echo "Не-активные версии (кандидаты на retire):"
    local na
    na="$(non_active_versions)"
    if [[ -n "$na" ]]; then
        echo "$na" | sed 's/^/  v/'
    else
        echo "  (нет)"
    fi
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
    echo "  2. Запустить proactive seed:" >&2
    echo "     $0 --seed-outbox" >&2
    echo "  3. Удалить мёртвые credential'ы (заброшенные dept'ы) — они не читаются, не мигрируют." >&2
    return 1
}

retire_version() {
    local v="$1"
    echo "→ Retire v${v} (удаляю ${KEY_PREFIX}${v} из keystore)..."
    secret_unset "${KEY_PREFIX}${v}"
}

# ── ACTION: status ────────────────────────────────────────────────────────────

if [[ "$ACTION" == "status" ]]; then
    show_status
    exit 0
fi

# ── ACTION: finalize / auto-finalize ──────────────────────────────────────────

do_finalize() {
    echo "=== FINALIZE keystore secret_service ==="
    echo ""
    show_status

    local victims
    victims="$(non_active_versions)"
    if [[ -z "$victims" ]]; then
        echo "→ Не-активных версий нет. Готово."
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
        confirm "Удалить ВСЕ не-активные версии из keystore?" || { echo "Отменено."; exit 0; }
    fi

    while read -r v; do
        [[ -z "$v" ]] && continue
        retire_version "$v"
    done <<< "$victims"

    echo ""
    echo "✓ Финализация завершена. Старые версии выведены из keystore."
}

retire_older_than() {
    local floor="$1"
    local v
    while read -r v; do
        [[ -z "$v" ]] && continue
        if [[ "$v" -lt "$floor" ]]; then
            retire_version "$v"
        fi
    done <<< "$(non_active_versions)"
}

if [[ "$ACTION" == "finalize" ]]; then
    do_finalize
    exit 0
fi

# ── ACTION: rotate ────────────────────────────────────────────────────────────

do_rotate() {
    local cur_ver new_ver new_key max_ver
    cur_ver="$(active_version)"

    if [[ -z "$cur_ver" ]]; then
        echo "ОШИБКА: не нашёл ${ACTIVE_FIELD} в keystore-Secret'е $NS/$SECRET." >&2
        echo "  Keystore не засеян? См. scripts/k8s/gen_secrets.sh / deploy.sh bootstrap." >&2
        exit 1
    fi

    max_ver="$(keystore_versions | tail -n1)"
    [[ -z "$max_ver" ]] && max_ver="$cur_ver"
    new_ver=$((max_ver + 1))
    new_key=$(openssl rand -base64 32 | tr -d '\n')

    local ts summary_out
    ts="$(date -u +%Y%m%dT%H%M%SZ)"
    summary_out="/tmp/dbos-rotate-secret-${ts}.txt"

    cat <<EOF

Будет сделано:
  1. key_v${new_ver} = <новый ключ> в keystore-Secret'е $SECRET.
  2. ${ACTIVE_FIELD} = ${new_ver} (новые encrypt'ы сразу под новой версией).
  3. Прошлый key_v${cur_ver} остаётся для расшифровки старых строк.
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
            echo "old_active_version: ${cur_ver}"
            echo "new_active_version: ${new_ver}"
            echo ""
            echo "NEW key_v${new_ver}:"
            echo "${new_key}"
        } > "$summary_out"
        chmod 600 "$summary_out"
    fi

    confirm "Продолжить ротацию?" || {
        echo "Отменено."
        [[ -f "$summary_out" ]] && { shred -u "$summary_out" 2>/dev/null || rm -f "$summary_out"; }
        return 1
    }

    echo ""
    echo "→ Пишу key_v${new_ver} в keystore..."
    secret_set_string "${KEY_PREFIX}${new_ver}" "$new_key"

    echo "→ Выставляю ${ACTIVE_FIELD}=${new_ver}..."
    secret_set_string "$ACTIVE_FIELD" "$new_ver"

    cat <<EOF

✓ Ключ ротирован. Активная версия = v${new_ver}.

Durable keystore читается сервисом вживую — рестарт pod'ов не нужен.
Lazy re-encrypt мигрирует данные под новый ключ на чтении.
Прогресс:
    $0 --status

Финализация (после migration_status: complete):
    $0 --finalize

EOF
}

if [[ "$ACTION" == "rotate" ]]; then
    echo "=== РОТАЦИЯ keystore secret_service (фаза 1) ==="
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
    echo "=== AUTO-FINALIZE keystore secret_service ==="
    show_status

    cur_ver="$(active_version)"
    if [[ -z "$cur_ver" ]]; then
        echo "ОШИБКА: ${ACTIVE_FIELD} пуст в keystore-Secret'е." >&2
        exit 1
    fi

    victims="$(non_active_versions || true)"

    if [[ -n "$victims" ]]; then
        if check_migration_complete 2>/dev/null; then
            echo "✓ migration_status: complete → retire всех не-активных версий."
            while read -r v; do
                [[ -z "$v" ]] && continue
                retire_version "$v"
            done <<< "$victims"
        else
            echo "⚠ migration_status: не complete → полный retire пропускается."
            echo "  Убираю только версии глубже v$((cur_ver - 1)), active и active-1 сохраняю."
            retire_older_than "$((cur_ver - 1))"
        fi
    else
        echo "→ Не-активных версий нет — retire не требуется."
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

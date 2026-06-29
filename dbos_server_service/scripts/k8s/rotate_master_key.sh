#!/usr/bin/env bash
# Ротация мастер-ключа шифрования server_service без потери данных.
#
# Ключи живут в durable keystore-Secret'е dbos-server-encryption-keys
# (KEYSTORE_BACKEND=k8s), поля совпадают с K8sSecretKeyStore:
#   active_version  — активная версия (строкой)
#   key_v<N>        — master-материал версии N
#
# Flow (тот же контракт, что у сервисного key_rotation_service.rotate):
#   1. Прочитать active_version из keystore-Secret'а.
#   2. Сгенерировать новый ключ, new_version = max(known)+1.
#   3. Пропатчить keystore-Secret: добавить key_v<new>, выставить
#      active_version=new. Прошлый key_v<old> остаётся (dual-version read).
#   4. Сервис читает keystore вживую (durable backend) — рестарт не нужен.
#      Lazy re-encrypt перешифровывает строки активным ключом на чтении;
#      прогресс — `/internal/migration_status`.
#   5. После remaining_legacy=0 — retire старых key_v<N> (--finalize).
#
# Использование:
#   scripts/k8s/rotate_master_key.sh                  # фаза 1: rotate
#   scripts/k8s/rotate_master_key.sh --finalize       # фаза 2: retire старых версий, если migration 100%
#   scripts/k8s/rotate_master_key.sh --auto-finalize  # CronJob-режим: retire prev-prev + rotate
#   scripts/k8s/rotate_master_key.sh --status         # показать активную версию и материал
#
# Флаги-модификаторы:
#   --yes              non-interactive (для CronJob); skip confirm()
#   --force-finalize   bypass migration_status (только DR/incident, требует подтверждения)
#
# Требования:
#   kubectl, jq, openssl, curl (в pod'е deploy/server-service)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NS="${DBOS_NAMESPACE:-dbos}"
# Патчим keystore-Secret, а не env-Secret dbos-secrets.
SECRET="${DBOS_SECRET_NAME:-dbos-server-encryption-keys}"
# s2s-ключи (ROTATION_RUNNER_API_KEY) лежат в dbos-secrets — отдельно от keystore.
S2S_SECRET="${DBOS_S2S_SECRET_NAME:-dbos-secrets}"
SERVER_DEPLOY="server-service"
WORKER_DEPLOY="server-worker"
SERVER_PORT="8002"
MIGRATION_STATUS_PATH="/api/server/v1/internal/migration_status"

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

# Текущая активная версия из keystore-Secret'а.
active_version() {
    secret_get "$ACTIVE_FIELD"
}

# Все версии в keystore (key_v<N>), отсортированные численно.
keystore_versions() {
    kubectl -n "$NS" get secret "$SECRET" -o json 2>/dev/null \
        | jq -r '.data | keys[]' \
        | grep -E "^${KEY_PREFIX}[0-9]+\$" \
        | sed "s/^${KEY_PREFIX}//" \
        | sort -n || true
}

# Не-активные версии (кандидаты на retire), по возрастанию.
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

# Проверить, что lazy-миграция завершена. 0 — можно retire, 1 — нельзя.
check_migration_complete() {
    local json
    if ! json=$(fetch_migration_status_json "$SERVER_DEPLOY" "$SERVER_PORT" "$MIGRATION_STATUS_PATH"); then
        return 1
    fi
    local reason
    if reason=$(migration_status_is_complete "$json"); then
        return 0
    fi
    echo "ОШИБКА: миграция не завершена (${reason})." >&2
    echo "" >&2
    echo "Что делать:" >&2
    echo "  1. Подождать lazy re-encrypt (read-path сам перешифрует на чтении)." >&2
    echo "  2. Запустить proactive outbox-seed для остатков:" >&2
    echo "     kubectl -n $NS exec deploy/$SERVER_DEPLOY -- curl -s -X POST \\" >&2
    echo "         -H \"X-Service-Identity: rotation_runner\" -H \"Authorization: Bearer \$KEY\" \\" >&2
    echo "         http://localhost:${SERVER_PORT}/api/server/v1/internal/reencrypt_outbox/seed?limit=5000" >&2
    echo "  3. Если данные принципиально не читаются (мёртвые credential'ы, заброшенные dept'ы) —" >&2
    echo "     удалить их вручную или дёрнуть --force-finalize под approval'ом security'а." >&2
    return 1
}

# Retire одной версии: убрать key_v<N> из keystore-Secret'а.
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
    echo "=== FINALIZE keystore server_service ==="
    echo ""
    show_status

    local victims
    victims="$(non_active_versions)"
    if [[ -z "$victims" ]]; then
        echo "→ Не-активных версий нет. Готово."
        return 0
    fi

    if [[ "$FORCE_FINALIZE" == "true" ]]; then
        confirm_force_finalize "server-master" || { echo "Отменено."; exit 1; }
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

# Retire версий строго СТАРШЕ заданного floor'а (v<floor), активную не трогаем.
# Используется в auto-finalize при неполной миграции: держим active + active-1,
# выпиливаем всё, что глубже.
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

# ── ACTION: rotate (общая фаза 1) ──────────────────────────────────────────────

do_rotate() {
    local cur_ver new_ver new_key max_ver
    cur_ver="$(active_version)"

    if [[ -z "$cur_ver" ]]; then
        echo "ОШИБКА: не нашёл ${ACTIVE_FIELD} в keystore-Secret'е $NS/$SECRET." >&2
        echo "  Keystore не засеян? См. scripts/k8s/gen_secrets.sh / deploy.sh bootstrap." >&2
        exit 1
    fi

    # new_version = max(known)+1 — монотонно, как в key_rotation_service.rotate.
    max_ver="$(keystore_versions | tail -n1)"
    [[ -z "$max_ver" ]] && max_ver="$cur_ver"
    new_ver=$((max_ver + 1))
    new_key=$(openssl rand -base64 32 | tr -d '\n=')

    local ts summary_out
    ts="$(date -u +%Y%m%dT%H%M%SZ)"
    summary_out="/tmp/dbos-rotate-${ts}.txt"

    cat <<EOF

Будет сделано:
  1. key_v${new_ver} = <новый ключ> в keystore-Secret'е $SECRET.
  2. ${ACTIVE_FIELD} = ${new_ver} (новые токены сразу под новой версией).
  3. Прошлый key_v${cur_ver} остаётся для расшифровки старых строк.
  4. Lazy re-encrypt сам мигрирует данные на чтении. Прогресс — --status.

EOF

    if [[ "$ASSUME_YES" != "true" ]]; then
        cat <<EOF
Новый мастер-ключ (СОХРАНИ — без него восстановление невозможно):
  ${new_key}

Дубликат в: ${summary_out}

EOF
        {
            echo "DBOS server master-key rotation"
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
Lazy re-encrypt мигрирует данные под новый ключ на чтении. Для «холодных»
строк запусти proactive seed:
    kubectl -n $NS exec deploy/$SERVER_DEPLOY -- curl -s -X POST \\
        -H "X-Service-Identity: rotation_runner" -H "Authorization: Bearer \$KEY" \\
        http://localhost:${SERVER_PORT}/api/server/v1/internal/reencrypt_outbox/seed?limit=5000

Прогресс смотреть:
    $0 --status

Финализация (после migration_status: complete):
    $0 --finalize

EOF
}

if [[ "$ACTION" == "rotate" ]]; then
    echo "=== РОТАЦИЯ keystore server_service (фаза 1) ==="
    show_status
    do_rotate
    exit 0
fi

# ── ACTION: auto-finalize (CronJob) ────────────────────────────────────────────
#
#   - Если миграция полная → retire всех не-активных версий + rotate.
#   - Если миграция не полная → rotate без полного retire; убираем только
#     версии глубже active-1, чтобы за 6 мес ключи не накапливались.

if [[ "$ACTION" == "auto-finalize" ]]; then
    echo "=== AUTO-FINALIZE keystore server_service ==="
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

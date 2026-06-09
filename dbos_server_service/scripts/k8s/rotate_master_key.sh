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
#   5. Lazy re-encrypt: серверные read-path'ы перешифровывают строки активным
#      ключом на лету. Прогресс — `/internal/migration_status`.
#   6. После remaining_legacy=0 — удаление SERVER_ENCRYPTION_KEY__v<old>.
#
# Использование:
#   scripts/k8s/rotate_master_key.sh                  # фаза 1: rotate + restart, finalize не делает
#   scripts/k8s/rotate_master_key.sh --finalize       # фаза 2: drop __v<old>, если migration 100%
#   scripts/k8s/rotate_master_key.sh --auto-finalize  # CronJob-режим: finalize previous-previous + rotate
#   scripts/k8s/rotate_master_key.sh --status         # показать текущую версию и legacy keys
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
SECRET="${DBOS_SECRET_NAME:-dbos-secrets}"
SERVER_DEPLOY="server-service"
WORKER_DEPLOY="server-worker"
SERVER_PORT="8002"
# Параллельные lazy-агенты выставят этот path (см. obsidian/infra/runbooks/Rotation.md).
# Существующий /internal/secrets/migration_status под worker-JWT остаётся; этот —
# отдельный S2S endpoint для rotation-runner.
MIGRATION_STATUS_PATH="/api/server/v1/internal/migration_status"

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

# Список legacy-keys в Secret'е, отсортированный по версии.
list_legacy_keys() {
    kubectl -n "$NS" get secret "$SECRET" -o json \
        | jq -r '.data | keys[]' \
        | grep -E '^SERVER_ENCRYPTION_KEY__v[0-9]+$' \
        | sort -t v -k2 -n || true
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
    list_legacy_keys | sed 's/^/  /' || echo "  (нет)"
    echo ""
}

# Проверить, что lazy-миграция завершена. Возвращает 0 если можно дропать,
# 1 если нельзя. Печатает в stderr человекочитаемое сообщение.
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
    echo "         http://localhost:${SERVER_PORT}/api/server/v1/reencrypt_outbox/seed?limit=5000" >&2
    echo "  3. Если данные принципиально не читаются (мёртвые credential'ы, заброшенные dept'ы) —" >&2
    echo "     удалить их вручную или дёрнуть --force-finalize под approval'ом security'а." >&2
    return 1
}

# ── ACTION: status ────────────────────────────────────────────────────────────

if [[ "$ACTION" == "status" ]]; then
    show_status
    exit 0
fi

# ── ACTION: finalize / auto-finalize ──────────────────────────────────────────
#
# finalize:
#   - проверка migration_status (если не --force-finalize)
#   - дропнуть ВСЕ SERVER_ENCRYPTION_KEY__v<N>
#   - rolling restart
#
# auto-finalize (для CronJob):
#   - если есть legacy keys: попытаться finalize previous-previous (всё кроме
#     самого свежего __v<N-1>); если миграция полная — дроп ВСЕХ, иначе
#     только дроп __v<N-2> и старше, __v<N-1> оставить.
#   - после этого — провести rotate (фаза 1).

do_finalize() {
    echo "=== FINALIZE SERVER_ENCRYPTION_KEY ==="
    echo ""
    show_status

    local prev_keys
    prev_keys=$(list_legacy_keys)
    if [[ -z "$prev_keys" ]]; then
        echo "→ Previous-keys уже не в Secret'е. Готово."
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
        confirm "Удалить ВСЕ previous-keys из Secret'а?" || { echo "Отменено."; exit 0; }
    fi

    while read -r key; do
        [[ -z "$key" ]] && continue
        echo "→ Удаляю $key из Secret'а..."
        secret_unset "$key"
    done <<< "$prev_keys"

    echo "→ Rolling restart server-service + server-worker..."
    kubectl -n "$NS" rollout restart deploy/"$SERVER_DEPLOY"
    kubectl -n "$NS" rollout restart deploy/"$WORKER_DEPLOY"
    kubectl -n "$NS" rollout status deploy/"$SERVER_DEPLOY"  --timeout=300s
    kubectl -n "$NS" rollout status deploy/"$WORKER_DEPLOY" --timeout=300s

    echo ""
    echo "✓ Финализация завершена. Previous-keys удалены."
}

# Удалить только legacy ключи СТАРШЕ заданного `keep_ver` (drop __v<N-2> и
# глубже, оставить __v<N-1>). Используется в auto-finalize, когда полный
# finalize невозможен (миграция <100%), но previous-previous уже точно
# никому не нужен — за 6 мес data под двумя ключами вглубь не остаётся.
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

# ── ACTION: rotate (общая фаза 1) ──────────────────────────────────────────────

do_rotate() {
    local cur_key cur_ver new_ver new_key
    cur_key=$(secret_get SERVER_ENCRYPTION_KEY)
    cur_ver=$(secret_get SERVER_ENCRYPTION_KEY_VERSION)

    if [[ -z "$cur_key" || -z "$cur_ver" ]]; then
        echo "ОШИБКА: не нашёл SERVER_ENCRYPTION_KEY / _VERSION в Secret'е $NS/$SECRET." >&2
        exit 1
    fi

    new_ver=$((cur_ver + 1))
    new_key=$(openssl rand -base64 32 | tr -d '\n=')

    local ts summary_out
    ts="$(date -u +%Y%m%dT%H%M%SZ)"
    summary_out="/tmp/dbos-rotate-${ts}.txt"

    cat <<EOF

Будет сделано:
  1. Текущий ключ (v${cur_ver}) переедет в SERVER_ENCRYPTION_KEY__v${cur_ver}.
  2. SERVER_ENCRYPTION_KEY = <новый>, SERVER_ENCRYPTION_KEY_VERSION = ${new_ver}.
  3. Rolling restart server-service + server-worker.
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
            echo "old_version: ${cur_ver}"
            echo "new_version: ${new_ver}"
            echo ""
            echo "NEW SERVER_ENCRYPTION_KEY:"
            echo "${new_key}"
            echo ""
            echo "PREVIOUS SERVER_ENCRYPTION_KEY (теперь под SERVER_ENCRYPTION_KEY__v${cur_ver}):"
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
    echo "→ Пишу previous-ключ в SERVER_ENCRYPTION_KEY__v${cur_ver}..."
    secret_set_string "SERVER_ENCRYPTION_KEY__v${cur_ver}" "$cur_key"

    echo "→ Подменяю SERVER_ENCRYPTION_KEY и SERVER_ENCRYPTION_KEY_VERSION..."
    kubectl -n "$NS" patch secret "$SECRET" --type='merge' -p "$(jq -n \
        --arg new_key "$new_key" \
        --arg new_ver "$new_ver" \
        '{stringData: {SERVER_ENCRYPTION_KEY: $new_key, SERVER_ENCRYPTION_KEY_VERSION: $new_ver}}')"

    echo ""
    echo "→ Rolling restart server-service + server-worker..."
    confirm "Рестартовать сейчас?" || {
        echo "Pause. Запусти руками: kubectl -n $NS rollout restart deploy/$SERVER_DEPLOY deploy/$WORKER_DEPLOY"
        return 0
    }

    kubectl -n "$NS" rollout restart deploy/"$SERVER_DEPLOY"
    kubectl -n "$NS" rollout restart deploy/"$WORKER_DEPLOY"
    kubectl -n "$NS" rollout status deploy/"$SERVER_DEPLOY"  --timeout=300s
    kubectl -n "$NS" rollout status deploy/"$WORKER_DEPLOY" --timeout=300s

    cat <<EOF

✓ Ключ ротирован. Активная версия = v${new_ver}.

Lazy re-encrypt автоматически мигрирует данные под новый ключ на чтении.
Прогресс смотреть:
    $0 --status

Финализация (после migration_status: complete):
    $0 --finalize

EOF
}

if [[ "$ACTION" == "rotate" ]]; then
    echo "=== РОТАЦИЯ SERVER_ENCRYPTION_KEY (фаза 1) ==="
    show_status
    do_rotate
    exit 0
fi

# ── ACTION: auto-finalize (CronJob) ────────────────────────────────────────────
#
# Идея: одна команда, безопасный шаг forward.
#   - Если миграция полная → finalize (дроп всех __v<N>) + rotate (новый ключ).
#   - Если миграция не полная → rotate без finalize; дроп __v<N-2> и старше,
#     чтобы за 6 мес legacy keys не накапливались.

if [[ "$ACTION" == "auto-finalize" ]]; then
    echo "=== AUTO-FINALIZE SERVER_ENCRYPTION_KEY ==="
    show_status

    cur_ver=$(secret_get SERVER_ENCRYPTION_KEY_VERSION)
    if [[ -z "$cur_ver" ]]; then
        echo "ОШИБКА: SERVER_ENCRYPTION_KEY_VERSION пуст в Secret'е." >&2
        exit 1
    fi

    legacy=$(list_legacy_keys || true)

    if [[ -n "$legacy" ]]; then
        if check_migration_complete 2>/dev/null; then
            echo "✓ migration_status: complete → finalize."
            ACTION_LABEL="finalize"
            while read -r key; do
                [[ -z "$key" ]] && continue
                echo "→ Удаляю $key..."
                secret_unset "$key"
            done <<< "$legacy"
            # rolling restart после drop'а старого ключа, иначе pod'ы продолжат
            # держать его в памяти и попытаются decrypt'ить, если встретят (что
            # не страшно, но логически грязно).
            echo "→ Rolling restart..."
            kubectl -n "$NS" rollout restart deploy/"$SERVER_DEPLOY"
            kubectl -n "$NS" rollout restart deploy/"$WORKER_DEPLOY"
            kubectl -n "$NS" rollout status deploy/"$SERVER_DEPLOY"  --timeout=300s
            kubectl -n "$NS" rollout status deploy/"$WORKER_DEPLOY" --timeout=300s
        else
            echo "⚠ migration_status: не complete → finalize пропускается."
            echo "  Дроплю только __v<N-2> и старее (если есть), новейший __v<N-1> сохраняю."
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

#!/usr/bin/env bash
# Безопасностный harness: прогоняет последовательность ротаций и убеждается,
# что кластер после каждой остаётся живым (smoke_test проходит).
#
# Идея:
#   1. Перед началом — кластер должен быть Ready и smoke должен проходить.
#      Если уже что-то не так — не ротируем (rotation бы только усугубил).
#   2. Делаем backup master-ключей + полный snapshot dbos-secrets — на случай
#      если придётся восстанавливать.
#   3. Прогоняем по одной ротации из ROTATIONS:
#        - rotate_s2s_keys.sh        (самая безопасная: только Secret patch + restart)
#        - rotate_redis_password.sh  (после фикса: Secret patch + restart redis + consumers)
#        - rotate_db_passwords.sh    (ALTER USER + Secret patch + restart consumers)
#      После КАЖДОЙ — smoke. Если smoke упал — откат из backup'а (kubectl apply),
#      restart всех deploy'ев → выход 1.
#   4. Финальный smoke + summary.
#
# Master-key ротации (rotate_master_key.sh / rotate_secret_master_key.sh /
# rotate_redis_stash_master_key.sh) запускаются в `--auto-finalize` режиме:
# одна команда делает безопасный шаг forward — finalize previous-previous (если
# миграция за прошлый cycle дошла до 100%) + rotate новой версии. До migration
# не доходит = просто rotate без drop'а текущего __v<N-1>.
#
# Использование:
#   scripts/k8s/test_rotation_safety.sh                       # интерактивно, полный набор
#   scripts/k8s/test_rotation_safety.sh --yes                 # для CronJob — без prompt'ов
#   scripts/k8s/test_rotation_safety.sh --dry-run             # показать план, не выполнять
#   scripts/k8s/test_rotation_safety.sh --restore-only DIR    # только восстановление из <DIR>
#   scripts/k8s/test_rotation_safety.sh --rotation server-master   # ручной запуск ONE-OF
#       Возможные значения: s2s | redis | db |
#                            secret-master | redis-stash-master | server-master
#
# Требования: kubectl, jq, bash, sibling-скрипты в той же папке.
# Должны быть смонтированы прямо рядом:
#   smoke_test.sh, backup_master_keys.sh, backup_secret_full.sh,
#   rotate_s2s_keys.sh, rotate_redis_password.sh, rotate_db_passwords.sh.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NS="${DBOS_NAMESPACE:-dbos}"

ASSUME_YES="false"
DRY_RUN="false"
RESTORE_ONLY=""
SINGLE_ROTATION=""

# ── Утилиты ───────────────────────────────────────────────────────────────────

require_bin() {
    command -v "$1" >/dev/null 2>&1 || { echo "ОШИБКА: нужен $1 в PATH." >&2; exit 1; }
}

require_bin kubectl
require_bin jq

confirm() {
    local prompt="$1"
    if [[ "$ASSUME_YES" == "true" ]]; then
        return 0
    fi
    read -p "  ${prompt} [yes/no]: " yn
    [[ "$yn" == "yes" ]]
}

log_info() { echo "▶ $*"; }
log_ok()   { echo "  ✓ $*"; }
log_warn() { echo "  ⚠ $*" >&2; }
log_err()  { echo "  ✗ $*" >&2; }

# ── Парсинг аргументов ────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
    case "$1" in
        --yes|-y)
            ASSUME_YES="true"
            shift
            ;;
        --dry-run)
            DRY_RUN="true"
            shift
            ;;
        --restore-only)
            shift
            RESTORE_ONLY="${1:-}"
            [[ -n "$RESTORE_ONLY" ]] || { echo "ОШИБКА: --restore-only требует путь к backup-каталогу." >&2; exit 1; }
            shift
            ;;
        --rotation)
            shift
            SINGLE_ROTATION="${1:-}"
            [[ -n "$SINGLE_ROTATION" ]] || { echo "ОШИБКА: --rotation требует имя." >&2; exit 1; }
            shift
            ;;
        -h|--help)
            sed -n '2,40p' "$0"
            exit 0
            ;;
        *)
            echo "ОШИБКА: неизвестный аргумент: $1" >&2
            exit 1
            ;;
    esac
done

# ── Smoke wrapper ─────────────────────────────────────────────────────────────

run_smoke() {
    local label="$1"
    # Retry-loop: rollout status может вернуться раньше чем Traefik обновит
    # endpoint'ы, и тогда smoke ловит 502 на свежезарестартированном сервисе.
    # Делаем до 3 попыток с паузой 10с между ними — стабилизирует endpoint slice.
    local attempt
    for attempt in 1 2 3; do
        log_info "smoke: ${label} (попытка ${attempt}/3)..."
        if bash "$SCRIPT_DIR/smoke_test.sh" >/tmp/dbos-safety-smoke-${label}.log 2>&1; then
            log_ok "smoke OK (${label}, попытка ${attempt}). Лог: /tmp/dbos-safety-smoke-${label}.log"
            return 0
        fi
        if [[ $attempt -lt 3 ]]; then
            log_warn "smoke FAIL (${label}, попытка ${attempt}); жду 10с и повторяю..."
            sleep 10
        fi
    done
    log_err "smoke FAIL (${label}) после 3 попыток. Лог: /tmp/dbos-safety-smoke-${label}.log"
    tail -n 30 /tmp/dbos-safety-smoke-${label}.log >&2 || true
    return 1
}

# ── --restore-only ────────────────────────────────────────────────────────────

restore_from() {
    local backup_dir="$1"
    log_info "Восстановление из ${backup_dir}..."
    local full_yaml
    full_yaml=$(ls -t "$backup_dir"/secret-full-*.yaml 2>/dev/null | head -n1 || true)
    if [[ -z "$full_yaml" ]]; then
        log_err "не найден secret-full-*.yaml в ${backup_dir}."
        return 1
    fi
    log_info "Используем ${full_yaml}."
    if [[ "$ASSUME_YES" == "true" ]]; then
        bash "$SCRIPT_DIR/backup_secret_full.sh" --restore "$full_yaml" --no-encryption --yes
    else
        bash "$SCRIPT_DIR/backup_secret_full.sh" --restore "$full_yaml" --no-encryption
    fi
    log_info "Рестартую все deploy'и для применения восстановленного Secret'а..."
    for d in redis auth-service logging-service server-service server-worker secret-service; do
        kubectl -n "$NS" rollout restart "deploy/${d}" >/dev/null 2>&1 || true
    done
    for d in redis auth-service logging-service server-service server-worker secret-service; do
        kubectl -n "$NS" rollout status "deploy/${d}" --timeout=180s || log_warn "rollout ${d} не завершился"
    done
    log_ok "Восстановление завершено."
}

if [[ -n "$RESTORE_ONLY" ]]; then
    restore_from "$RESTORE_ONLY"
    exit 0
fi

# ── Pre-checks ────────────────────────────────────────────────────────────────

echo "=== ROTATION SAFETY HARNESS ==="
echo "namespace: ${NS}"
echo "mode:      $([[ "$DRY_RUN" == "true" ]] && echo "DRY-RUN " || true)${SINGLE_ROTATION:+single=${SINGLE_ROTATION} }$([[ "$ASSUME_YES" == "true" ]] && echo non-interactive || echo interactive)"
echo ""

log_info "pre-check: все pod'ы в namespace ${NS} должны быть Ready..."
# POSIX awk не умеет backreferences (`\1`), поэтому сверяем numerator/denominator
# по split. Игнорируем Completed (это пачка job-pod'ов после миграций).
NOT_READY=$(kubectl -n "$NS" get pods --no-headers 2>/dev/null \
    | awk '$3 == "Completed" {next} {n=split($2,a,"/"); if (n!=2 || a[1] != a[2] || $3 != "Running") print $1, $2, $3}' || true)
if [[ -n "$NOT_READY" ]]; then
    log_err "не все pod'ы Ready/Running:"
    echo "$NOT_READY" >&2
    if [[ "$ASSUME_YES" == "true" ]]; then
        exit 1
    fi
    confirm "Продолжить несмотря на это?" || exit 1
else
    log_ok "все pod'ы Ready/Running."
fi

if [[ "$DRY_RUN" != "true" ]]; then
    if ! run_smoke baseline; then
        log_err "baseline smoke упал. Ротации НЕ запускаются — сначала почини cluster."
        exit 1
    fi
fi

# ── Backup ────────────────────────────────────────────────────────────────────

TS="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="/tmp/dbos-rotation-safety-${TS}"
BACKUP_FULL="${BACKUP_DIR}/secret-full-${TS}.yaml"

if [[ "$DRY_RUN" == "true" ]]; then
    log_info "[dry-run] пропускаю backup."
else
    mkdir -p "$BACKUP_DIR"
    chmod 700 "$BACKUP_DIR"

    log_info "backup master-keys → ${BACKUP_DIR}/master/"
    BACKUP_TARGET_DIR="${BACKUP_DIR}/master" \
        BACKUP_PASSPHRASE="${BACKUP_PASSPHRASE:-dbos-rotation-safety-${TS}}" \
        bash "$SCRIPT_DIR/backup_master_keys.sh" \
        || { log_err "backup master-keys провалился."; exit 1; }

    log_info "backup full Secret → ${BACKUP_FULL}"
    # --no-encryption: harness, не отправляется в долгое хранение; для prod
    # CronJob — задать BACKUP_PASSPHRASE и убрать флаг.
    bash "$SCRIPT_DIR/backup_secret_full.sh" \
        --target-dir "$BACKUP_DIR" \
        --no-encryption \
        --yes \
        || { log_err "backup full Secret провалился."; exit 1; }
    # backup_secret_full даёт имя с собственным TS; найдём актуальный файл.
    LATEST_FULL=$(ls -t "$BACKUP_DIR"/secret-full-*.yaml 2>/dev/null | head -n1 || true)
    if [[ -z "$LATEST_FULL" ]]; then
        log_err "после backup_secret_full.sh нет ни одного secret-full-*.yaml в ${BACKUP_DIR}."
        exit 1
    fi
    BACKUP_FULL="$LATEST_FULL"
    log_ok "backup готов: ${BACKUP_FULL}"
fi

# ── Список ротаций ────────────────────────────────────────────────────────────

# Порядок безопасности (от менее инвазивной к более):
#   1. s2s                — только Secret patch + restart всех consumer'ов.
#   2. redis              — Secret patch + restart redis (Recreate, 5-15s downtime) + consumers.
#   3. db                 — ALTER USER в 5 БД + Secret patch + restart consumers.
#   4. secret-master      — auto-finalize: rotate SECRET_ENCRYPTION_KEY + drop ancient legacy.
#   5. redis-stash-master — auto-finalize: rotate REDIS_STASH_ENCRYPTION_KEY.
#   6. server-master      — auto-finalize: rotate SERVER_ENCRYPTION_KEY.
#
# Master-key последними, потому что rolling restart всего стека (server+worker+secret)
# затрагивает Traefik endpoint slices сильнее, чем s2s/db/redis по отдельности.
#
# Format: "id | script | args" — id используется в логах и result map, script — bash file
# в SCRIPT_DIR, args — собираются в bash -c.
ROTATION_SPECS=(
    "s2s                | rotate_s2s_keys.sh                 | --yes"
    "redis              | rotate_redis_password.sh           | --yes"
    "db                 | rotate_db_passwords.sh             | --service all --yes"
    "secret-master      | rotate_secret_master_key.sh        | --auto-finalize --yes"
    "redis-stash-master | rotate_redis_stash_master_key.sh   | --auto-finalize --yes"
    "server-master      | rotate_master_key.sh               | --auto-finalize --yes"
)

# Парсим в две параллельные структуры: ROTATION_IDS (порядок) и
# ROTATION_CMD (map id → full command).
ROTATION_IDS=()
declare -A ROTATION_CMD=()
for spec in "${ROTATION_SPECS[@]}"; do
    # Trim каждое поле от пробелов вокруг "|".
    rid=$(echo "$spec"   | awk -F'|' '{gsub(/^[[:space:]]+|[[:space:]]+$/, "", $1); print $1}')
    script=$(echo "$spec"| awk -F'|' '{gsub(/^[[:space:]]+|[[:space:]]+$/, "", $2); print $2}')
    args=$(echo "$spec"  | awk -F'|' '{gsub(/^[[:space:]]+|[[:space:]]+$/, "", $3); print $3}')
    ROTATION_IDS+=("$rid")
    ROTATION_CMD[$rid]="${SCRIPT_DIR}/${script} ${args}"
done

# Master-key ротации требуют rolling restart всего стека — Traefik endpoint slices
# обновляются с задержкой. После рестарта добавляем пауза в run_rotation_smoke().
declare -A ROTATION_POST_PAUSE=(
    [secret-master]=30
    [redis-stash-master]=30
    [server-master]=30
)

if [[ -n "$SINGLE_ROTATION" ]]; then
    if [[ -z "${ROTATION_CMD[$SINGLE_ROTATION]:-}" ]]; then
        log_err "неизвестная ротация: ${SINGLE_ROTATION}"
        echo "Возможные: ${ROTATION_IDS[*]}" >&2
        exit 1
    fi
    ROTATION_IDS=("$SINGLE_ROTATION")
fi

declare -A ROTATION_RESULT=()

# ── Dry-run: показать план ────────────────────────────────────────────────────

if [[ "$DRY_RUN" == "true" ]]; then
    echo ""
    log_info "План (ничего не выполняется):"
    for rid in "${ROTATION_IDS[@]}"; do
        pause="${ROTATION_POST_PAUSE[$rid]:-0}"
        if [[ "$pause" -gt 0 ]]; then
            echo "  - ${rid}: ${ROTATION_CMD[$rid]}  +pause=${pause}s"
        else
            echo "  - ${rid}: ${ROTATION_CMD[$rid]}"
        fi
    done
    echo "  + smoke после каждой (с 3x retry)"
    echo ""
    log_ok "dry-run завершён."
    exit 0
fi

# ── Прогон ротаций ────────────────────────────────────────────────────────────

for rid in "${ROTATION_IDS[@]}"; do
    cmd="${ROTATION_CMD[$rid]}"
    echo ""
    log_info "=== ROTATION: ${rid} ==="
    log_info "cmd: ${cmd}"
    confirm "Запустить ${rid}?" || { log_warn "пропущена."; ROTATION_RESULT[$rid]="SKIP"; continue; }

    if ! bash -c "$cmd"; then
        log_err "ротация ${rid} провалилась."
        ROTATION_RESULT[$rid]="FAIL"
        log_warn "запускаю --restore-only из ${BACKUP_DIR}..."
        restore_from "$BACKUP_DIR" || log_err "восстановление тоже провалилось — нужно вмешательство руками."
        echo ""
        echo "=== HARNESS FAILED ==="
        for k in "${!ROTATION_RESULT[@]}"; do
            printf "  %-22s %s\n" "$k" "${ROTATION_RESULT[$k]}"
        done
        exit 1
    fi

    # Master-key rotation требует extra времени на rolling restart всего стека +
    # обновление Traefik endpoint slices. Без паузы smoke ловит 502/connection refused
    # на свежезарестартированных pod'ах, даже несмотря на 3x retry в run_smoke.
    pause="${ROTATION_POST_PAUSE[$rid]:-0}"
    if [[ "$pause" -gt 0 ]]; then
        log_info "пауза ${pause}s перед smoke (waiting for endpoint slices)..."
        sleep "$pause"
    fi

    if ! run_smoke "after-${rid}"; then
        log_err "после ротации ${rid} smoke провалился."
        ROTATION_RESULT[$rid]="SMOKE_FAIL"
        log_warn "запускаю --restore-only из ${BACKUP_DIR}..."
        restore_from "$BACKUP_DIR" || log_err "восстановление тоже провалилось — нужно вмешательство руками."
        echo ""
        echo "=== HARNESS FAILED ==="
        for k in "${!ROTATION_RESULT[@]}"; do
            printf "  %-22s %s\n" "$k" "${ROTATION_RESULT[$k]}"
        done
        exit 1
    fi

    ROTATION_RESULT[$rid]="PASS"
done

# ── Final smoke ───────────────────────────────────────────────────────────────

echo ""
if ! run_smoke final; then
    log_err "final smoke провалился."
    exit 1
fi

# ── Summary ───────────────────────────────────────────────────────────────────

echo ""
echo "=== HARNESS SUMMARY ==="
for k in "${ROTATION_IDS[@]}"; do
    printf "  %-22s %s\n" "$k" "${ROTATION_RESULT[$k]:-?}"
done
echo ""
echo "Backup сохранён в ${BACKUP_DIR}."
echo "Для повторного восстановления:"
echo "  $0 --restore-only ${BACKUP_DIR}"
echo ""
log_ok "harness завершён без ошибок."

#!/usr/bin/env bash
# pg_dump обоих кластеров (auth + logging) с custom-форматом и проверкой
# целостности. Запускается двумя способами:
#
#   1) Локально с хоста (k3s VM):
#        scripts/k8s/backup_pg.sh /var/backups/dbos
#
#   2) Из CronJob внутри кластера (см. k8s/100-postgres-backup.yaml):
#        TARGET_DEPLOY=auth-postgres \
#        TARGET_DB=auth_db \
#        TARGET_USER=auth_user \
#        BACKUP_ROOT=/backup \
#        scripts/k8s/backup_pg.sh
#      В этом режиме pg_dump зовётся прямо в контейнере postgres-клиента
#      (через PGHOST=<service>), kubectl не нужен.
#
# Retention: реализован как ring of suffixes. На каждый кластер три кольца:
#   - daily-NN.dump   (NN=00..13, 14 слотов, по дню недели/месяца mod 14)
#   - weekly-NN.dump  (NN=0..3,   4 слота,   по неделе mod 4)
#   - monthly-NN.dump (NN=0..5,   6 слотов,  по месяцу mod 6)
# Слот перезаписывается, старого файла на диске никогда больше N штук.
# Имена детерминированы — restore сразу видит самый свежий файл в каждом кольце.

set -euo pipefail

# ──────────────────────────────────────────────────────────────────────────
# Режим 1: вызов из CronJob (in-cluster, переменные окружения переданы)
# ──────────────────────────────────────────────────────────────────────────
if [[ -n "${TARGET_DEPLOY:-}" ]]; then
    : "${TARGET_DB:?TARGET_DB required}"
    : "${TARGET_USER:?TARGET_USER required}"
    : "${BACKUP_ROOT:?BACKUP_ROOT required (PV mount inside CronJob)}"
    : "${PGPASSWORD:?PGPASSWORD required}"

    DEST="$BACKUP_ROOT/$TARGET_DEPLOY"
    mkdir -p "$DEST"

    # Слот в кольце определяется текущей датой UTC. Day-of-year mod ring_size.
    DOY=$(date -u +%j)            # 001..366
    DOY_INT=$((10#$DOY))
    DOW=$(date -u +%u)            # 1..7
    DOM=$(date -u +%d)
    MONTH=$(date -u +%m); MONTH_INT=$((10#$MONTH))

    DAILY_SLOT=$(printf "%02d" $((DOY_INT % 14)))
    WEEKLY_SLOT=$((DOY_INT / 7 % 4))
    MONTHLY_SLOT=$((MONTH_INT % 6))

    DAILY_FILE="$DEST/daily-$DAILY_SLOT.dump"
    WEEKLY_FILE="$DEST/weekly-$WEEKLY_SLOT.dump"
    MONTHLY_FILE="$DEST/monthly-$MONTHLY_SLOT.dump"

    echo "→ Бэкап $TARGET_DEPLOY ($TARGET_DB) UTC=$(date -u +%FT%TZ)"
    echo "  daily slot:   $DAILY_FILE"

    TMP="$DEST/.in-progress-$$.dump"
    trap 'rm -f "$TMP"' EXIT

    # custom-формат: восстанавливается через pg_restore, поддерживает
    # параллельный --jobs и партиальный restore (-t/-n). --compress=6 — sweet
    # spot между размером и CPU. --no-owner / --no-acl упрощают restore в
    # другую БД (например тестовый clone).
    pg_dump \
        --host="$TARGET_DEPLOY" \
        --username="$TARGET_USER" \
        --dbname="$TARGET_DB" \
        --format=custom \
        --compress=6 \
        --no-owner \
        --no-acl \
        --file="$TMP"

    # Verification: если pg_restore --list не парсится — дамп битый,
    # не перезаписываем рабочий слот.
    if ! pg_restore --list "$TMP" >/dev/null 2>&1; then
        echo "✗ Verification failed (pg_restore --list)" >&2
        exit 1
    fi

    # Атомарная замена слота: новый файл → daily; weekly/monthly — копия
    # daily в первый день недели/месяца. Так weekly и monthly никогда не
    # делают второй pg_dump (нагрузка на БД одна на запуск).
    mv "$TMP" "$DAILY_FILE"
    trap - EXIT

    if [[ "$DOW" == "1" ]]; then
        cp "$DAILY_FILE" "$WEEKLY_FILE"
        echo "  weekly slot:  $WEEKLY_FILE (Monday rotation)"
    fi
    if [[ "$DOM" == "01" ]]; then
        cp "$DAILY_FILE" "$MONTHLY_FILE"
        echo "  monthly slot: $MONTHLY_FILE (1st-of-month rotation)"
    fi

    SIZE=$(du -h "$DAILY_FILE" | cut -f1)
    echo "✓ $TARGET_DEPLOY: $SIZE"
    ls -lh "$DEST"
    exit 0
fi

# ──────────────────────────────────────────────────────────────────────────
# Режим 2: вызов с хоста (kubectl exec), без in-cluster переменных
# ──────────────────────────────────────────────────────────────────────────
DEST_DIR="${1:-./backups}"
TS=$(date -u +%Y%m%dT%H%M%SZ)
TARGET="$DEST_DIR/$TS"
mkdir -p "$TARGET"

dump_one() {
    local deploy=$1
    local db=$2
    local user=$3
    local out="$TARGET/${deploy}.dump"
    echo "→ Бэкап $deploy → $out..."
    kubectl -n dbos exec deploy/"$deploy" -- \
        pg_dump -U "$user" -d "$db" \
            --format=custom --compress=6 --no-owner --no-acl \
        > "$out"
    if ! pg_restore --list "$out" >/dev/null 2>&1; then
        echo "✗ Verification failed для $deploy" >&2
        return 1
    fi
    ls -lh "$out"
}

dump_one auth-postgres    auth_db    auth_user
dump_one logging-postgres logging_db logging_user

echo ""
echo "✓ Бэкап завершён: $TARGET"
echo ""
echo "  Ротация (старые > 30 дней удаляются):"
find "$DEST_DIR" -mindepth 1 -maxdepth 1 -type d -mtime +30 -print -exec rm -rf {} \;

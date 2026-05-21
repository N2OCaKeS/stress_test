#!/usr/bin/env bash
# pg_dump обоих баз через kubectl exec.
# Использование:
#   scripts/k8s/backup_pg.sh             — бэкап в ./backups/<timestamp>/
#   scripts/k8s/backup_pg.sh /backups    — бэкап в /backups/<timestamp>/
#
# Для cron на VM:
#   0 2 * * * /opt/dbos/scripts/k8s/backup_pg.sh /var/backups/dbos >/var/log/dbos-backup.log 2>&1

set -euo pipefail

DEST_DIR="${1:-./backups}"
TS=$(date -u +%Y%m%dT%H%M%SZ)
TARGET="$DEST_DIR/$TS"
mkdir -p "$TARGET"

dump_one() {
    local deploy=$1
    local db=$2
    local user=$3
    local out="$TARGET/${deploy}.sql.gz"
    echo "→ Бэкап $deploy → $out..."
    kubectl -n dbos exec deploy/"$deploy" -- pg_dump -U "$user" -d "$db" --clean --if-exists \
        | gzip > "$out"
    ls -lh "$out"
}

dump_one auth-postgres    auth_db    auth_user
dump_one logging-postgres logging_db logging_user

echo ""
echo "✓ Бэкап завершён: $TARGET"
echo ""
echo "  Ротация (старые > 30 дней удаляются):"
find "$DEST_DIR" -mindepth 1 -maxdepth 1 -type d -mtime +30 -print -exec rm -rf {} \;

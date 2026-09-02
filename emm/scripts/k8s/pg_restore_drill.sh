#!/usr/bin/env bash
# pg_restore drill — ручная проверка, что dump'ы из dbos-backup-pv реально
# восстанавливаются и содержат осмысленные данные.
#
# Запуск — только руками, не из CronJob. Рекомендуемая частота — раз в квартал
# (плюс после любых изменений в backup_pg.sh / 100-postgres-backup.yaml).
#
# Что делает:
#   1. Поднимает временный namespace dbos-drill.
#   2. Поднимает в нём postgres:16 pod (drill-postgres).
#   3. Через pod-mount берёт самый свежий daily-XX.dump для выбранной БД из
#      dbos-backup-pv (PVC ReadOnlyMany не поддерживается local-path, поэтому
#      используется временный clone PVC через hostPath; см. ниже).
#   4. pg_restore --clean --if-exists --dbname=<temp> в drill-postgres.
#   5. SELECT COUNT(*) FROM <main_table> и сравнивает с prod COUNT (delta ≤ 20%).
#   6. Печатает OK/FAIL, размер дампа, время восстановления.
#   7. kubectl delete ns dbos-drill — гарантированная очистка (trap EXIT).
#
# НЕ трогает prod-postgres ничем кроме одного SELECT COUNT(*).
#
# Использование:
#   scripts/k8s/pg_restore_drill.sh auth_db
#   scripts/k8s/pg_restore_drill.sh secret_db
#   scripts/k8s/pg_restore_drill.sh server_db
#   scripts/k8s/pg_restore_drill.sh worker_db
#   scripts/k8s/pg_restore_drill.sh logging_db
#
# Переменные окружения:
#   DBOS_NAMESPACE       — namespace c prod БД (default: dbos)
#   DBOS_DRILL_NAMESPACE — namespace для drill (default: dbos-drill)
#   DBOS_DRILL_DELTA_PCT — допустимая дельта в процентах (default: 20)
#   DBOS_DRILL_KEEP_NS   — 1 → не удалять namespace в конце (для разбора)
#   DBOS_DRILL_DBS       — список БД через запятую для --non-interactive
#                          (default: auth_db,logging_db,server_db,worker_db,secret_db)
#
# Флаги:
#   --non-interactive    запустить drill по всем БД из DBOS_DRILL_DBS подряд,
#                        не требует positional-аргумента. Подходит для CronJob:
#                        результат — summary в stdout + exit 0 если все БД OK,
#                        exit 1 если хоть одна FAIL.
#
# Требования:
#   kubectl, jq

set -euo pipefail

NS="${DBOS_NAMESPACE:-dbos}"
DRILL_NS="${DBOS_DRILL_NAMESPACE:-dbos-drill}"
DELTA_PCT="${DBOS_DRILL_DELTA_PCT:-20}"
KEEP_NS="${DBOS_DRILL_KEEP_NS:-0}"
PVC_NAME="dbos-backup-pv"

NON_INTERACTIVE="false"
DBS_ARG=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --non-interactive)
            NON_INTERACTIVE="true"
            shift
            ;;
        -h|--help)
            sed -n '1,40p' "$0" >&2
            exit 0
            ;;
        -*)
            echo "Неизвестный флаг $1" >&2
            exit 2
            ;;
        *)
            if [[ -z "$DBS_ARG" ]]; then
                DBS_ARG="$1"
            else
                echo "Лишний positional-аргумент: $1" >&2
                exit 2
            fi
            shift
            ;;
    esac
done

if [[ "$NON_INTERACTIVE" == "true" ]]; then
    # CronJob-режим: гоняем последовательно по списку DBOS_DRILL_DBS и
    # печатаем сводку. Сам скрипт вызывает себя per-DB через bash без --non-interactive.
    DBS_LIST="${DBOS_DRILL_DBS:-auth_db,logging_db,server_db,worker_db,secret_db}"
    IFS=',' read -r -a DBS_ARRAY <<< "$DBS_LIST"
    OVERALL_RC=0
    declare -a SUMMARY=()
    for db_name in "${DBS_ARRAY[@]}"; do
        echo ""
        echo "════════════════════════════════════════════════════════════════"
        echo " Drill: $db_name"
        echo "════════════════════════════════════════════════════════════════"
        if bash "$0" "$db_name"; then
            SUMMARY+=("OK   $db_name")
        else
            SUMMARY+=("FAIL $db_name")
            OVERALL_RC=1
        fi
    done
    echo ""
    echo "════════════════════════════════════════════════════════════════"
    echo " Сводка pg_restore_drill (non-interactive)"
    echo "════════════════════════════════════════════════════════════════"
    for line in "${SUMMARY[@]}"; do
        echo "  $line"
    done
    if [[ $OVERALL_RC -eq 0 ]]; then
        echo " Итог: все БД OK"
    else
        echo " Итог: есть FAIL — см. выше"
    fi
    exit $OVERALL_RC
fi

if [[ -z "$DBS_ARG" ]]; then
    cat >&2 <<EOF
Использование: $0 <db_name>
  где <db_name>: auth_db | logging_db | server_db | worker_db | secret_db
  либо $0 --non-interactive    (все БД из DBOS_DRILL_DBS)
EOF
    exit 2
fi

DB="$DBS_ARG"

# Маппинг БД → (deploy для backup-папки, имя таблицы для sanity, имя ключа в Secret)
case "$DB" in
    auth_db)
        TARGET_DEPLOY="auth-postgres"
        MAIN_TABLE="users"
        DB_USER_KEY="AUTH_DB_USER"
        DB_PASSWORD_KEY="AUTH_DB_PASSWORD"
        ;;
    logging_db)
        TARGET_DEPLOY="logging-postgres"
        MAIN_TABLE="audit_events"
        DB_USER_KEY="LOGGING_DB_USER"
        DB_PASSWORD_KEY="LOGGING_DB_PASSWORD"
        ;;
    server_db)
        TARGET_DEPLOY="server-postgres"
        MAIN_TABLE="servers"
        DB_USER_KEY="SERVER_DB_USER"
        DB_PASSWORD_KEY="SERVER_DB_PASSWORD"
        ;;
    worker_db)
        TARGET_DEPLOY="worker-postgres"
        MAIN_TABLE="tasks"
        DB_USER_KEY="WORKER_DB_USER"
        DB_PASSWORD_KEY="WORKER_DB_PASSWORD"
        ;;
    secret_db)
        TARGET_DEPLOY="secret-postgres"
        MAIN_TABLE="credentials"
        DB_USER_KEY="SECRET_DB_USER"
        DB_PASSWORD_KEY="SECRET_DB_PASSWORD"
        ;;
    *)
        echo "Неизвестная БД: $DB" >&2
        echo "Поддерживается: auth_db | logging_db | server_db | worker_db | secret_db" >&2
        exit 2
        ;;
esac

TEMP_DB="drill_${DB}"

echo "──────────────────────────────────────────────────────────────"
echo " pg_restore drill"
echo "──────────────────────────────────────────────────────────────"
echo " БД (target):        $DB"
echo " Deploy backup-папки: $TARGET_DEPLOY"
echo " Sanity таблица:     $MAIN_TABLE"
echo " Drill namespace:    $DRILL_NS"
echo " Допустимая дельта:  ±${DELTA_PCT}%"
echo "──────────────────────────────────────────────────────────────"
echo

# ────────────────────────────────────────────────────────────────────
# 0. Проверка зависимостей
# ────────────────────────────────────────────────────────────────────
for bin in kubectl jq; do
    if ! command -v "$bin" >/dev/null 2>&1; then
        echo "✗ Не найден $bin в PATH" >&2
        exit 1
    fi
done

if ! kubectl get ns "$NS" >/dev/null 2>&1; then
    echo "✗ Namespace $NS не существует" >&2
    exit 1
fi

if ! kubectl -n "$NS" get pvc "$PVC_NAME" >/dev/null 2>&1; then
    echo "✗ PVC $PVC_NAME в namespace $NS не найден" >&2
    exit 1
fi

# ────────────────────────────────────────────────────────────────────
# 1. Получить prod COUNT(*) FROM main_table (один SELECT, read-only)
# ────────────────────────────────────────────────────────────────────
echo "→ Шаг 1: prod COUNT(*) FROM $MAIN_TABLE (read-only)"
DB_USER=$(kubectl -n "$NS" get secret dbos-secrets -o "jsonpath={.data.$DB_USER_KEY}" | base64 -d)
DB_PASSWORD=$(kubectl -n "$NS" get secret dbos-secrets -o "jsonpath={.data.$DB_PASSWORD_KEY}" | base64 -d)

PROD_COUNT=$(
    kubectl -n "$NS" exec deploy/"$TARGET_DEPLOY" -- \
        env PGPASSWORD="$DB_PASSWORD" \
        psql -U "$DB_USER" -d "$DB" -tA -c "SELECT COUNT(*) FROM $MAIN_TABLE" 2>&1 \
        | tr -d '[:space:]'
)

if ! [[ "$PROD_COUNT" =~ ^[0-9]+$ ]]; then
    echo "✗ Не удалось получить prod COUNT — ответ: $PROD_COUNT" >&2
    exit 1
fi
echo "  prod COUNT($MAIN_TABLE) = $PROD_COUNT"
echo

# ────────────────────────────────────────────────────────────────────
# 2. Подготовить drill namespace + cleanup-trap
# ────────────────────────────────────────────────────────────────────
cleanup() {
    local rc=$?
    if [[ "$KEEP_NS" == "1" ]]; then
        echo
        echo "  DBOS_DRILL_KEEP_NS=1 — namespace $DRILL_NS оставлен для разбора."
    else
        echo
        echo "→ Cleanup: kubectl delete ns $DRILL_NS"
        kubectl delete ns "$DRILL_NS" --ignore-not-found --wait=false || true
    fi
    exit "$rc"
}
trap cleanup EXIT INT TERM

echo "→ Шаг 2: создать namespace $DRILL_NS"
if kubectl get ns "$DRILL_NS" >/dev/null 2>&1; then
    echo "  Уже существует — удаляем перед новым прогоном"
    kubectl delete ns "$DRILL_NS" --wait=true
fi
kubectl create ns "$DRILL_NS"
echo

# ────────────────────────────────────────────────────────────────────
# 3. Найти PV node + hostPath для dbos-backup-pv (local-path provisioner)
#    Drill-pod монтирует тот же hostPath read-only — это безопаснее, чем
#    клонировать PVC на 20 GiB на отдельный диск.
# ────────────────────────────────────────────────────────────────────
echo "→ Шаг 3: определить hostPath для $PVC_NAME"
PV_NAME=$(kubectl -n "$NS" get pvc "$PVC_NAME" -o jsonpath='{.spec.volumeName}')
if [[ -z "$PV_NAME" ]]; then
    echo "✗ PVC $PVC_NAME не привязан к PV" >&2
    exit 1
fi

HOST_PATH=$(kubectl get pv "$PV_NAME" -o jsonpath='{.spec.hostPath.path}{.spec.local.path}')
if [[ -z "$HOST_PATH" ]]; then
    echo "✗ Не удалось определить hostPath/local.path для PV $PV_NAME" >&2
    exit 1
fi

PV_NODE=$(kubectl get pv "$PV_NAME" -o jsonpath='{.spec.nodeAffinity.required.nodeSelectorTerms[0].matchExpressions[0].values[0]}')
if [[ -z "$PV_NODE" ]]; then
    # k3s single-node — берём первую ноду как fallback
    PV_NODE=$(kubectl get nodes -o jsonpath='{.items[0].metadata.name}')
fi

echo "  PV:        $PV_NAME"
echo "  hostPath:  $HOST_PATH"
echo "  node:      $PV_NODE"
echo

# ────────────────────────────────────────────────────────────────────
# 4. Drill postgres pod + поиск самого свежего daily-XX.dump
# ────────────────────────────────────────────────────────────────────
echo "→ Шаг 4: запустить drill-postgres + найти свежий daily dump"

DRILL_PG_PASS="drill-$(date +%s)-$$"

kubectl apply -n "$DRILL_NS" -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: drill-postgres
  labels: { app: drill-postgres }
spec:
  restartPolicy: Never
  nodeName: $PV_NODE
  securityContext:
    runAsUser: 999
    runAsGroup: 999
    fsGroup: 999
  containers:
    - name: postgres
      image: postgres:16
      env:
        - name: POSTGRES_PASSWORD
          value: "$DRILL_PG_PASS"
        - name: POSTGRES_DB
          value: "$TEMP_DB"
        - name: PGDATA
          value: /var/lib/postgresql/data/pgdata
      ports:
        - containerPort: 5432
      volumeMounts:
        - name: pgdata
          mountPath: /var/lib/postgresql/data
        - name: backup
          mountPath: /backup
          readOnly: true
      readinessProbe:
        exec:
          command: ["pg_isready", "-U", "postgres"]
        initialDelaySeconds: 3
        periodSeconds: 2
      resources:
        requests: { cpu: "200m", memory: "256Mi" }
        limits:   { cpu: "2000m", memory: "1Gi" }
  volumes:
    - name: pgdata
      emptyDir: {}
    - name: backup
      hostPath:
        path: $HOST_PATH
        type: Directory
EOF

echo "  ждём readiness drill-postgres (до 120s)..."
if ! kubectl -n "$DRILL_NS" wait --for=condition=Ready pod/drill-postgres --timeout=120s; then
    echo "✗ drill-postgres не стал Ready" >&2
    kubectl -n "$DRILL_NS" describe pod drill-postgres || true
    kubectl -n "$DRILL_NS" logs drill-postgres --tail=50 || true
    exit 1
fi

# Найти свежий daily-XX.dump (через mtime, не через slot-номер — слот может
# отставать на сутки если CronJob упал)
LATEST_DUMP=$(
    kubectl -n "$DRILL_NS" exec drill-postgres -- \
        bash -c "ls -1t /backup/$TARGET_DEPLOY/daily-*.dump 2>/dev/null | head -1" \
        | tr -d '\r'
)

if [[ -z "$LATEST_DUMP" ]]; then
    echo "✗ В /backup/$TARGET_DEPLOY/ нет daily-*.dump файлов" >&2
    kubectl -n "$DRILL_NS" exec drill-postgres -- ls -la "/backup/$TARGET_DEPLOY/" 2>&1 || true
    exit 1
fi

DUMP_SIZE=$(
    kubectl -n "$DRILL_NS" exec drill-postgres -- \
        bash -c "du -h '$LATEST_DUMP' | cut -f1" \
        | tr -d '\r'
)
DUMP_MTIME=$(
    kubectl -n "$DRILL_NS" exec drill-postgres -- \
        bash -c "stat -c '%y' '$LATEST_DUMP'" \
        | tr -d '\r'
)
echo "  dump:    $LATEST_DUMP"
echo "  size:    $DUMP_SIZE"
echo "  mtime:   $DUMP_MTIME"
echo

# ────────────────────────────────────────────────────────────────────
# 5. pg_restore --clean --if-exists в temp DB
# ────────────────────────────────────────────────────────────────────
echo "→ Шаг 5: pg_restore в drill-postgres/$TEMP_DB"

RESTORE_START=$(date +%s)

set +e
RESTORE_LOG=$(
    kubectl -n "$DRILL_NS" exec drill-postgres -- \
        env PGPASSWORD="$DRILL_PG_PASS" \
        pg_restore \
            --host=localhost \
            --username=postgres \
            --dbname="$TEMP_DB" \
            --clean --if-exists \
            --no-owner --no-acl \
            --verbose \
            "$LATEST_DUMP" 2>&1
)
RESTORE_RC=$?
set -e

RESTORE_END=$(date +%s)
RESTORE_SEC=$((RESTORE_END - RESTORE_START))

# pg_restore с --clean --if-exists на свежей БД даст ворох NOTICE про
# несуществующие объекты — это норма, но exit code должен быть 0.
if [[ $RESTORE_RC -ne 0 ]]; then
    echo "✗ pg_restore exit code $RESTORE_RC"
    echo "── pg_restore log (last 50 lines) ──"
    echo "$RESTORE_LOG" | tail -50
    echo "── pg_restore drill: FAIL ──"
    exit 1
fi
echo "  ✓ pg_restore завершён за ${RESTORE_SEC}s"
echo

# ────────────────────────────────────────────────────────────────────
# 6. Sanity: COUNT(*) FROM main_table в восстановленной БД
# ────────────────────────────────────────────────────────────────────
echo "→ Шаг 6: SELECT COUNT(*) FROM $MAIN_TABLE в восстановленной БД"
DRILL_COUNT=$(
    kubectl -n "$DRILL_NS" exec drill-postgres -- \
        env PGPASSWORD="$DRILL_PG_PASS" \
        psql -U postgres -d "$TEMP_DB" -tA -c "SELECT COUNT(*) FROM $MAIN_TABLE" 2>&1 \
        | tr -d '[:space:]'
)

if ! [[ "$DRILL_COUNT" =~ ^[0-9]+$ ]]; then
    echo "✗ Не удалось получить drill COUNT — ответ: $DRILL_COUNT" >&2
    echo "── pg_restore drill: FAIL ──"
    exit 1
fi
echo "  drill COUNT($MAIN_TABLE) = $DRILL_COUNT"
echo

# ────────────────────────────────────────────────────────────────────
# 7. Сравнение prod vs drill, дельта в пределах ±DELTA_PCT
# ────────────────────────────────────────────────────────────────────
echo "→ Шаг 7: сравнение prod vs drill"

# Дельта считается относительно prod. Если prod == 0 и drill == 0 — ок.
if [[ "$PROD_COUNT" == "0" && "$DRILL_COUNT" == "0" ]]; then
    DELTA_OK=1
    DELTA_INFO="prod=0, drill=0 — обе пустые, ок"
elif [[ "$PROD_COUNT" == "0" ]]; then
    DELTA_OK=0
    DELTA_INFO="prod=0, drill=$DRILL_COUNT — в дампе есть данные, в prod нет (странно)"
else
    DIFF=$((DRILL_COUNT - PROD_COUNT))
    DIFF_ABS=${DIFF#-}
    DELTA_NUM=$((DIFF_ABS * 100))
    DELTA_REAL=$((DELTA_NUM / PROD_COUNT))
    if [[ $DELTA_REAL -le $DELTA_PCT ]]; then
        DELTA_OK=1
    else
        DELTA_OK=0
    fi
    DELTA_INFO="prod=$PROD_COUNT, drill=$DRILL_COUNT, |Δ|=${DIFF_ABS} (${DELTA_REAL}%), порог=${DELTA_PCT}%"
fi

echo "  $DELTA_INFO"
echo
echo "──────────────────────────────────────────────────────────────"
echo " РЕЗУЛЬТАТ"
echo "──────────────────────────────────────────────────────────────"
echo " БД:               $DB"
echo " Dump:             $LATEST_DUMP"
echo " Size:             $DUMP_SIZE"
echo " Restore time:     ${RESTORE_SEC}s"
echo " prod COUNT:       $PROD_COUNT"
echo " drill COUNT:      $DRILL_COUNT"
echo " Дельта порог:     ±${DELTA_PCT}%"

if [[ $DELTA_OK -eq 1 ]]; then
    echo " Статус:           OK"
    echo "──────────────────────────────────────────────────────────────"
    cat <<'EOF'

  Интерпретация:
    Дамп восстанавливается, ключевая таблица содержит данные близкие к
    production. Backup годен для DR-сценария.

EOF
    exit 0
else
    echo " Статус:           FAIL"
    echo "──────────────────────────────────────────────────────────────"
    cat <<'EOF'

  Интерпретация FAIL:
    1. Если drill COUNT сильно меньше prod — дамп старый или урезанный.
       Проверь, что CronJob отрабатывает (kubectl -n dbos get cronjob
       pg-backup-*) и слоты daily-XX.dump обновляются.
    2. Если drill COUNT > prod заметно — prod БД могла быть сильно
       почищена (ретеншн? incident?). Проверь логи приложения и audit.
    3. Если pg_restore упал на конкретных объектах — проверь версию
       postgres image (CronJob и drill оба должны быть postgres:16),
       расхождение мажорных версий ломает custom-format dump.

  Эскалация: ИБ + DevOps. Не игнорировать.

EOF
    exit 1
fi

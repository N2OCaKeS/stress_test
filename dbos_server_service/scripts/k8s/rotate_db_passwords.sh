#!/usr/bin/env bash
# Ротация Postgres-паролей сервисных пользователей без потери доступа.
#
# Каждый из 5 postgres-deploy'ев в namespace dbos поднят с
# POSTGRES_USER=<service_user> (см. k8s/10-postgres-*.yaml и
# k8s/11/13-postgres-*.yaml) — то есть service_user сам является
# superuser'ом своей БД. Отдельного 'postgres' role нет.
# ALTER USER делается тем же service_user'ом самому себе через psql -U
# $POSTGRES_USER -d $POSTGRES_DB.
#
# Flow для каждого сервиса:
#   1. openssl rand 32 символа [A-Za-z0-9] (формат gen_secrets.sh).
#   2. ALTER USER <user> WITH PASSWORD '<new>' в нужном postgres-pod'е.
#   3. kubectl patch secret dbos-secrets — заменить <KEY>_DB_PASSWORD.
#   4. Rolling restart всех consumer'ов (service-deploy + worker если нужно).
#   5. kubectl wait rollout status.
#
# Маппинг user → consumers собран из k8s/*.yaml grep'ом secretKeyRef:
#   auth     auth_user     AUTH_DB_PASSWORD    consumers: auth-service
#   logging  logging_user  LOGGING_DB_PASSWORD consumers: logging-service
#   server   server_user   SERVER_DB_PASSWORD  consumers: server-service
#   worker   worker_user   WORKER_DB_PASSWORD  consumers: server-service, server-worker
#                                              (server-service читает worker_db
#                                              для join-доступа к worker outbox)
#   secret   secret_user   SECRET_DB_PASSWORD  consumers: secret-service
#
# Migrate-Job'ы (k8s/45-migrations.yaml) используют те же DB_PASSWORD'ы,
# но они one-shot и подхватят новое значение при следующем `make k8s-apply` /
# rollout — рестартовать их не нужно.
#
# Скрипт ИНТЕРАКТИВНЫЙ.
#
# Использование:
#   scripts/k8s/rotate_db_passwords.sh                       # все 5 (по очереди, с подтверждением)
#   scripts/k8s/rotate_db_passwords.sh --yes                 # без подтверждений (для harness/CronJob)
#   scripts/k8s/rotate_db_passwords.sh --service auth        # только один
#   scripts/k8s/rotate_db_passwords.sh --service all         # эквивалент без аргументов
#   scripts/k8s/rotate_db_passwords.sh --status              # возраст Secret'а + последний rollout
#
# Требования: kubectl, jq, openssl.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NS="${DBOS_NAMESPACE:-dbos}"
SECRET="${DBOS_SECRET_NAME:-dbos-secrets}"

ASSUME_YES="false"

# Длина DB-пароля (формат gen_secrets.sh::rand 32).
readonly RAND_DB_PASS_LEN=32

# ── Утилиты ───────────────────────────────────────────────────────────────────
# confirm / require_bin / rand_alnum / secret_set_string — в _rotation_helpers.sh.

# shellcheck source=_rotation_helpers.sh
source "$SCRIPT_DIR/_rotation_helpers.sh"

require_bin kubectl
require_bin jq
require_bin openssl

rand_pw() { rand_alnum "$RAND_DB_PASS_LEN"; }

# Когда был последний раз изменён Secret (по metadata.creationTimestamp + age).
show_status() {
    echo ""
    echo "Namespace:  $NS"
    echo "Secret:     $SECRET"
    echo ""
    echo "Возраст Secret'а:"
    kubectl -n "$NS" get secret "$SECRET" --show-managed-fields=false \
        -o jsonpath='{.metadata.creationTimestamp}{"\n"}' 2>/dev/null || true
    echo ""
    echo "Текущие rollout'ы consumer'ов:"
    for d in auth-service logging-service server-service server-worker secret-service; do
        local img
        img=$(kubectl -n "$NS" get deploy "$d" -o jsonpath='{.metadata.annotations.deployment\.kubernetes\.io/revision}' 2>/dev/null || true)
        local restarted
        restarted=$(kubectl -n "$NS" get deploy "$d" -o jsonpath='{.spec.template.metadata.annotations.kubectl\.kubernetes\.io/restartedAt}' 2>/dev/null || true)
        printf "  %-18s revision=%-4s restartedAt=%s\n" "$d" "${img:-?}" "${restarted:-<never>}"
    done
    echo ""
    echo "Postgres-pod'ы:"
    for p in auth-postgres logging-postgres server-postgres worker-postgres secret-postgres; do
        local ready
        ready=$(kubectl -n "$NS" get deploy "$p" -o jsonpath='{.status.readyReplicas}/{.status.replicas}' 2>/dev/null || true)
        printf "  %-18s ready=%s\n" "$p" "${ready:-?}"
    done
    echo ""
}

# ── Описания пяти сервисов ────────────────────────────────────────────────────
# Поля (по индексу в массивах):
#   NAME              — короткое имя (для --service)
#   PG_DEPLOY         — deploy postgres-pod'а
#   PG_USER           — service_user (он же superuser своего инстанса)
#   PG_DB             — БД, к которой логинимся для ALTER USER
#   SECRET_KEY        — ключ в dbos-secrets, который надо обновить
#   CONSUMERS         — список deploy'ев, которые читают этот пароль
#                       (запятые → пробелы при использовании)

SERVICES=(auth logging server worker secret)

declare -A PG_DEPLOY=(
    [auth]=auth-postgres
    [logging]=logging-postgres
    [server]=server-postgres
    [worker]=worker-postgres
    [secret]=secret-postgres
)
declare -A PG_USER=(
    [auth]=auth_user
    [logging]=logging_user
    [server]=server_user
    [worker]=worker_user
    [secret]=secret_user
)
declare -A PG_DB=(
    [auth]=auth_db
    [logging]=logging_db
    [server]=server_db
    [worker]=worker_db
    [secret]=secret_db
)
declare -A SECRET_KEY=(
    [auth]=AUTH_DB_PASSWORD
    [logging]=LOGGING_DB_PASSWORD
    [server]=SERVER_DB_PASSWORD
    [worker]=WORKER_DB_PASSWORD
    [secret]=SECRET_DB_PASSWORD
)
# Consumers — пробелы как разделители. server-service ходит и в server_db,
# и в worker_db (worker outbox), поэтому worker-пароль трогает оба deploy'я.
declare -A CONSUMERS=(
    [auth]="auth-service"
    [logging]="logging-service"
    [server]="server-service"
    [worker]="server-service server-worker"
    [secret]="secret-service"
)

# ── Ротация одного сервиса ────────────────────────────────────────────────────

rotate_one() {
    local svc="$1"
    local pg_deploy="${PG_DEPLOY[$svc]}"
    local pg_user="${PG_USER[$svc]}"
    local pg_db="${PG_DB[$svc]}"
    local key="${SECRET_KEY[$svc]}"
    local consumers="${CONSUMERS[$svc]}"

    echo ""
    echo "─────────────────────────────────────────────────────────────────────"
    echo "Сервис: ${svc}"
    echo "  postgres deploy:  ${pg_deploy}"
    echo "  postgres user:    ${pg_user} (superuser своего инстанса)"
    echo "  postgres db:      ${pg_db}"
    echo "  Secret key:       ${key}"
    echo "  Consumers:        ${consumers}"
    echo "─────────────────────────────────────────────────────────────────────"

    confirm "Ротировать ${svc} сейчас?" || { echo "→ Пропущен."; return 0; }

    local new_pw
    new_pw=$(rand_pw)
    if [[ ${#new_pw} -ne $RAND_DB_PASS_LEN ]]; then
        echo "ОШИБКА: rand_pw вернул не ${RAND_DB_PASS_LEN} символов (${#new_pw})." >&2
        return 1
    fi

    local ts
    ts="$(date -u +%Y%m%dT%H%M%SZ)"
    local out="/tmp/dbos-rotate-db-${svc}-${ts}.txt"
    {
        echo "DBOS DB password rotation"
        echo "timestamp: ${ts}"
        echo "service:   ${svc}"
        echo "pg_user:   ${pg_user}"
        echo "pg_db:     ${pg_db}"
        echo "secret:    ${key}"
        echo ""
        echo "NEW ${key}:"
        echo "${new_pw}"
    } > "$out"
    chmod 600 "$out"
    echo "→ Новый пароль сохранён в ${out} (chmod 600)."

    # Step 1: ALTER USER. Делаем INSIDE pod'а — POSTGRES_PASSWORD доступен как
    # env, его и берём через .pgpass (через PGPASSWORD env-переменную для psql).
    # service_user == POSTGRES_USER самого postgres-pod'а, поэтому имеет право
    # сменить свой пароль через ALTER USER current_user.
    echo "→ Step 1: ALTER USER ${pg_user} WITH PASSWORD '<new>' в ${pg_deploy}..."
    # psql берёт пароль из PGPASSWORD env-переменной; здесь это сам контейнер,
    # POSTGRES_PASSWORD уже примонтирован. Используем bash -c чтобы interpolation
    # происходила внутри pod'а, не у нас (иначе пароль попадёт в kubectl exec
    # ps tree — пусть он остаётся только в env pod'а).
    # SQL передаём через stdin чтобы не светить новое значение в exec-аргументах
    # (kubectl логирует args в audit).
    if ! printf "ALTER USER %s WITH PASSWORD %s;\n" \
            "$pg_user" "$(printf "'%s'" "${new_pw//\'/\'\'}")" \
        | kubectl -n "$NS" exec -i "deploy/${pg_deploy}" -- \
              bash -c 'PGPASSWORD="$POSTGRES_PASSWORD" psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -q'
    then
        echo "ОШИБКА: ALTER USER провалился. Secret не тронут, пароль в БД прежний." >&2
        echo "         Файл ${out} с новым (неприменённым) паролем не удаляю — посмотри сам." >&2
        return 1
    fi

    # Step 1a: post-ALTER sanity check. Логинимся в pod psql'ом с НОВЫМ паролем
    # и SELECT 1 — это catch-all для случая, когда ALTER USER вернул 0, но
    # пароль не применился (теоретически не должно быть, но дешевле проверить
    # сейчас, чем чинить упавший Secret patch потом). Пароль передаём через
    # stdin → PGPASSWORD внутри pod'а, не через kubectl exec args.
    echo "→ Step 1a: sanity-check логина с новым паролем..."
    if ! printf "%s" "$new_pw" | kubectl -n "$NS" exec -i "deploy/${pg_deploy}" -- \
            bash -c 'PGPASSWORD="$(cat)" psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -q -c "SELECT 1;" >/dev/null'
    then
        echo "ОШИБКА: ALTER USER вернул 0, но логин с новым паролем не проходит." >&2
        echo "         БД в неконсистентом состоянии. Secret НЕ патчился." >&2
        echo "         Новый пароль в ${out} — попробуй вручную:" >&2
        echo "           kubectl -n ${NS} exec -it deploy/${pg_deploy} -- psql -U ${pg_user} -d ${pg_db}" >&2
        return 1
    fi
    echo "  OK."

    # Step 2: patch Secret. Если этот шаг провалится — БД уже с новым паролем,
    # а consumer'ы ещё со старым → они зафейлят connection при следующем reconnect.
    # Поэтому patch должен идти СРАЗУ после ALTER USER, без интерактивов между.
    echo "→ Step 2: patch Secret ${SECRET}.${key}..."
    if ! secret_set_string "$key" "$new_pw"; then
        echo "ОШИБКА: patch Secret провалился. БД уже с новым паролем!" >&2
        echo "         Запиши пароль вручную из ${out} и руками сделай:" >&2
        echo "         kubectl -n ${NS} patch secret ${SECRET} --type=merge \\" >&2
        echo "           -p '{\"stringData\":{\"${key}\":\"<пароль из файла>\"}}'" >&2
        return 1
    fi

    # Step 3: rolling restart consumers. БД продолжает принимать живые
    # connection'ы со старым паролем (Postgres не разрывает существующие при
    # ALTER USER), но новые открытия пойдут только с новым → consumer'ы должны
    # подхватить новый Secret в env через restart.
    echo "→ Step 3: rolling restart consumers: ${consumers}..."
    confirm "Рестартовать ${consumers}?" || { echo "→ Pause. Запусти руками: kubectl -n ${NS} rollout restart deploy/${consumers// / deploy/}"; return 0; }

    for d in $consumers; do
        kubectl -n "$NS" rollout restart "deploy/${d}"
    done
    for d in $consumers; do
        kubectl -n "$NS" rollout status "deploy/${d}" --timeout=300s
    done

    echo ""
    echo "✓ ${svc}: пароль ротирован, consumer'ы перезапущены."
    echo "  Дубликат: ${out}.   После переноса в pwd-manager:  shred -u ${out}"
    return 0
}

# ── Парсинг аргументов ────────────────────────────────────────────────────────

TARGET=all
while [[ $# -gt 0 ]]; do
    case "$1" in
        --status)
            show_status
            exit 0
            ;;
        --service)
            shift
            TARGET="${1:-}"
            [[ -n "$TARGET" ]] || { echo "ОШИБКА: --service требует значение." >&2; exit 1; }
            shift
            ;;
        --yes|-y)
            ASSUME_YES="true"
            shift
            ;;
        -h|--help)
            sed -n '2,30p' "$0"
            exit 0
            ;;
        *)
            echo "ОШИБКА: неизвестный аргумент: $1" >&2
            exit 1
            ;;
    esac
done

# Валидация TARGET
case "$TARGET" in
    all) ;;
    auth|logging|server|worker|secret) ;;
    *)
        echo "ОШИБКА: --service должен быть одним из: auth | logging | server | worker | secret | all" >&2
        exit 1
        ;;
esac

# ── Запуск ────────────────────────────────────────────────────────────────────

echo "=== РОТАЦИЯ POSTGRES-ПАРОЛЕЙ ==="
show_status

if [[ "$TARGET" == "all" ]]; then
    echo "Будут ротированы ВСЕ 5 сервисов по очереди (с подтверждением каждого)."
    confirm "Продолжить?" || { echo "Отменено."; exit 0; }
    for svc in "${SERVICES[@]}"; do
        rotate_one "$svc" || echo "⚠ Сервис ${svc} не дошёл до конца — см. вывод выше."
    done
else
    rotate_one "$TARGET"
fi

echo ""
echo "=== Готово ==="

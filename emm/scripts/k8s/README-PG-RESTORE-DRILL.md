# pg_restore drill

Ручная проверка, что pg_dump-бэкапы из `dbos-backup-pv` реально восстанавливаются
и содержат осмысленные данные.

> Связанные скрипты:
> - `scripts/k8s/backup_pg.sh` — сам бэкап (вызывается из CronJob, см. `k8s/100-postgres-backup.yaml`).
> - `scripts/k8s/rotate_master_key.sh` — ротация SERVER_ENCRYPTION_KEY (см. `README-MASTER-KEYS.md` если присутствует).

## Зачем

CronJob `pg-backup-*` в `k8s/100-postgres-backup.yaml` гоняет `pg_dump` каждую
ночь и держит ring of slots в `dbos-backup-pv`. Сам backup-скрипт делает
sanity-проверку `pg_restore --list` (парсинг table-of-contents), но это не
гарантирует, что:

1. дамп действительно восстанавливается без ошибок,
2. внутри него лежат настоящие данные, а не пустые таблицы.

Drill ловит ровно эти два класса проблем: повреждение дампа на диске и
"пустой backup" — когда pg_dump прошёл, но запросил не ту БД / not enough
permissions / etc.

## Когда запускать

- **Quarterly** — раз в три месяца, плановая проверка восстанавливаемости.
- **После любых правок** в `scripts/k8s/backup_pg.sh` или
  `k8s/100-postgres-backup.yaml` — убедиться, что новый формат
  восстанавливается.
- **После апгрейда** postgres image (например `postgres:16` → `postgres:17`)
  — custom-format dump несовместим между мажорными версиями.
- **После инцидентов** с CronJob (OOM, недоступность PV) — даже если
  bash exit 0, дамп мог быть обрезан.

НЕ нужно гонять каждый день автоматически. CronJob не добавлен сознательно —
drill потребляет CPU и временно держит копию БД в RAM/диске; для ежедневной
проверки достаточно `pg_restore --list` в самом `backup_pg.sh`.

## Запуск

```bash
# Полный прогон для одной БД
scripts/k8s/pg_restore_drill.sh auth_db
scripts/k8s/pg_restore_drill.sh logging_db
scripts/k8s/pg_restore_drill.sh server_db
scripts/k8s/pg_restore_drill.sh worker_db
scripts/k8s/pg_restore_drill.sh secret_db

# Все пять подряд (последовательно — drill держит 1 GiB RAM)
for db in auth_db logging_db server_db worker_db secret_db; do
    scripts/k8s/pg_restore_drill.sh "$db" || echo "✗ FAIL: $db"
done

# Если хочется разобрать неудачный drill — оставить namespace
DBOS_DRILL_KEEP_NS=1 scripts/k8s/pg_restore_drill.sh auth_db
kubectl -n dbos-drill exec -it drill-postgres -- psql -U postgres -d drill_auth_db
```

## Параметры

| ENV                   | Default       | Назначение                                   |
|-----------------------|---------------|----------------------------------------------|
| `DBOS_NAMESPACE`      | `dbos`        | Namespace с prod-БД и `dbos-backup-pv`       |
| `DBOS_DRILL_NAMESPACE`| `dbos-drill`  | Временный namespace под drill-postgres       |
| `DBOS_DRILL_DELTA_PCT`| `20`          | Допустимое расхождение prod COUNT vs drill   |
| `DBOS_DRILL_KEEP_NS`  | `0`           | `1` — оставить namespace после прогона       |

Дельта 20% по умолчанию — учёт того, что между моментом ночного backup'а и
запуском drill (днём) база накапливает свежие записи. Для logging_db
(audit_events) можно увеличить — поток событий вырастает быстрее.

## Что проверяется

Для каждой БД sanity-таблица:

| БД          | Deploy            | Таблица         |
|-------------|-------------------|-----------------|
| auth_db     | auth-postgres     | `users`         |
| logging_db  | logging-postgres  | `audit_events`  |
| server_db   | server-postgres   | `servers`       |
| worker_db   | worker-postgres   | `tasks`         |
| secret_db   | secret-postgres   | `credentials`   |

Алгоритм:

1. Считать `COUNT(*) FROM <main_table>` в prod (single read-only SELECT).
2. Создать namespace `dbos-drill`, поднять `drill-postgres` (postgres:16,
   emptyDir под PGDATA, hostPath read-only mount каталога backup-PV).
3. Найти самый свежий `daily-XX.dump` в `dbos-backup-pv/<target-deploy>/`
   (по mtime — не по slot-номеру, чтобы не наткнуться на старый слот, если
   CronJob ронялся вчера).
4. `pg_restore --clean --if-exists --no-owner --no-acl` в drill-postgres.
5. `COUNT(*) FROM <main_table>` в восстановленной БД.
6. Сравнить с prod: `|drill - prod| / prod ≤ DELTA_PCT`.
7. `kubectl delete ns dbos-drill` (через trap EXIT, даже на FAIL).

## Интерпретация результата

### Статус OK

Дамп восстанавливается, ключевая таблица содержит данные близкие к
production. Backup годен для DR-сценария. Фиксируется в плане SMIB
(см. `obsidian/infra/Production checklist.md`).

### Статус FAIL — что делать

В порядке вероятности:

1. **drill COUNT << prod COUNT** (например 0 при prod = 12000).
   - Backup устарел: CronJob не отрабатывает.
   - Проверить: `kubectl -n dbos get cronjob pg-backup-*`,
     `kubectl -n dbos get jobs -l app=pg-backup --sort-by=.metadata.creationTimestamp`.
   - Проверить логи последнего Job: `kubectl -n dbos logs job/<name>`.
   - Проверить размер PV: `kubectl -n dbos exec deploy/auth-postgres -- df -h /backup`
     (если 100% — backup усечён).

2. **pg_restore exit code ≠ 0**.
   - Несовместимая мажорная версия postgres между источником и drill —
     поднять `postgres:16` на обоих концах (и в CronJob и в drill).
   - Дамп физически повреждён (битый диск под local-path PV). Сменить
     дамп на `weekly-NN.dump` или `monthly-NN.dump` (передать через
     ручной `pg_restore` отдельно).

3. **drill COUNT >> prod COUNT** (например drill = 1M, prod = 100).
   - Prod-БД была случайно почищена. Проверить `audit_events`
     в logging_db, проверить миграции (`kubectl -n dbos get jobs -l
     component=migration`), проверить логи приложения за период.
   - Это **боевой инцидент** — эскалация на ИБ.

4. **Drill вообще не запустился (pod не Ready)**.
   - Проверить, что node имеет место (`df -h /var/lib/rancher`).
   - Проверить image-pull (`postgres:16` может быть не закеширован
     на closed network ноде; либо использовать local registry).

### Эскалация

Любой FAIL фиксируется в:

1. Тикет в трекере (с ссылкой на полный output drill).
2. Уведомление ИБ (`audit_events` в logging_db — drill сам по себе аудита
   не пишет, это manual-only скрипт).
3. Чек-лист `obsidian/infra/Production checklist.md` — отметить, что
   квартальный drill провалился.

## Безопасность

- Скрипт **только читает** prod-БД (один SELECT COUNT на каждый запуск).
- Drill-postgres поднимается в **отдельном namespace** с собственным
  `POSTGRES_PASSWORD` (одноразовый, не сохраняется).
- Backup-PV монтируется в drill-pod **read-only** (`readOnly: true`).
- После завершения namespace `dbos-drill` удаляется (`trap EXIT`); даже
  при panic'e pod не остаётся.
- Restore-дамп уходит в `emptyDir` drill-pod'а — после `delete ns`
  данные исчезают вместе с pod'ом.

## Что НЕ делает drill

- Не пишет аудит-события (по дизайну; запускается руками, фиксируется в
  трекере).
- Не проверяет `weekly-NN.dump` / `monthly-NN.dump` — только `daily-NN`.
  Для weekly/monthly можно вручную: оставить namespace через
  `DBOS_DRILL_KEEP_NS=1`, потом `pg_restore` из drill-pod'а по нужному
  файлу.
- Не сравнивает схему (DDL) между prod и drill. Если нужно — `pg_dump
  --schema-only` обеих БД и `diff` локально.
- Не добавлен в CronJob и Makefile сознательно. Только manual.

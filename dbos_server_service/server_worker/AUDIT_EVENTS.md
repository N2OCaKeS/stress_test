# AUDIT_EVENTS — server_worker

Аудит-события, которые worker эмитит в `loging_service` через
`audit_outbox` (см. `services/audit_outbox_publisher.py`). Большинство
событий пишется под `audit_action` конкретного handler'а
(`server.power_on`, `ipmi_controller.password_rotate`,
`server_account.password_rotate`, и т.д.) — их каталоги живут в
`server_service/AUDIT_EVENTS.md` рядом с источником запроса.

Здесь перечислены **runner-level meta-события** — те, которые
`tasks/_runner.py` пишет от своего имени, без вызова `impl`. Они
указывают на проблему с самой task'ой (отмена, удаление row'а,
повторный enqueue), а не на ошибку бизнес-логики.

**Severity:** `default_severity` относится к успешному завершению.
Для failure-веток ниже severity указан явно (в коде runner'а), потому
что runner-meta-события почти всегда status=failure.

Все события используют схему naming: `<entity>.<verb>`. Lifecycle —
`service.<verb>`. Source-of-truth для runner-веток — `tasks/_runner.py`.

---

## Runner meta-events

| action | severity | эмитится при | target_type | детали (`details`) |
|---|---|---|---|---|
| `task.deleted_midrun` | WARNING | row задачи удалён из `tasks` между `mark_running` и terminal write (retention cleanup, ручной DELETE, автотест) — terminal `mark_succeeded`/`mark_failed` пропускается, retry не шедулится | `audit_target_type` handler'а (`server`, `ipmi_controller`, …), либо `None` если handler не задал | `task_id` (str), `original_action` (str — `audit_action` handler'а, например `server.power_on`), `phase` (`"success"` — на happy-path; на failure-path поле опускается), `reason` = `"task_deleted_midrun"`, `error` (str, redacted — только на failure-path), `attempt` (int, только на failure-path), `max_attempts` (int, только на failure-path) |

### Прочие runner-meta под `audit_action` handler'а

Эти ветки пишут event под именем handler'а (`audit_action`,
переданным в `run_task`), но с `details.reason`, обозначающим
runner-meta-причину:

| details.reason | severity | условие | дополнительные details |
|---|---|---|---|
| `task_not_found` | ERROR | `get_by_id` вернул None на старте (task_id из брокера, в БД row нет) | `target_type: "task"`, `target_id: <task_id>` |
| `duplicate_dispatch` | WARNING | `mark_running` CAS отбил task в нестандартном статусе (already running / terminal / cancelled) — повторный enqueue или race двух worker'ов | `observed_status` (str) |
| `task_cancelled` | WARNING | оператор успел дёрнуть `POST /tasks/{id}/cancel` до того, как worker подобрал сообщение из Redis — task в `cancelled` ещё до `mark_running` | `observed_status`, опц. `cancelled_by` (str), `cancel_reason` (str). `timestamp` audit-event'а переопределяется на `task.cancelled_at` |
| `cancelled_midrun` | WARNING | оператор отменил task пока `impl` работал — `mark_succeeded` / `mark_failed` / `mark_pending_for_retry` CAS отбили запись (status=cancelled в БД), retry не шедулится | `observed_status: "cancelled"`, `will_retry: False`, `attempt`, `max_attempts`, опц. `cancelled_by`, `cancel_reason`, `error` (на failure-path). `timestamp` override на `cancelled_at` |

---

## Handler-level events

Сами handler'ы (`tasks/power.py`, `tasks/passwords.py`,
`tasks/inventory.py`, `tasks/prepare.py`, `tasks/installed_packages.py`,
`tasks/users.py`, `tasks/secrets_reencrypt.py`) пишут события под
своими `audit_action`. Severity-defaults и описания полей — в
`server_service/AUDIT_EVENTS.md`: эти actions зарегистрированы как
принадлежащие `server_service`, потому что именно server_service дёргает
worker'а и владеет бизнес-смыслом операции.

Реестр `audit_action`, эмитируемых handler'ами (source-of-truth — поле
`audit_action=` в `tasks/*.py`):

| action | source | target_type |
|---|---|---|
| `server.power_on` | `tasks/power.py` | `server` |
| `server.power_off` | `tasks/power.py` | `server` |
| `server.power_reboot` | `tasks/power.py` | `server` |
| `server.power_status` | `tasks/power.py` | `server` |
| `server.inventory_sync` | `tasks/inventory.py` | `server` |
| `server.prepare` | `tasks/prepare.py` | `server` |
| `installed_packages.list` | `tasks/installed_packages.py` | `server` |
| `server_account.provision` | `tasks/users.py` | `server_account` |
| `server_account.update_on_host` | `tasks/users.py` | `server_account` |
| `server_account.deprovision` | `tasks/users.py` | `server_account` |
| `server_account.users_inventory` | `tasks/users.py` | `server_account` |
| `server_account.password_rotate` | `tasks/passwords.py` | `server_account` |
| `ipmi_controller.password_rotate` | `tasks/passwords.py` | `ipmi_controller` |

---

## Worker-lifecycle events

События, которые `main.py` и CLI пишут вне `_runner.run_task` — они
относятся к самому процессу worker'а, а не к конкретной task'е.

| action | severity | эмитится при | target_type | детали (`details`) |
|---|---|---|---|---|
| `task.worker_shutdown` | ERROR (или WARNING если задача уйдёт в retry) | graceful shutdown worker'а — все живые task'и переводятся в `failed` (либо retry если `attempt < max_attempts`), чтобы scheduler/другой реплика подхватили | `task` | `task_id`, `reason="worker_shutdown"`, `attempt`, `max_attempts`, `will_retry` |
| `task.worker_orphaned` | ERROR | sweep'ер нашёл task'у, у которой `worker_id` не отвечает heartbeat'ом (упавший процесс) — task принудительно `failed`, retry-decision не делается, оператор разбирается вручную | `task` | `task_id`, `reason="worker_orphaned"`, `worker_id`, `attempt`, `max_attempts` |
| `secrets.reencrypt_tick` | INFO (success), WARNING (errors>0), ERROR (app_env mismatch) | каждый тик `secrets.reencrypt_lazy` — даже когда работы нет (skip/idle), чтобы видеть пульс ротации ключей | `secret` | `processed`, `skipped`, `errors`, `claimed`, `seeded`, `remaining_before`, `active_version`, `batch_size`. Либо `skipped=True` + `reason` (`active_tasks_present` / `app_env_mismatch`) на ранних exit'ах |
| `audit.outbox_reattempt_manual` | WARNING | CLI-команда `outbox-reattempt` — оператор форсит повторную доставку конкретной row'ы из `audit_outbox`. Severity WARNING — manual-интервенция в audit-pipeline | `audit_outbox` | `row_id`, `reason` (оператор пишет, зачем), `source="cli"` |

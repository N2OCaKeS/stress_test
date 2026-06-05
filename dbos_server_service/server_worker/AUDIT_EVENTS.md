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
| `task.deleted_midrun` | WARNING | row задачи удалён из `tasks` между `mark_running` и terminal write (retention cleanup, ручной DELETE, автотест) — terminal `mark_succeeded`/`mark_failed` пропускается, retry не шедулится | `audit_target_type` handler'а (`server`, `ipmi_controller`, …), либо `None` если handler не задал | `task_id` (str), `original_action` (str — `audit_action` handler'а, например `server.power_on`; присутствует и на success-, и на failure-path), `phase` (`"success"` — только на happy-path; на failure-path не выставляется), `reason` = `"task_deleted_midrun"`, `error` (str, redacted — только на failure-path), `attempt` (int, только на failure-path), `max_attempts` (int, только на failure-path) |

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
| `server_account.users_inventory` | `tasks/users.py` | `server` |
| `server_account.password_rotate` | `tasks/passwords.py` | `server_account` |
| `ipmi_controller.password_rotate` | `tasks/passwords.py` | `ipmi_controller` |
| `bmc.tls_downgrade` | `clients/__init__.py` | `ipmi_controller` |

`server_account.users_inventory` — `target_type=server` (а не `server_account`), потому что inventory снимает срез всех ОС-пользователей хоста, а не работает с конкретной учёткой; ключ корреляции в audit'е — `server_id`. Симметрично соседнему `server.inventory_sync` (`tasks/inventory.py`).

`bmc.tls_downgrade` — отдельное worker-level WARNING, эмитится из `_probe_redfish_cascade` при каждом фактическом переходе на менее защищённый канал BMC: `https_verify → https_noverify` (self-signed cert или MITM-подозрение) и `https_verify → http` / `https_noverify → http` (legacy BMC без TLS). Severity всегда `WARNING`, status `success`, `actor_type=service` (явный override в payload — остальные worker-actions полагаются на дефолт `service` из `audit_client.emit`), `target_type=ipmi_controller`, `target_id` совпадает с `details.host`. Поля `details`: `host` (host[:port] BMC), `from` (`https_verify` | `https_noverify`), `to` (`https_noverify` | `http`). Эмит через transactional outbox (`enqueue_audit`); при недоступности outbox event теряется silent — probe-loop не должен крэшить из-за audit'а.

На failure-ветке `ipmi_controller.password_rotate`, когда BMC принял пароль
(apply прошёл), но read-only verify под новым паролем не сработал, runner
проставляет `error_code="BMC_VERIFY_AFTER_ROTATE_FAILED"` и кладёт
`details.phase`:

- `verify_first_attempt` — упала первая попытка verify сразу после apply
  (мгновенный transient / лёгкий NTP-drift, до 1s sleep между попытками);
- `verify_retry` — упала и повторная попытка verify через 1s паузу. Storage
  с BMC точно разъехался: оператор либо ждёт следующий retry task'и, либо
  идёт чинить NTP / BMC.

Transport-уровень (`BMC_AUTH_FAILED` / `BMC_UNREACHABLE` / `BMC_TIMEOUT`)
пробрасывается через `__cause__` (`raise ... from exc`), читается из
`task.last_error`.

---

## Worker-lifecycle events

События, которые `main.py` и CLI пишут вне `_runner.run_task` — они
относятся к самому процессу worker'а, а не к конкретной task'е.

| action | severity | эмитится при | target_type | детали (`details`) |
|---|---|---|---|---|
| `task.worker_shutdown` | ERROR (или WARNING если задача уйдёт в retry) | graceful shutdown worker'а — все живые task'и переводятся в `failed` (либо retry если `attempt < max_attempts`), чтобы scheduler/другой реплика подхватили | `task` (canonical: `target_id = task_id`) | `task_id`, `reason="worker_shutdown"`, `attempt`, `max_attempts`, `will_retry`, опц. `server_id` (если task несла `target_server_id`) |
| `task.worker_orphaned` | ERROR | sweep'ер нашёл task'у, у которой `worker_id` не отвечает heartbeat'ом (упавший процесс) — task принудительно `failed`, retry-decision не делается, оператор разбирается вручную | `task` (canonical: `target_id = task_id`) | `task_id`, `reason="worker_orphaned"`, `worker_id`, `attempt`, `max_attempts`, опц. `server_id` (если task несла `target_server_id`) |
| `secrets.reencrypt_tick` | INFO (success, `allowed=True`), WARNING (status=`warning`, `errors>0`, `allowed=False`, `reason="finalize_errors"`), ERROR (status=`failure`, `allowed=False`, `reason="app_env_mismatch"`) | каждый тик `secrets.reencrypt_lazy` — даже когда работы нет (skip/idle), чтобы видеть пульс ротации ключей | `secret` | `processed`, `skipped`, `errors`, `claimed`, `seeded`, `remaining_before`, `active_version`, `batch_size`. На partial-failure-path добавляется `reason="finalize_errors"` и `allowed=False`, чтобы scheduler/sweep отличали чистый success от warning'а по одному полю. Либо `skipped=True` + `reason` (`active_tasks_present` / `app_env_mismatch`) на ранних exit'ах; `app_env_mismatch` дополнительно несёт `worker_app_env` и `server_service_app_env` и эмитится с `allowed=False`. |
| `audit.outbox_reattempt_manual` | WARNING | CLI-команда `outbox-reattempt` — оператор форсит повторную доставку конкретной row'ы из `audit_outbox`. Severity WARNING — manual-интервенция в audit-pipeline | `audit_outbox` | `row_id`, `reason` (оператор пишет, зачем), `source="cli"`. На emit'е заполняются `actor_id` (`--actor-id` CLI), `actor_type="operator"`, `target_id=row_id`, `severity=WARNING` явным полем (не из default-таблицы) |

`dispatch_outbox` publisher (`tasks/dispatch_outbox.py`) audit-событий **не эмитит**: он читает строки `dispatch_outbox` из server_service-БД и кикает taskiq-задачи — это внутренний fanout, не бизнес-операция. Видимость наблюдается через worker-логи (`reached attempts cap`, backoff-warnings) и счётчики publisher'а. Если оператор ищет в этой таблице `dispatch_outbox.*` — таких action'ов нет by design.

`secrets.reencrypt_lazy` (taskiq-периодика, `main.py::secrets_reencrypt_lazy`) собственного action'а **не имеет** — каждый её тик пишет одно audit-событие `secrets.reencrypt_tick` (см. строку выше). Имя `secrets.reencrypt_lazy` встречается только в worker-логах (`secrets.reencrypt_lazy: ...`) и в названии cron-job'ы в `core/config.py`. SIEM-правила пишутся по `action=secrets.reencrypt_tick`.

---

## Publisher-side детали

`_publish_one` инкрементит `attempts` ДО ветвления на permanent_4xx /
poison / transient. Для 4xx это **информационная метрика** (row уходит в
DLQ с `attempts=1`, не с `0`, чтобы по полю было видно «попытка реально
была сделана», а row не выглядел «ещё не пробованным»). Для transient'а
и poison-cap'а — реальный счётчик retry'ев, по которому работают
`_maybe_poison` и `_apply_backoff`. Контракт зафиксирован тестом
`test_emit_4xx_sends_row_to_dlq`: после 4xx ожидается `attempts=1` и
`reason="permanent_4xx"` в DLQ-логе.

---

## Threat model — known accepted risks

Аудитная пометка про два класса рисков, которые периодически выплывают в
security-аудитах кода и принимаются как осознанные trade-off'ы на текущем
стенде. Полный разбор и обоснование — в `obsidian/services/server_worker.md`
секция **Threat model**.

- **IPMI new password в argv `ipmitool user set password`**
  (`src/clients/ipmitool.py:232`). Новый пароль уходит последним positional
  argument'ом, виден в `/proc/<pid>/cmdline` на время вызова. Mitigations:
  `_mask_password_in_argv` в logs/audit/last_error, минимальный child env,
  Redfish-путь без argv для iDRAC/iLO. Accepted в закрытой management-сети.
- **Plaintext-пароли в Redis-stash** (`src/tasks/passwords.py:222`,
  `src/tasks/users.py:95`, `src/tasks/prepare.py:119`). Value хранится как JSON
  без envelope-шифрования. Mitigations: TTL, обязательный redis AUTH в prod,
  явный DELETE после submit, неугадываемые ключи. Envelope-шифрование не
  реализовано — worker не держит `SERVER_ENCRYPTION_KEY` (граница: шифрование
  секретов живёт в `server_service`). Owner-decision: accepted.

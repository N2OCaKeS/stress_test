"""Обёртка над task lifecycle — DB-статус + audit.

Архитектура lifecycle.

Durable retry:

* На retry-path записываем `tasks.scheduled_retry_at = now + back-off`
  перед запуском `_schedule_retry` (fire-and-forget). Если worker умрёт
  во время `asyncio.sleep`, `_recover_scheduled_retries` на следующем
  startup'е увидит `scheduled_retry_at <= now()` и сделает `kiq` —
  устраняет «навсегда queued» после краха.
* `mark_running` пишет `tasks.worker_id` (для sweep'а), а
  `mark_pending_for_retry` обнуляет — task между попытками «ничейная»,
  не считается orphan'ом.

  ┌─ session 1 (task lookup + mark_running) ────┐
  │   load Task; mark_running; commit            │
  └──────────────────────────────────────────────┘
                       │
                       ▼
                impl(payload)               ← бизнес-логика (idrac/ssh/...)
                       │
                       ▼
  ┌─ session 2 (terminal status + outbox) ──────┐
  │   load fresh Task (FOR UPDATE-эквивалент)   │
  │   mark_succeeded | mark_failed              │
  │   INSERT audit event INTO audit_outbox      │
  │   commit  ← ATOMIC: status и audit вместе   │
  └──────────────────────────────────────────────┘
                       │
                       ▼
              flush_outbox (best-effort just-in-time)

Гарантии:

* Если task в DB `succeeded`/`failed` — соответствующая audit-row в
  `audit_outbox` гарантированно существует (один commit). SIGKILL после
  commit'а не теряет audit — publisher отправит при следующем проходе.
* `flush_outbox()` после commit'а — оптимизация latency (доставка в
  loging_service «обычно сразу»); если она упадёт, фоновый publisher
  всё равно довезёт. Никогда не блокирует mark_succeeded/failed.

Что *не* гарантирует код здесь:

* Дедупликация на стороне loging_service — это его ответственность
  (`request_id` + `action` + `timestamp` идемпотентны на ingest).
* Параллельные воркеры на одном task_id — это исключено taskiq-broker'ом
  (по дизайну — один consumer на сообщение).

Retry / back-off:

* `mark_running` теперь CAS (UPDATE WHERE status='queued' RETURNING).
  Если task в нестандартном статусе (уже-running/succeeded/failed) —
  возвращает None, impl не вызывается, audit «duplicate_dispatch».
  Защита от повторного enqueue одного task_id (server_service dispatch
  retry / Redis re-delivery).
* На exception в impl, если `attempt < max_attempts` — task переводится
  обратно в `queued` (mark_pending_for_retry) и шедулится re-kick через
  asyncio.create_task с back-off `10s * 2^(attempt-1)` capped 300s.
  Финальный фейл (`attempt >= max_attempts`) — mark_failed как раньше.
* Активная running task регистрируется в `runner_state.running_tasks`
  для graceful shutdown (см. `_runner_state.py`).
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable

from src.core.constants import TaskStatus
from src.db.session import AsyncSessionLocal
from src.repositories import task as task_repo
from src.services import audit_outbox_publisher
from src.tasks._runner_state import (
    get_worker_id,
    register_running_task,
    unregister_running_task,
)
from src.utils.redaction import redact_error_message

logger = logging.getLogger(__name__)

# Базовый back-off для retry. 10 секунд × 2^(attempt-1) с потолком 300s
# (5 минут). Для max_attempts=3 фактические паузы: 10s, 20s.
_RETRY_BASE_DELAY_SECONDS = 10.0
_RETRY_MAX_DELAY_SECONDS = 300.0

# Сильные ссылки на фоновые retry-таски. `asyncio.create_task` сам по себе
# держит на task только weakref через event loop, поэтому на длинном back-off
# (20с+) сборщик может собрать задачу до того, как она проснётся и сделает
# re-kick. Кладём handle сюда на время жизни и снимаем по done-callback.
_RETRY_TASKS: set[asyncio.Task] = set()


def _filter_result_for_audit(
    result: dict | None,
    safe_fields: set[str] | None,
) -> dict | object:
    """Применить whitelist к handler-result перед записью в audit.

    Правила:

    * `result is None` → возвращаем `None` (так и пишем в audit, как и
      раньше — тест `test_result_can_be_none` фиксирует поведение).
    * `safe_fields is None` (handler не объявил whitelist) → возвращаем
      sentinel-dict `{"emitted": False, "reason": "no_whitelist"}`.
      По умолчанию секреты НЕ уходят в audit, даже ценой потери
      операционной информации.
    * `safe_fields` объявлен → фильтруем по ключам. Не входящие в
      whitelist значения отбрасываются без следов (нет «redacted»-
      плейсхолдеров, чтобы наличие/отсутствие поля не намекало на сам
      факт его существования).
    * `result` не dict (странный handler) → возвращаем sentinel.
    """
    if result is None:
        return None
    if safe_fields is None:
        return {
            "emitted": False,
            "reason": "no_whitelist",
            "result_type": type(result).__name__,
        }
    if not isinstance(result, dict):
        return {
            "emitted": False,
            "reason": "result_not_dict",
            "result_type": type(result).__name__,
        }
    return {k: v for k, v in result.items() if k in safe_fields}


def _attach_cancel_metadata(details: dict, task) -> None:
    """Доклеить `cancelled_by`/`cancel_reason` в details cancel-audit'а.

    Поля приходят из `POST /tasks/{id}/cancel` (см. migration 0005). Если
    оператор не указал reason или server_service не пробросил actor_id — в
    DB лежит NULL, в audit такие поля просто не попадают: пустые значения
    в audit-payload только захламляют дашборды.
    """
    if task is None:
        return
    cancelled_by = getattr(task, "cancelled_by", None)
    if cancelled_by is not None:
        details["cancelled_by"] = cancelled_by
    cancel_reason = getattr(task, "cancel_reason", None)
    if cancel_reason is not None:
        details["cancel_reason"] = cancel_reason


async def run_task(
    task_id: str,
    *,
    audit_action: str,
    audit_target_type: str | None = None,
    impl: Callable[[dict], Awaitable[dict | None]],
    audit_safe_fields: set[str] | None = None,
) -> None:
    """Общий task lifecycle.

      1. подгрузить Task row по id;
      2. mark_running (CAS), commit (session 1);
      3. вызвать `impl(payload)` и забрать result;
      4. mark_succeeded / mark_failed / mark_pending_for_retry И INSERT
         audit_outbox row в той же session, commit (session 2 — atomic);
      5. best-effort flush outbox'а (just-in-time publish).

    `impl` — kind-specific бизнес-логика (idrac/ssh/HTTP). На вход
    получает `payload` task'и, возвращает `result` dict (тот пишется
    в task-row и форвардится в audit details *после* whitelist'а).

    `audit_safe_fields` — whitelist ключей из `result`, которые можно
    положить в `audit details.result`. Если None — handler не разрешил
    эмит, в audit пойдёт sentinel `{emitted: False, reason: ...}`. См.
    `_filter_result_for_audit`.

    CAS на mark_running: если task в `running`/`succeeded`/`failed`/
    `cancelled` (повторный enqueue, race с другим worker'ом) — impl
    НЕ вызывается, пишется audit «duplicate_dispatch». Атомарно
    защищает от двойного запуска одной task'и.
    """
    # ── session 1: task lookup + mark_running (CAS) ──────────────────────
    #
    # Отдельная сессия, потому что impl(payload) может выполняться
    # минутами (inventory.sync на медленном SSH, IPMI-reboot) — держать
    # транзакцию открытой всё это время = pool exhaustion и блокировка `tasks` row'у.
    #
    # task_not_found — особый случай: пишем audit-outbox без mark_running
    # (соответствующего Task в DB нет).
    async with AsyncSessionLocal() as session:
        task = await task_repo.get_by_id(session, task_id)
        if task is None:
            await task_repo.enqueue_audit(
                session,
                task_id=None,
                payload={
                    "action": audit_action,
                    "status": "failure",
                    "allowed": False,
                    "target_id": task_id,
                    "target_type": "task",
                    "details": {"reason": "task_not_found"},
                    "severity": "ERROR",
                },
            )
            await session.commit()
            # Не упадём, если publisher временно недоступен — outbox
            # удержит событие.
            await _safe_flush_outbox()
            return

        # «Снимок» полей до CAS — нужны и для duplicate-dispatch audit,
        # и для happy/failure path'ей ниже.
        target_id = task.target_server_id or task_id
        request_id = task.request_id
        actor_id = task.created_by
        payload_for_impl = task.payload or {}
        task_kind = task.task_kind
        max_attempts = task.max_attempts
        current_status = task.status

        # Cancel fast-path: оператор успел дёрнуть
        # `POST /tasks/{id}/cancel` после dispatch'а, но до того, как
        # worker подобрал сообщение из Redis. Status уже cancelled —
        # `mark_running` CAS всё равно отбил бы её (фильтр по
        # `status='queued'`), но мы хотим явный audit-event и log,
        # а не общий `duplicate_dispatch`.
        if current_status == TaskStatus.CANCELLED:
            logger.info(
                "task_id=%s status=cancelled — skipping dispatch (operator cancel)",
                task_id,
            )
            cancel_details = {
                "task_id": task_id,
                "reason": "task_cancelled",
                "observed_status": current_status,
            }
            _attach_cancel_metadata(cancel_details, task)
            await task_repo.enqueue_audit(
                session,
                task_id=task_id,
                payload={
                    "action": audit_action,
                    "status": "failure",
                    "allowed": False,
                    "target_id": target_id,
                    "target_type": audit_target_type,
                    "request_id": request_id,
                    "actor_id": actor_id,
                    "details": cancel_details,
                    "severity": "WARNING",
                },
            )
            await session.commit()
            await _safe_flush_outbox()
            return

        marked = await task_repo.mark_running(
            session, task, worker_id=get_worker_id(),
        )
        if marked is None:
            # CAS отказал — task в нестандартном статусе. impl НЕ
            # вызываем, статус НЕ меняем. Пишем audit «duplicate_dispatch»
            # чтобы оператор видел повторный enqueue.
            logger.warning(
                "mark_running CAS rejected task_id=%s current_status=%s "
                "(duplicate dispatch or stale enqueue)",
                task_id,
                current_status,
            )
            await task_repo.enqueue_audit(
                session,
                task_id=task_id,
                payload={
                    "action": audit_action,
                    "status": "failure",
                    "allowed": False,
                    "target_id": target_id,
                    "target_type": audit_target_type,
                    "request_id": request_id,
                    "actor_id": actor_id,
                    "details": {
                        "task_id": task_id,
                        "reason": "duplicate_dispatch",
                        "observed_status": current_status,
                    },
                    "severity": "WARNING",
                },
            )
            await session.commit()
            await _safe_flush_outbox()
            return

        # Текущий attempt (после инкремента в mark_running) — нужен для
        # retry decision.
        current_attempt = task.attempt

        # Регистрируем task в process-local state ДО commit'а mark_running.
        # Иначе между commit'ом (row уже `running` в БД) и входом в impl
        # есть окно, где SIGTERM-drain не видит задачу в RUNNING_TASKS и
        # она зависает `running` до orphan-sweep'а. add до commit'а — drain
        # увидит её в любом случае; discard гарантируем finally ниже
        # (он же отрабатывает, если сам commit бросит исключение).
        register_running_task(task_id)
        try:
            await session.commit()
        except Exception:
            # commit упал — row не стал `running`, снимаем преждевременную
            # регистрацию и пробрасываем дальше (taskiq зачтёт фейл task'и).
            unregister_running_task(task_id)
            raise

    # ── impl: бизнес-логика out-of-transaction ──────────────────────────
    # Задача уже в RUNNING_TASKS (см. выше). finally снимает регистрацию
    # независимо от happy/failure/retry — graceful shutdown к этому моменту
    # либо уже финализировал её, либо больше не должен трогать.
    try:
        try:
            result = await impl(payload_for_impl)
        except Exception as exc:  # noqa: BLE001 — surface error verbatim into DB
            # Реальные клиенты (sushy/asyncssh/pyghmi/ipmitool) могут зашить
            # creds в текст ошибки. Перед записью в task.last_error и
            # audit details.error прогоняем через redact_error_message.
            error_message = redact_error_message(f"{type(exc).__name__}: {exc}")

            # Retry decision: ещё есть попытки → mark_pending_for_retry +
            # schedule re-kick через back-off. Иначе terminal mark_failed.
            should_retry = current_attempt < max_attempts

            # Durable retry scheduling: считаем `scheduled_retry_at` ДО
            # открытия session 2 — одно и то же значение в БД и в
            # `_schedule_retry`. Если процесс умрёт после commit'а, но
            # до `asyncio.create_task`, startup-recovery подхватит row
            # по `scheduled_retry_at <= now()`.
            retry_delay = _compute_backoff_delay(current_attempt) if should_retry else 0.0
            scheduled_retry_at = (
                datetime.fromtimestamp(
                    datetime.now(timezone.utc).timestamp() + retry_delay,
                    tz=timezone.utc,
                )
                if should_retry
                else None
            )

            # ── session 2 (failure path): mark_* + audit_outbox atomic ──
            #
            # `mark_*` теперь CAS на ``status != 'cancelled'``: если
            # оператор успел дёрнуть cancel пока impl падал — terminal
            # write пропускается, retry не шедулится, audit идёт с
            # `observed_status=cancelled`. Без этого guard'а cancelled
            # row перетёрся бы failed/queued и cancel был бы потерян.
            cancelled_midrun = False
            async with AsyncSessionLocal() as fail_session:
                fresh = await task_repo.get_by_id(fail_session, task_id)
                if fresh is not None:
                    if should_retry:
                        marked = await task_repo.mark_pending_for_retry(
                            fail_session,
                            fresh,
                            error_message,
                            scheduled_retry_at=scheduled_retry_at,
                        )
                    else:
                        marked = await task_repo.mark_failed(
                            fail_session, fresh, error_message
                        )
                    if marked is None:
                        cancelled_midrun = True
                        logger.warning(
                            "task cancelled mid-run, terminal write skipped "
                            "task_id=%s should_retry=%s",
                            task_id,
                            should_retry,
                        )
                # severity отличается: retry — WARNING (transient),
                # exhausted — ERROR (terminal). Status в audit во всех
                # случаях — failure (impl упал именно сейчас), но детали
                # говорят оператору про будущий retry.
                #
                # cancelled_midrun: row уже cancelled, terminal write
                # пропустили. Audit пишем со статусом failure (impl
                # действительно упал), но will_retry=False и
                # observed_status=cancelled — оператору сразу видно,
                # что cancel случился во время выполнения.
                audit_status = "failure"
                if cancelled_midrun:
                    audit_severity = "WARNING"
                    # Перечитываем row: cancel мог прийти между первым
                    # `get_by_id` и `mark_*` (CAS), тогда у `fresh`
                    # cancel-поля ещё пустые. Лишний SELECT на уже редкой
                    # ветке не страшен — зато в audit попадёт актуальный
                    # `cancelled_by` / `cancel_reason`.
                    refreshed = await task_repo.get_by_id(fail_session, task_id)
                    audit_details = {
                        "task_id": task_id,
                        "error": error_message,
                        "attempt": current_attempt,
                        "max_attempts": max_attempts,
                        "will_retry": False,
                        "observed_status": TaskStatus.CANCELLED.value,
                        "reason": "cancelled_midrun",
                    }
                    _attach_cancel_metadata(audit_details, refreshed)
                elif should_retry:
                    audit_severity = "WARNING"
                    audit_details = {
                        "task_id": task_id,
                        "error": error_message,
                        "attempt": current_attempt,
                        "max_attempts": max_attempts,
                        "will_retry": True,
                    }
                else:
                    audit_severity = "ERROR"
                    audit_details = {
                        "task_id": task_id,
                        "error": error_message,
                        "attempt": current_attempt,
                        "max_attempts": max_attempts,
                        "will_retry": False,
                    }
                await task_repo.enqueue_audit(
                    fail_session,
                    task_id=task_id,
                    payload={
                        "action": audit_action,
                        "status": audit_status,
                        "allowed": False,
                        "target_id": target_id,
                        "target_type": audit_target_type,
                        "request_id": request_id,
                        "actor_id": actor_id,
                        "details": audit_details,
                        "severity": audit_severity,
                    },
                )
                await fail_session.commit()

            await _safe_flush_outbox()

            # Schedule re-kick после commit'а — иначе back-off асинкронной
            # таски не помешает publisher'у дописать audit. Если row
            # был cancelled mid-run — re-kick не нужен (mark_pending_for_retry
            # вернул None, status в БД остался cancelled, оживлять нечего).
            if should_retry and not cancelled_midrun:
                await _schedule_retry(
                    task_kind, task_id, current_attempt, delay=retry_delay,
                )
            return

        # ── session 2 (happy path): mark_succeeded + audit_outbox atomic ─
        #
        # mark_succeeded CAS на ``status != 'cancelled'``. Если оператор
        # успел отменить task пока impl работал — terminal write пропускаем,
        # audit пишем как failure/cancelled_midrun, success-результат
        # отбрасываем (его поведение оператор отменил сознательно).
        filtered_result = _filter_result_for_audit(result, audit_safe_fields)
        success_cancelled_midrun = False
        async with AsyncSessionLocal() as success_session:
            fresh = await task_repo.get_by_id(success_session, task_id)
            if fresh is not None:
                marked = await task_repo.mark_succeeded(
                    success_session, fresh, result,
                )
                if marked is None:
                    success_cancelled_midrun = True
                    logger.warning(
                        "task cancelled mid-run, terminal write skipped "
                        "task_id=%s phase=success",
                        task_id,
                    )
            # Outbox-row пишем ВСЕГДА, даже если Task-row исчез между
            # сессиями (защита от silent no-op: владелец task'и удалил
            # row → пусть audit об этом останется).
            if success_cancelled_midrun:
                refreshed = await task_repo.get_by_id(success_session, task_id)
                success_cancel_details = {
                    "task_id": task_id,
                    "reason": "cancelled_midrun",
                    "observed_status": TaskStatus.CANCELLED.value,
                }
                _attach_cancel_metadata(success_cancel_details, refreshed)
                audit_payload = {
                    "action": audit_action,
                    "status": "failure",
                    "allowed": False,
                    "target_id": target_id,
                    "target_type": audit_target_type,
                    "request_id": request_id,
                    "actor_id": actor_id,
                    "details": success_cancel_details,
                    "severity": "WARNING",
                }
            else:
                audit_payload = {
                    "action": audit_action,
                    "status": "success",
                    "allowed": True,
                    "target_id": target_id,
                    "target_type": audit_target_type,
                    "request_id": request_id,
                    "actor_id": actor_id,
                    "details": {"task_id": task_id, "result": filtered_result},
                }
            await task_repo.enqueue_audit(
                success_session,
                task_id=task_id,
                payload=audit_payload,
            )
            await success_session.commit()

        await _safe_flush_outbox()
    finally:
        unregister_running_task(task_id)


def _compute_backoff_delay(attempt: int) -> float:
    """Exponential back-off: `base * 2^(attempt-1)` с потолком `_RETRY_MAX_DELAY_SECONDS`.

    Вынесено в отдельную функцию, чтобы caller failure-path'а мог
    использовать те же миллисекунды для записи `scheduled_retry_at` в БД
    и для in-process sleep'а.
    """
    return min(
        _RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1)),
        _RETRY_MAX_DELAY_SECONDS,
    )


async def _schedule_retry(
    task_kind: str,
    task_id: str,
    attempt: int,
    *,
    delay: float | None = None,
) -> None:
    """Шедулинг повторного `kiq` через exponential back-off.

    Запускает background `asyncio.create_task` который:

      1. ждёт `delay` сек (или `_compute_backoff_delay(attempt)` если
         `delay is None`);
      2. находит `broker.task(task_kind)` через `broker.find_task(...)`;
      3. вызывает `.kicker().kiq(task_id)` чтобы повторно положить
         задачу в Redis-очередь.

    Если broker не находит task_kind (миграция вырезала kind, но в DB
    ещё валяются row'ы) — task ушла в `queued`, новый kiq не происходит,
    оператор увидит зависшую `queued`-task в DB (опционально подбирает
    watchdog/cron). Мы не падаем — re-kick best-effort.

    Durable side: caller failure-path'а ДО вызова
    `_schedule_retry` записал `tasks.scheduled_retry_at = now + delay`
    в БД. Если процесс умрёт во время `asyncio.sleep`, перезапущенный
    worker увидит row через `_recover_scheduled_retries` и сам kiq'нет.
    `asyncio.create_task` тут — fast-path latency optimization, не
    durability guarantee.
    """
    # local-import: иначе циклический импорт src.main → src.tasks → src.main.
    from src.main import broker

    if delay is None:
        delay = _compute_backoff_delay(attempt)

    async def _delayed_kick() -> None:
        try:
            await asyncio.sleep(delay)
            task = broker.find_task(task_kind)
            if task is None:
                logger.warning(
                    "retry skipped: broker does not know task_kind=%s "
                    "(task_id=%s left in queued, manual recovery needed)",
                    task_kind,
                    task_id,
                )
                return
            await task.kicker().kiq(task_id)
            logger.info(
                "retry kicked task_id=%s task_kind=%s attempt=%s delay=%ss",
                task_id,
                task_kind,
                attempt,
                delay,
            )
        except asyncio.CancelledError:
            # Worker shutdown во время sleep'а — это OK, task осталась
            # в `queued`, следующий старт worker'а / operator-cron её
            # подхватит.
            logger.info(
                "retry cancelled during shutdown task_id=%s (left in queued)",
                task_id,
            )
            raise
        except Exception as exc:  # noqa: BLE001 — fire-and-forget never throws
            # Реальные клиенты (httpx, Redis) могут зашить URL с
            # basic-auth в repr(exc) — прогоняем через redact, как в
            # audit_outbox_publisher.
            redacted = redact_error_message(f"{type(exc).__name__}: {exc}")
            logger.warning(
                "retry kick failed task_id=%s task_kind=%s: %s",
                task_id,
                task_kind,
                redacted,
            )

    retry_task = asyncio.create_task(_delayed_kick(), name=f"retry_{task_id}")
    _RETRY_TASKS.add(retry_task)
    retry_task.add_done_callback(_RETRY_TASKS.discard)


async def _safe_flush_outbox() -> None:
    """Best-effort publish. Никогда не пробрасывает исключения наружу.

    Latency-оптимизация: на хорошей сети audit-event доедет до
    loging_service за тот же тик, что и commit task'и. На плохой —
    фоновый publisher (`run_publisher_loop`) подхватит позже.
    """
    try:
        await audit_outbox_publisher.flush_outbox()
    except Exception:  # noqa: BLE001
        # Логирование делает сам publisher; здесь — просто проглатываем,
        # чтобы не сорвать happy-path lifecycle'а.
        pass

"""Точка входа server_worker'а.

`broker` — singleton taskiq broker'а; `taskiq worker src.main:broker` его
запускает. Импорт `src.tasks` (в самом низу файла) регистрирует все
task-handler'ы на broker'е.

Жизненный цикл воркера:

* `WORKER_STARTUP` — поднимаем `audit_outbox_publisher.run_publisher_loop`
  как фоновую `asyncio.Task`. Inline `_safe_flush_outbox()` в `_runner`
  покрывает happy-path сразу после commit'а task-lifecycle, но при
  длительной недоступности loging_service строки `audit_outbox` копятся —
  фоновый loop их «доносит». Loop никогда не падает (см. реализацию).
* `WORKER_SHUTDOWN` — отменяем фоновый task и ждём его завершения; затем
  graceful-drain'им активные task-handler'ы (running impl): ждём до
  `WORKER_SHUTDOWN_TIMEOUT_SECONDS`, после mark_pending_for_retry либо
  mark_failed зависших — иначе они оставались бы в `status='running'`
  навсегда.

Scheduler: `scheduler = TaskiqScheduler(broker, [LabelScheduleSource])`
экспортируется на module-level — `taskiq
scheduler src.main:scheduler` поднимает его отдельным процессом. При
`SCHEDULER_ENABLED=true` регистрируется periodic `system.heartbeat`
(каждые 60 секунд, через `schedule_label`). Дефолт — disabled, чтобы
dev/test/CI не делали лишних запросов.
"""

import asyncio
import logging

from taskiq import TaskiqEvents, TaskiqScheduler, TaskiqState
from taskiq.schedule_sources import LabelScheduleSource
from taskiq_redis import ListQueueBroker, RedisAsyncResultBackend

from src.core.config import get_settings

_settings = get_settings()

logging.basicConfig(
    level=_settings.worker_log_level,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

# Глушим говорливых HTTP-логгеров: при WORKER_LOG_LEVEL=DEBUG `httpx._client`
# пишет в stdout полные запросы вместе с заголовками `Authorization: Bearer ...`
# → journald/k8s log-aggregator. Жёстко загоняем в WARNING.
for _noisy in ("httpx", "httpcore", "hpack"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

broker = ListQueueBroker(url=_settings.redis_url).with_result_backend(
    RedisAsyncResultBackend(redis_url=_settings.redis_url, result_ex_time=3600)
)

# Ключ в `TaskiqState` под который кладём handle фоновой publisher-таски.
# Дёргаем через state, а не через global, чтобы при множественных broker'ах
# в одном процессе (например, в тестах) каждый имел собственную ссылку.
_PUBLISHER_TASK_KEY = "audit_outbox_publisher_task"


@broker.on_event(TaskiqEvents.WORKER_STARTUP)
async def _warmup_http_pools(state: TaskiqState) -> None:
    """Прогрев pooled httpx.AsyncClient'ов для loging_service и server_service.

    Сами `get_*_client()` ленивые, но прогрев на старте полезен по двум
    причинам: (1) первая ошибка конфигурации лимитов всплывает сразу, а
    не в середине первого audit-эмита; (2) async event-loop с привязкой
    к pool'у фиксируется здесь, а не в первом call'е.
    """
    from src.services.http_pool import get_audit_client, get_server_service_client

    get_audit_client()
    get_server_service_client()


@broker.on_event(TaskiqEvents.WORKER_STARTUP)
async def _start_audit_outbox_publisher(state: TaskiqState) -> None:
    """Поднимаем фоновый publisher для transactional audit outbox.

    Inline `_safe_flush_outbox()` после каждого commit'а task-lifecycle
    покрывает 99% happy-path. Этот loop — страховка на сценарий
    «loging_service недоступен пару минут / низкий QPS»: иначе строки
    `audit_outbox` копятся без shipping'а.
    """
    # Локальный import: модуль publisher'а тянет `src.db.session` → async engine.
    # Держим импорт здесь, чтобы `import src.main` для регистрации тасок
    # не открывал DB-engine как side-effect.
    from src.services import audit_outbox_publisher

    task = asyncio.create_task(
        audit_outbox_publisher.run_publisher_loop(),
        name="audit_outbox_publisher",
    )
    # TaskiqState — UserDict-like + attr access. Пишем под фиксированным
    # ключом, shutdown-хук поднимает оттуда.
    state[_PUBLISHER_TASK_KEY] = task
    logger.info("audit_outbox publisher loop scheduled on worker startup")


@broker.on_event(TaskiqEvents.WORKER_SHUTDOWN)
async def _stop_audit_outbox_publisher(state: TaskiqState) -> None:
    """Останавливаем publisher-loop при graceful shutdown'е воркера.

    `run_publisher_loop` сам по себе — `while True: await flush; await sleep`,
    выйти он может только через `CancelledError`. Молча проглатываем cancel
    — это штатный путь остановки.
    """
    task: asyncio.Task | None = state.get(_PUBLISHER_TASK_KEY)
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception as exc:  # noqa: BLE001 — shutdown-хук не должен падать
        logger.warning(
            "audit_outbox publisher loop raised on shutdown: %s: %s",
            type(exc).__name__,
            exc,
        )
    finally:
        try:
            del state[_PUBLISHER_TASK_KEY]
        except KeyError:
            pass
    logger.info("audit_outbox publisher loop stopped on worker shutdown")


@broker.on_event(TaskiqEvents.WORKER_SHUTDOWN)
async def _drain_running_tasks(state: TaskiqState) -> None:
    """Graceful drain активных task-handler'ов при SIGTERM.

    Без этого taskiq отменял coroutine'ы где-то в середине impl, и task
    в БД оставалась `status='running'` навсегда — требовала ручного
    recovery. Watchdog для stale-running не реализован — TODO.

    Текущая логика:

      1. Ждём ``WORKER_SHUTDOWN_TIMEOUT_SECONDS`` пока ``RUNNING_TASKS``
         set опустеет естественным образом (impl-функции завершатся сами).
         Polling 0.5s — мелкий накладной расход, зато даёт быстрый
         «нашли всех» при коротких ops.
      2. После timeout: для каждой остающейся task'и — снова CAS-update:
         если `attempt < max_attempts` → mark_pending_for_retry (новый
         worker подберёт), иначе mark_failed("worker_shutdown"). Audit
         outbox-row пишется в той же session (атомарно со сменой статуса).
      3. Inline flush_outbox (best-effort) — audit о shutdown'е уходит
         в loging_service до того, как процесс умрёт.

    Чего НЕ делаем:

      * Re-kick через broker (kicker.kiq) — broker уже останавливается,
        соединение к Redis закрывается. Task в `queued`-статусе будет
        ждать в DB; следующий worker может подхватить через свой
        startup-scan либо operator вручную re-enqueue.
      * Отменять impl-coroutine'ы — taskiq это делает сам после возврата
        из хука; мы только финализируем DB-state.

    Worker shutdown timeout исчерпан → SIGKILL: оставшиеся outbox-rows
    переживут (DB persistent), но lifecycle state — нет. Это deliberate:
    за порогом разумного грейс-периода лучше ловить операторски (alert
    на `status='running' AND started_at < now()-15m`).
    """
    # Локальный импорт, как везде — иначе циклический.
    from datetime import datetime, timezone

    from src.repositories import task as task_repo
    from src.tasks._runner_state import RUNNING_TASKS
    from src.db.session import AsyncSessionLocal
    from src.services import audit_outbox_publisher
    from src.core.constants import TaskStatus
    from src.utils.redaction import redact_error_message

    timeout = _settings.worker_shutdown_timeout_seconds
    poll = 0.5
    elapsed = 0.0

    if not RUNNING_TASKS:
        # Нет активных handler'ов — нечего drain'ить.
        return

    logger.info(
        "graceful shutdown: waiting up to %ss for %s running task(s) to finish",
        timeout,
        len(RUNNING_TASKS),
    )

    while RUNNING_TASKS and elapsed < timeout:
        await asyncio.sleep(poll)
        elapsed += poll

    if not RUNNING_TASKS:
        logger.info("graceful shutdown: all running tasks finished within %ss", elapsed)
        return

    # Timeout. Делаем snapshot чтобы итерировать без race с discard'ами.
    survivors = list(RUNNING_TASKS)
    logger.warning(
        "graceful shutdown timeout (%ss): %s task(s) still running — "
        "marking pending/failed",
        timeout,
        len(survivors),
    )

    for tid in survivors:
        try:
            async with AsyncSessionLocal() as session:
                fresh = await task_repo.get_by_id(session, tid)
                if fresh is None:
                    continue
                # Terminal статусы (SUCCEEDED/FAILED/CANCELLED) — задача
                # уже закрыта другой ветвью, drain'у тут делать нечего.
                # А вот RUNNING и QUEUED обрабатываем одинаково: id попал в
                # RUNNING_TASKS, значит handler ещё в impl-ветке. QUEUED
                # возможен в окне «register_running_task() выполнен, но
                # commit mark_running ещё не прошёл» (см. _runner.run_task)
                # — без этой обработки задача застряла бы queued без
                # scheduled_retry_at и никто бы её не подобрал.
                if fresh.status not in (TaskStatus.RUNNING, TaskStatus.QUEUED):
                    continue

                error_message = "worker_shutdown: terminated by SIGTERM/shutdown event"

                # Retry vs terminal — то же правило, что в `_runner`.
                # scheduled_retry_at = now() — следующий стартующий worker
                # подхватит row через `_recover_scheduled_retries` (он
                # фильтрует по `scheduled_retry_at IS NOT NULL AND <= now()`).
                # Без timestamp'а recovery её не увидит, и задача висит
                # queued до orphan-sweep'а или ручного вмешательства.
                if fresh.attempt < fresh.max_attempts:
                    await task_repo.mark_pending_for_retry(
                        session,
                        fresh,
                        error_message,
                        scheduled_retry_at=datetime.now(timezone.utc),
                    )
                    audit_severity = "WARNING"
                    will_retry = True
                else:
                    await task_repo.mark_failed(session, fresh, error_message)
                    audit_severity = "ERROR"
                    will_retry = False

                target_id = fresh.target_server_id or tid
                await task_repo.enqueue_audit(
                    session,
                    task_id=tid,
                    payload={
                        "action": "task.worker_shutdown",
                        "status": "failure",
                        "allowed": False,
                        "target_id": target_id,
                        "target_type": "task",
                        "request_id": fresh.request_id,
                        "actor_id": fresh.created_by,
                        "details": {
                            "task_id": tid,
                            "reason": "worker_shutdown",
                            "attempt": fresh.attempt,
                            "max_attempts": fresh.max_attempts,
                            "will_retry": will_retry,
                        },
                        "severity": audit_severity,
                    },
                )
                await session.commit()
        except Exception as exc:  # noqa: BLE001 — shutdown-хук не должен падать
            # Реальные клиенты могут зашить URL с basic-auth / Bearer в
            # repr(exc) — прогоняем через redact как везде.
            redacted = redact_error_message(f"{type(exc).__name__}: {exc}")
            logger.warning(
                "graceful shutdown: failed to finalize task_id=%s: %s",
                tid,
                redacted,
            )
        finally:
            # discard в любом случае — task уже либо не RUNNING, либо мы
            # не смогли её обновить; не хотим зацикливать drain.
            RUNNING_TASKS.discard(tid)

    # Best-effort: пытаемся доставить audit о shutdown'е в loging_service
    # до того, как процесс умрёт. Если loging лёг — outbox-publisher
    # следующего worker'а подхватит при старте.
    try:
        await audit_outbox_publisher.flush_outbox()
    except Exception as exc:  # noqa: BLE001
        redacted = redact_error_message(f"{type(exc).__name__}: {exc}")
        logger.warning(
            "graceful shutdown: outbox flush failed: %s",
            redacted,
        )


@broker.on_event(TaskiqEvents.WORKER_SHUTDOWN)
async def _close_http_pools(state: TaskiqState) -> None:
    """Закрыть pooled HTTP-клиенты после graceful drain.

    Ставится последним среди WORKER_SHUTDOWN-хендлеров, чтобы:

      * `_stop_audit_outbox_publisher` уже отменил background loop;
      * `_drain_running_tasks` доделал best-effort flush_outbox через
        `audit_client.emit` (он сам же дергает наш пул, поэтому пулы
        должны жить до этого момента).

    После выхода из этого хука taskiq закроет broker, и FD-учёт
    httpx-пула должен быть чистым.
    """
    from src.services import http_pool

    await http_pool.aclose_all()


# Регистрируем все task-handler'ы (должно идти после определения `broker`).
from src import tasks  # noqa: E402, F401


# ── Durable retry startup-recovery ───────────────────────────────────────────
#
# `_schedule_retry` пишет `tasks.scheduled_retry_at = now + delay` ДО запуска
# fire-and-forget `asyncio.create_task` с back-off sleep'ом. Если worker умер
# во время sleep'а — row остался `status='queued' AND scheduled_retry_at <=
# now()` без re-kick'а. Этот хук на WORKER_STARTUP подхватывает их и kiq'ает
# заново.
#
# Запускается на каждой replica при старте; защищён `with_for_update
# (skip_locked)` — если две replica'и стартуют одновременно, одна и та же
# row не подхватится дважды.
async def _recover_due_scheduled_retries_once() -> None:
    """Один проход recovery: SELECT due-row'ы и kiq каждой.

    Общая логика для startup-хука (`_recover_scheduled_retries`) и
    periodic-task'и (`tasks_recover_scheduled_retries`). Без этого фикс
    P0-2 был бы дубликатом кода: startup поднимает потерянные при крэше
    retry-планы, periodic — потерянные в долгоживущем процессе
    (asyncio.create_task GC'нулся, `_RETRY_TASKS` set теряет ссылку,
    `_delayed_kick` ловит CancelledError при чужой отмене и re-raise'ит
    без re-kick'а).
    """
    from src.db.session import AsyncSessionLocal
    from src.repositories import task as task_repo
    from src.utils.redaction import redact_error_message

    try:
        async with AsyncSessionLocal() as session:
            due = await task_repo.list_due_scheduled_retries(session)
            if not due:
                return

            logger.info(
                "scheduled_retries recovery: found %s task(s) with due "
                "scheduled_retry_at, re-kicking",
                len(due),
            )
            recovered = 0
            for t in due:
                try:
                    target_task = broker.find_task(t.task_kind)
                    if target_task is None:
                        logger.warning(
                            "scheduled_retries recovery: broker does not "
                            "know task_kind=%s (task_id=%s left in queued)",
                            t.task_kind,
                            t.id,
                        )
                        continue
                    await target_task.kicker().kiq(t.id)
                    recovered += 1
                except Exception as exc:  # noqa: BLE001
                    redacted = redact_error_message(
                        f"{type(exc).__name__}: {exc}"
                    )
                    logger.warning(
                        "scheduled_retries recovery: re-kick failed "
                        "task_id=%s task_kind=%s: %s",
                        t.id,
                        t.task_kind,
                        redacted,
                    )
            # commit ПОСЛЕ kiq-loop'а: row-level lock'и держатся до commit'а;
            # после него вторая replica увидит row'ы свободными (но статусы
            # уже изменятся как только consumer подхватит kiq, и `mark_running`
            # CAS отобьёт повторную попытку).
            await session.commit()
    except Exception as exc:  # noqa: BLE001 — recovery не должна крэшить хост
        redacted = redact_error_message(f"{type(exc).__name__}: {exc}")
        logger.warning(
            "scheduled_retries recovery: SELECT failed: %s",
            redacted,
        )
        return

    logger.info(
        "scheduled_retries recovery: re-kicked %s/%s task(s)",
        recovered,
        len(due),
    )


@broker.on_event(TaskiqEvents.WORKER_STARTUP)
async def _recover_scheduled_retries(state: TaskiqState) -> None:
    """Поднять «потерянные» при крэше retry-планы.

    Сценарий: worker A на attempt=1 упал во время `asyncio.sleep(10s)`
    back-off'а. Task в DB: status='queued', scheduled_retry_at = T+10s.
    Worker B стартует через 30s → видит row → kiq → retry поехал.

    Concurrent-startup защита: SELECT идёт с `FOR UPDATE SKIP LOCKED`,
    session держится открытой до конца kiq-loop'а и commit'ится после.
    Если две replica'и стартуют одновременно, второй SELECT просто
    пропустит row'ы, заблокированные первой. Без SKIP LOCKED обе делают
    kiq на одни и те же row'ы — CAS на `mark_running` отбивает дубль
    consumer'а, но Redis-очередь успевает раздуться лишними сообщениями.
    """
    await _recover_due_scheduled_retries_once()


# ── Scheduler skeleton ──────────────────────────────────────────────────────
#
# `scheduler` экспортирован как module-level атрибут — для запуска:
#
#     taskiq scheduler src.main:scheduler
#
# отдельным процессом. Scheduler читает `schedule` labels у broker-task'ов
# (через `LabelScheduleSource`) и шлёт scheduled `kiq` вызовы в ту же
# Redis-очередь, что слушает worker.
#
# При `SCHEDULER_ENABLED=true` регистрируются periodic task'и:
#
#   * `system.heartbeat` (60s) — INFO «alive» в stdout, sanity-check
#     scheduler-цепочки;
#   * `worker.heartbeat` (60s) — UPSERT `(worker_id, now)` в
#     `worker_heartbeats`; используется sweep'ом для cross-replica
#     zombie detection;
#   * `tasks.sweep_orphaned` (60s) — найти running task'и без свежего
#     heartbeat'а от их `worker_id` и mark_failed("worker_orphaned");
#   * `worker.cleanup_stale_heartbeats` (1h) — DELETE row'ов в
#     `worker_heartbeats` старше cleanup-порога (default 7d); bounded
#     growth таблицы при частых pod-рестартах;
#   * `tasks.cleanup_completed_old` (daily 03:00 MSK) — DELETE terminal
#     task'ов старше `TASKS_RETENTION_DAYS` (default 30d); bounded growth
#     таблицы `tasks`;
#   * `audit_outbox.cleanup_published_old` (daily 03:30 MSK) — DELETE
#     published outbox-row'ов старше `AUDIT_OUTBOX_RETENTION_DAYS`
#     (default 90d); bounded growth `audit_outbox`;
#   * `secrets.reencrypt_lazy` (*/5 минут) — постепенная ре-шифрация
#     секретов под активный мастер-ключ через server_service. Скип-тик
#     если есть активные user-handler'ы, чтобы не конкурировать за DB-
#     write'ы. Полная логика — в самой task'е ниже.
#
# Когда `SCHEDULER_ENABLED=false` — task'и всё равно регистрируются на
# broker'е (нужны на worker-стороне для `find_task`), но без `schedule`
# labels — scheduler их игнорирует. Это «infrastructure ready» — задачи
# можно kiq'нуть вручную из теста или ops-консоли.
scheduler = TaskiqScheduler(broker=broker, sources=[LabelScheduleSource(broker)])


@broker.task(
    "system.heartbeat",
    schedule=[{"cron": "*/1 * * * *"}] if _settings.scheduler_enabled else [],
)
async def system_heartbeat() -> None:
    """Демо periodic task — тикает каждую минуту, когда scheduler enabled.

    Пишет logger.info «alive» — заглушка для будущей логики: запись
    `last_heartbeat_at` в DB для operator-monitoring, push Prometheus-
    метрики или health-check для service mesh.

    Когда `SCHEDULER_ENABLED=false` — task всё равно регистрируется на
    broker'е (нужна на worker-стороне, чтобы `find_task` её нашёл), но
    без `schedule` labels — scheduler её игнорирует.
    """
    logger.info("system.heartbeat tick — worker alive")


@broker.task(
    "worker.heartbeat",
    schedule=[{"cron": "*/1 * * * *"}] if _settings.scheduler_enabled else [],
)
async def worker_heartbeat() -> None:
    """Heartbeat периодик: UPSERT `(worker_id, now())` в `worker_heartbeats`.

    Каждая worker-replica шлёт раз в минуту. Sweep смотрит на
    `worker_heartbeats` чтобы понять, какие worker_id «живы». Если
    pod умер OOM-kill / node-failure'ом, heartbeat перестаёт обновляться
    → sweep mark_failed-ит running task'и этого worker_id.

    Когда `SCHEDULER_ENABLED=false` — task регистрируется, но без cron
    label, scheduler её не дёргает. На worker'е тесты могут позвать её
    вручную через `worker_heartbeat.original_func()` или kiq.

    Ошибки логируются (`redact_error_message`), не пробрасываются —
    sweep устойчив к одному пропущенному tick'у (heartbeat-stale-window
    сильно больше cron-периода).
    """
    from src.db.session import AsyncSessionLocal
    from src.repositories import worker_heartbeat as heartbeat_repo
    from src.tasks._runner_state import get_worker_id
    from src.utils.redaction import redact_error_message

    wid = get_worker_id()
    try:
        async with AsyncSessionLocal() as session:
            await heartbeat_repo.upsert_heartbeat(session, worker_id=wid)
            await session.commit()
    except Exception as exc:  # noqa: BLE001 — periodic не должен крэшить scheduler
        redacted = redact_error_message(f"{type(exc).__name__}: {exc}")
        logger.warning(
            "worker.heartbeat upsert failed worker_id=%s: %s",
            wid,
            redacted,
        )


@broker.task(
    "tasks.recover_scheduled_retries",
    schedule=[{"cron": "*/1 * * * *"}] if _settings.scheduler_enabled else [],
)
async def tasks_recover_scheduled_retries() -> None:
    """Periodic recovery «зависших» retry-row'ов.

    Закрывает дыру startup-only `_recover_scheduled_retries`: если worker
    долго живёт, а фоновая `_RETRY_TASKS`-task'а молча отменилась (GC,
    ошибка event loop, или чужой `task.cancel()` в taskiq), row остаётся
    `status='queued' AND scheduled_retry_at <= now()` навсегда — sweep'у
    она невидима (он смотрит только `status='running'`), startup-recovery
    отработал давно. Минутный cron подбирает такие row'ы и re-kick'ает.

    `with_for_update(skip_locked=True)` в `list_due_scheduled_retries`
    защищает от concurrent-replica дубликата kiq'а; CAS `mark_running`
    дополнительно отбивает повторный consume.
    """
    await _recover_due_scheduled_retries_once()


@broker.task(
    "tasks.sweep_orphaned",
    schedule=[{"cron": "*/1 * * * *"}] if _settings.scheduler_enabled else [],
)
async def tasks_sweep_orphaned() -> None:
    """Periodic sweep cross-replica zombie running task'ов.

    Алгоритм: найти `tasks WHERE status='running' AND started_at <
    now() - worker_orphan_threshold_seconds AND worker_id NOT IN
    (active workers)` → mark_failed("worker_orphaned") + audit-row
    "task.worker_orphaned" (severity=ERROR).

    Active worker = heartbeat'ил `last_heartbeat_at >= now() -
    worker_heartbeat_stale_seconds` назад. Task с `worker_id IS NULL`
    автоматически orphan (legacy row или CAS написал NULL — тоже
    подбираем).

    Если `SCHEDULER_ENABLED=false` — task в broker'е, но без cron,
    sweep'а не происходит. Это OK для dev/CI: orphans там не вредят.

    Ошибки в sweep'е (transient DB / network) не должны крэшить
    scheduler-loop — ловим и логируем.
    """
    from src.core.constants import TaskStatus
    from src.db.session import AsyncSessionLocal
    from src.repositories import task as task_repo
    from src.utils.redaction import redact_error_message

    try:
        async with AsyncSessionLocal() as session:
            orphans = await task_repo.list_orphaned_running(
                session,
                orphan_threshold_seconds=_settings.worker_orphan_threshold_seconds,
                heartbeat_stale_seconds=_settings.worker_heartbeat_stale_seconds,
            )
    except Exception as exc:  # noqa: BLE001
        redacted = redact_error_message(f"{type(exc).__name__}: {exc}")
        logger.warning(
            "tasks.sweep_orphaned: SELECT failed: %s",
            redacted,
        )
        return

    if not orphans:
        return

    logger.warning(
        "tasks.sweep_orphaned: found %s orphan running task(s) — "
        "marking failed",
        len(orphans),
    )

    from src.services import audit_outbox_publisher

    for orphan in orphans:
        try:
            async with AsyncSessionLocal() as session:
                fresh = await task_repo.get_by_id(session, orphan.id)
                if fresh is None or fresh.status != TaskStatus.RUNNING:
                    # Race: кто-то уже закрыл task'у между SELECT и UPDATE.
                    continue
                error_message = (
                    f"worker_orphaned: worker_id={orphan.worker_id} "
                    f"heartbeat stale; started_at={orphan.started_at}"
                )
                await task_repo.mark_failed(session, fresh, error_message)
                target_id = fresh.target_server_id or fresh.id
                await task_repo.enqueue_audit(
                    session,
                    task_id=fresh.id,
                    payload={
                        "action": "task.worker_orphaned",
                        "status": "failure",
                        "allowed": False,
                        "target_id": target_id,
                        "target_type": "task",
                        "request_id": fresh.request_id,
                        "actor_id": fresh.created_by,
                        "details": {
                            "task_id": fresh.id,
                            "reason": "worker_orphaned",
                            "worker_id": fresh.worker_id,
                            "attempt": fresh.attempt,
                            "max_attempts": fresh.max_attempts,
                        },
                        "severity": "ERROR",
                    },
                )
                await session.commit()
        except Exception as exc:  # noqa: BLE001
            redacted = redact_error_message(f"{type(exc).__name__}: {exc}")
            logger.warning(
                "tasks.sweep_orphaned: failed to finalize task_id=%s: %s",
                orphan.id,
                redacted,
            )

    # Best-effort just-in-time publish — audit о orphan'ах должен
    # доехать в loging_service быстро. Если flush упадёт — background
    # publisher loop всё равно довезёт (outbox-row уже commit'нут).
    try:
        await audit_outbox_publisher.flush_outbox()
    except Exception as exc:  # noqa: BLE001
        redacted = redact_error_message(f"{type(exc).__name__}: {exc}")
        logger.warning(
            "tasks.sweep_orphaned: outbox flush failed: %s",
            redacted,
        )


@broker.task(
    "worker.cleanup_stale_heartbeats",
    schedule=[{"cron": "0 * * * *"}] if _settings.scheduler_enabled else [],
)
async def worker_cleanup_stale_heartbeats() -> None:
    """Hourly cleanup `worker_heartbeats` row'ов старше cleanup-порога.

    Каждый pod-рестарт в k8s = новый `worker_id` (hostname меняется при
    rolling-update). Без cleanup'а таблица растёт линейно по числу
    рестартов — за год может накопиться тысячи row'ов. Sweep сам по себе
    с этим живёт (stale row'ы просто не считаются активными), но row count
    лучше держать bounded.

    Cleanup-порог сильно больше stale-порога (7d vs 5min): между «replica
    умерла» и «row дропнут» оператор успевает увидеть её в orphan-sweep'е
    и forensics-логах, и только потом мы её забываем.

    Audit emit пропускаем намеренно — административная housekeeping-задача
    без security-смысла. Ошибки логируются (`redact_error_message`),
    не пробрасываются — sweep устойчив к одному пропущенному tick'у.
    """
    from datetime import datetime, timedelta, timezone

    from src.db.session import AsyncSessionLocal
    from src.repositories import worker_heartbeat as heartbeat_repo
    from src.utils.redaction import redact_error_message

    cutoff = datetime.now(timezone.utc) - timedelta(
        seconds=_settings.worker_heartbeat_cleanup_threshold_seconds
    )
    try:
        async with AsyncSessionLocal() as session:
            deleted = await heartbeat_repo.delete_stale_heartbeats(
                session, cutoff=cutoff
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001 — periodic не должен крэшить scheduler
        redacted = redact_error_message(f"{type(exc).__name__}: {exc}")
        logger.warning(
            "worker.cleanup_stale_heartbeats failed: %s",
            redacted,
        )
        return

    if deleted:
        logger.info(
            "worker.cleanup_stale_heartbeats: dropped %s stale row(s) "
            "older than %s",
            deleted,
            cutoff.isoformat(),
        )


@broker.task(
    "tasks.cleanup_completed_old",
    # Раз в сутки в 03:00 MSK (00:00 UTC) — низкий traffic ночью, не
    # конфликтует с power-cycle / inventory нагрузкой днём. Расписание
    # планируем по московскому времени (Europe/Moscow, UTC+3); cron в
    # taskiq читается в UTC, поэтому 00:00 UTC.
    schedule=[{"cron": "0 0 * * *"}] if _settings.scheduler_enabled else [],
)
async def tasks_cleanup_completed_old() -> None:
    """Daily retention: дропаем SUCCEEDED/FAILED task'и старше N дней.

    Без cleanup'а таблица `tasks` растёт линейно по числу dispatch'ей —
    индекс `(status, enqueued_at)` распухает, UI-история «task'и по
    серверу» тормозит. QUEUED/RUNNING не трогаем: in-flight task'и
    разгребает orphan-sweep, у которого есть audit-trail и retry-логика.

    Audit emit пропускаем — административная housekeeping, без security-
    смысла (как `worker.cleanup_stale_heartbeats`). Ошибки логируются с
    redact'ом и НЕ пробрасываются, чтобы один пропущенный tick не валил
    scheduler-loop.
    """
    from datetime import datetime, timedelta, timezone

    from src.db.session import AsyncSessionLocal
    from src.repositories import task as task_repo
    from src.utils.redaction import redact_error_message

    cutoff = datetime.now(timezone.utc) - timedelta(
        days=_settings.tasks_retention_days
    )
    try:
        async with AsyncSessionLocal() as session:
            deleted = await task_repo.delete_completed_older_than(
                session, cutoff=cutoff
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        redacted = redact_error_message(f"{type(exc).__name__}: {exc}")
        logger.warning("tasks.cleanup_completed_old failed: %s", redacted)
        return

    if deleted:
        logger.info(
            "tasks.cleanup_completed_old: dropped %s terminal task(s) "
            "older than %s",
            deleted,
            cutoff.isoformat(),
        )


@broker.task(
    "audit_outbox.cleanup_published_old",
    # 03:30 UTC — со сдвигом от `tasks.cleanup_completed_old` (00:00 UTC),
    # чтобы не пересекаться по DB-write нагрузке. Обе housekeeping-task'и
    # быстрые, но overlap всё равно нежелателен на крупном tasks'е.
    schedule=[{"cron": "30 0 * * *"}] if _settings.scheduler_enabled else [],
)
async def audit_outbox_cleanup_published_old() -> None:
    """Daily retention: дропаем published outbox-row'ы старше N дней.

    Покрывает обе категории published-row'ов:

      * Happy-path delivered (`published_at` стоит, `last_error IS NULL`)
        — событие доехало в loging_service, audit-trail живёт там.
      * DLQ-poisoned (`published_at` поставлен `_send_to_dlq`,
        `last_error` хранит причину) — событие потеряно, причину
        оператор поймал по ERROR-логу `audit_outbox: row sent to DLQ`.

    Unpublished (`published_at IS NULL`) не трогаем — это in-flight
    события, publisher до сих пор пытается их доставить. Если хочется
    задушить их принудительно — поднимай `MAX_PUBLISH_ATTEMPTS`-cap и
    `_send_to_dlq` сам их пометит.

    Audit emit пропускаем (как и у соседних cleanup-task'ов). Ошибки
    логируются и НЕ пробрасываются — periodic не должен крэшить
    scheduler-loop.
    """
    from datetime import datetime, timedelta, timezone

    from src.db.session import AsyncSessionLocal
    from src.repositories import audit_outbox as audit_outbox_repo
    from src.utils.redaction import redact_error_message

    cutoff = datetime.now(timezone.utc) - timedelta(
        days=_settings.audit_outbox_retention_days
    )
    try:
        async with AsyncSessionLocal() as session:
            deleted = await audit_outbox_repo.delete_published_older_than(
                session, cutoff=cutoff
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        redacted = redact_error_message(f"{type(exc).__name__}: {exc}")
        logger.warning(
            "audit_outbox.cleanup_published_old failed: %s", redacted
        )
        return

    if deleted:
        logger.info(
            "audit_outbox.cleanup_published_old: dropped %s published "
            "row(s) older than %s",
            deleted,
            cutoff.isoformat(),
        )


@broker.task("internal.outbox_re_attempt")
async def internal_outbox_re_attempt(row_id: int) -> bool:
    """Operator ручка: вернуть outbox-row из DLQ обратно в очередь.

    DLQ-row'ы (`_send_to_dlq` поставил `published_at = now()`) больше не
    попадают в publisher'овский SELECT. Чтобы попробовать заново —
    например, после фикса payload-схемы или починки loging_service на
    permanent-4xx — оператор kick'ает эту таску с конкретным
    `audit_outbox.id`. Она сбрасывает `published_at`, `attempts`,
    `next_retry_at`, `last_error` → publisher подхватит row в ближайший
    тик loop'а.

    Возвращает True, если row найден и сброшен; False — если row нет
    или она уже unpublished (idempotent). Ошибки логируются и НЕ
    пробрасываются — single-shot task, retry'я нет.

    Запускать через `taskiq broker.send` / kiq-CLI или из server_service
    `internal`-endpoint'а. UI-эндпоинта пока нет — это операторская
    ручка под рестарт после инцидента.
    """
    from src.services import audit_outbox_publisher
    from src.utils.redaction import redact_error_message

    try:
        return await audit_outbox_publisher.re_attempt_row(row_id)
    except Exception as exc:  # noqa: BLE001
        redacted = redact_error_message(f"{type(exc).__name__}: {exc}")
        logger.warning(
            "internal.outbox_re_attempt: row=%s failed: %s",
            row_id, redacted,
        )
        return False


@broker.task(
    "secrets.reencrypt_lazy",
    schedule=[{"cron": "*/5 * * * *"}] if _settings.scheduler_enabled else [],
)
async def secrets_reencrypt_lazy() -> None:
    """Фоновая постепенная ре-шифрация секретов под активный мастер-ключ.

    Политика: после смены `SERVER_ENCRYPTION_KEY` обе версии живут
    параллельно (старая — для decrypt, новая — для encrypt). Этот тик
    постепенно подтягивает старые записи к активной версии.

    Алгоритм одного тика:

      1. Если `SECRETS_REENCRYPT_ENABLED=false` → выйти молча (dev/test).
      2. Если в `RUNNING_TASKS` есть активные user-handler'ы → skip, чтобы
         не конкурировать с power/SSH/inventory за DB-write'ы и CPU.
         Полагаемся на short-running ходовку: следующий cron-тик (`*/5`)
         снова попробует.
      3. `GET /internal/secrets/migration_status`. `remaining==0` →
         миграция доехала, audit с processed=0 и выходим. Оператор по
         этому событию (и метрике в loging_service) решает, можно ли
         дропнуть `SERVER_ENCRYPTION_KEY__v<old>`.
      4. `POST /internal/secrets/reencrypt_batch?limit=N` —
         server_service сам commit'ит транзакцию. Worker логирует
         результат + audit `secrets.reencrypt_tick`.

    Cron-расписание `*/5 * * * *` — в проде даёт ~720 батчей в сутки, по
    100 записей = 72k записей/день в worst case. На практике RPS воркера
    ниже из-за shed'а при busy state. Реальный темп управляется через
    `SECRETS_REENCRYPT_BATCH_SIZE` env.

    Ошибки внутри тика логируются (`redact_error_message`) и НЕ пробрасываются
    — periodic-task не должен крэшить scheduler-loop. Все audit-события идут
    через transactional outbox (`enqueue_audit` + `commit`), а не через прямой
    `audit_client.emit` — чтобы при недоступности loging_service запись не
    терялась, а ждала retry'я publisher'ом.
    """
    from src.tasks._runner_state import RUNNING_TASKS
    from src.services import server_service_client
    from src.services import audit_outbox_publisher
    from src.repositories import task as task_repo
    from src.db.session import AsyncSessionLocal
    from src.core.exceptions import CredentialFetchError
    from src.utils.redaction import redact_error_message

    async def _enqueue_outbox_audit(payload: dict) -> None:
        """Положить audit-row в outbox, зафиксировать и попытаться доставить.

        После commit'а зовём `flush_outbox()` — это same-pattern, что и у
        `_runner.run_task::_safe_flush_outbox`: happy-path сразу доставляет
        событие, при сбое publisher background-loop'а добьёт row позже.
        Best-effort: ошибки на любом шаге логируются, тик не падает.
        """
        try:
            async with AsyncSessionLocal() as session:
                await task_repo.enqueue_audit(session, task_id=None, payload=payload)
                await session.commit()
        except Exception as exc:  # noqa: BLE001 — best-effort audit
            logger.debug(
                "secrets.reencrypt_lazy: outbox enqueue failed: %s",
                redact_error_message(f"{type(exc).__name__}: {exc}"),
            )
            return
        try:
            await audit_outbox_publisher.flush_outbox()
        except Exception as exc:  # noqa: BLE001 — happy-path optimization
            logger.debug(
                "secrets.reencrypt_lazy: outbox flush failed: %s",
                redact_error_message(f"{type(exc).__name__}: {exc}"),
            )

    settings = get_settings()

    if not settings.secrets_reencrypt_enabled:
        logger.debug("secrets.reencrypt_lazy: disabled via SECRETS_REENCRYPT_ENABLED")
        return

    if RUNNING_TASKS:
        logger.info(
            "secrets.reencrypt_lazy: skip tick — %s active task(s) in flight",
            len(RUNNING_TASKS),
        )
        await _enqueue_outbox_audit({
            "action": "secrets.reencrypt_tick",
            "status": "success",
            "allowed": True,
            "target_type": "secret",
            "details": {
                "skipped": True,
                "reason": "active_tasks_present",
                "active_count": len(RUNNING_TASKS),
            },
        })
        return

    try:
        status = await server_service_client.fetch_secrets_migration_status()
    except CredentialFetchError as exc:
        logger.warning(
            "secrets.reencrypt_lazy: status fetch failed: %s",
            redact_error_message(f"{exc.error_code}: {exc.message}"),
        )
        return
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "secrets.reencrypt_lazy: status fetch unexpected error: %s",
            redact_error_message(f"{type(exc).__name__}: {exc}"),
        )
        return

    remaining = int(status.get("remaining", 0))
    active_version = status.get("active_version")
    if remaining <= 0:
        logger.debug(
            "secrets.reencrypt_lazy: nothing to do (remaining=0, active=v%s)",
            active_version,
        )
        await _enqueue_outbox_audit({
            "action": "secrets.reencrypt_tick",
            "status": "success",
            "allowed": True,
            "target_type": "secret",
            "details": {
                "processed": 0,
                "remaining": 0,
                "active_version": active_version,
            },
        })
        return

    try:
        result = await server_service_client.trigger_secrets_reencrypt_batch(
            settings.secrets_reencrypt_batch_size
        )
    except CredentialFetchError as exc:
        logger.warning(
            "secrets.reencrypt_lazy: batch failed: %s",
            redact_error_message(f"{exc.error_code}: {exc.message}"),
        )
        return
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "secrets.reencrypt_lazy: batch unexpected error: %s",
            redact_error_message(f"{type(exc).__name__}: {exc}"),
        )
        return

    processed = int(result.get("processed", 0))
    errors = int(result.get("errors", 0))
    logger.info(
        "secrets.reencrypt_lazy: processed=%s errors=%s remaining_before=%s",
        processed,
        errors,
        remaining,
    )
    await _enqueue_outbox_audit({
        "action": "secrets.reencrypt_tick",
        "status": "success" if errors == 0 else "warning",
        "allowed": True,
        "target_type": "secret",
        "details": {
            "processed": processed,
            "errors": errors,
            "remaining_before": remaining,
            "active_version": active_version,
            "batch_size": settings.secrets_reencrypt_batch_size,
        },
    })

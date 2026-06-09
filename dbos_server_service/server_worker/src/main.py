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
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from taskiq import TaskiqEvents, TaskiqScheduler, TaskiqState
from taskiq.schedule_sources import LabelScheduleSource
from taskiq_redis import ListQueueBroker, RedisAsyncResultBackend

from src.core.config import get_settings
from src.core.constants import TaskStatus
from src.db import dispatch_outbox_session
from src.db.session import AsyncSessionLocal
from src.repositories import task as task_repo
from src.services import audit_outbox_publisher, http_pool, redis_pool
from src.utils.redaction import redact_error_message

# `from src.tasks._runner_state import RUNNING_TASKS` остаётся локальным:
# импорт сабмодуля прогоняет `src.tasks/__init__.py` → загружает все
# task-модули (`power.py`, `users.py`, ...), каждый из них на module-level
# делает `from src.main import broker` — circular на этой стадии загрузки
# `src.main` (broker ещё не определён). Аналогично для `from src.tasks
# import dispatch_outbox`.

try:
    _settings = get_settings()
except ValueError as _exc:
    # Settings-валидатор бьёт ValueError'ом, если обязательный env не
    # выставлен под текущий APP_ENV (например, WORKER_BOT_TOKEN пуст
    # в dev/staging/production). taskiq при импорте broker'а покажет
    # длинный pydantic traceback; перехватываем и пишем человекочитаемую
    # строку в stderr/journald, потом выходим с кодом 1.
    logging.basicConfig(
        level="ERROR",
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger(__name__).critical(
        "server_worker startup aborted: %s", _exc,
    )
    sys.exit(1)

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

# Флаг «HTTP/Redis-пулы закрыты» — выставляется `_close_http_pools` после
# `aclose_all()`. Защищает от use-after-aclose в shutdown-window'е, если
# в будущем порядок WORKER_SHUTDOWN-хуков случайно перетасуют (taskiq
# вызывает их в порядке регистрации — `flush_outbox` после `_close_http_pools`
# попытается ходить по уже закрытому httpx-клиенту). Любая попытка
# `_safe_flush_outbox` под выставленный флаг тихо возвращает — drain
# уже сделал своё, добивать нечего.
_pools_closed = False


def _on_publisher_exit(task: asyncio.Task) -> None:
    """Safety-net callback для фоновых publisher loop'ов.

    Loop'ы (`audit_outbox_publisher.run_publisher_loop`,
    `dispatch_outbox.run_publisher_loop`) внутри ловят `Exception`, но
    `BaseException`-подкласс (Cancel — штатно; что-то другое — нет)
    проскочит и task молча завершится. Без callback'а shutdown-хук
    дождётся уже-завершённого task'а мгновенно и worker подумает, что
    drain прошёл штатно. Здесь — ERROR-лог при unexpected exit, чтобы
    оператор увидел сигнал в k8s log-aggregator'е.
    """
    if task.cancelled():
        return
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        logger.critical(
            "background publisher loop exited unexpectedly: name=%s err=%s",
            task.get_name(),
            redact_error_message(f"{type(exc).__name__}: {exc}"),
        )


@broker.on_event(TaskiqEvents.WORKER_STARTUP)
async def _warn_on_missing_audit_api_key(state: TaskiqState) -> None:
    """Сигнал оператору при старте, если `LOGGING_SERVICE_API_KEY` пустой.

    В non-prod-окружениях (`local`/`dev`/`test`/`staging`) settings-валидатор
    разрешает пустой ключ — но это означает, что `audit_client.emit` тихо
    дропает каждое событие и инкрементит `_audit_dropped_no_api_key`.
    Без startup-WARNING'а оператор узнаёт о мисконфиге только если
    специально читает grep по логам. Пишем WARNING явно с указанием
    счётчика, чтобы k8s log-aggregator подсветил.
    """
    if not _settings.logging_service_api_key:
        logger.warning(
            "LOGGING_SERVICE_API_KEY is empty (app_env=%s); audit emits "
            "will be silently dropped — see audit_client._audit_dropped_no_api_key "
            "counter and `get_dropped_no_api_key_total()`",
            _settings.app_env,
        )


@broker.on_event(TaskiqEvents.WORKER_STARTUP)
async def _warmup_http_pools(state: TaskiqState) -> None:
    """Прогрев pooled httpx.AsyncClient'ов для loging_service и server_service.

    Сами `get_*_client()` ленивые, но прогрев на старте полезен по двум
    причинам: (1) первая ошибка конфигурации лимитов всплывает сразу, а
    не в середине первого audit-эмита; (2) async event-loop с привязкой
    к pool'у фиксируется здесь, а не в первом call'е.
    """
    from src.services.http_pool import (
        get_audit_client,
        get_bmc_probe_client,
        get_bmc_redfish_transport,
        get_server_service_client,
    )
    from src.core.config import get_settings as _gs

    get_audit_client()
    get_server_service_client()
    # BMC pool: probe (3 комбинации scheme/verify) + Redfish transport
    # под текущий verify-уровень. Второй verify-уровень поднимется лениво,
    # если cascade провалится на верхнем шаге.
    get_bmc_probe_client(scheme="https", verify=True)
    get_bmc_probe_client(scheme="https", verify=False)
    get_bmc_probe_client(scheme="http", verify=True)
    get_bmc_redfish_transport(verify=_gs().redfish_verify_tls)


_DISPATCH_PUBLISHER_TASK_KEY = "dispatch_outbox_publisher_task"


@broker.on_event(TaskiqEvents.WORKER_STARTUP)
async def _start_dispatch_outbox_publisher(state: TaskiqState) -> None:
    """Поднять фоновый publisher для dispatch_outbox.

    Закрывает окно потери между commit'ом server_service-транзакции и
    публикацией в Redis (см. docstring `tasks/dispatch_outbox.py`). Loop
    polls раз в `DISPATCH_OUTBOX_POLL_INTERVAL_SECONDS`. Если в текущем
    окружении `SERVER_SERVICE_DATABASE_URL` не выставлен —
    `dispatch_outbox.poll_once` сам no-op'ит, поэтому стартуем безусловно.
    """
    from src.tasks import dispatch_outbox

    task = asyncio.create_task(
        dispatch_outbox.run_publisher_loop(),
        name="dispatch_outbox_publisher",
    )
    task.add_done_callback(_on_publisher_exit)
    state[_DISPATCH_PUBLISHER_TASK_KEY] = task
    logger.info("dispatch_outbox publisher loop scheduled on worker startup")


@broker.on_event(TaskiqEvents.WORKER_SHUTDOWN)
async def _stop_dispatch_outbox_publisher(state: TaskiqState) -> None:
    """Остановить dispatch_outbox publisher при shutdown'е воркера."""
    task: asyncio.Task | None = state.get(_DISPATCH_PUBLISHER_TASK_KEY)
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception as exc:  # noqa: BLE001 — shutdown-хук не должен падать
        logger.warning(
            "dispatch_outbox publisher loop raised on shutdown: %s",
            redact_error_message(f"{type(exc).__name__}: {exc}"),
        )
    finally:
        try:
            del state[_DISPATCH_PUBLISHER_TASK_KEY]
        except KeyError:
            pass
    # Закрыть engine на server_service-БД (если поднимался).
    await dispatch_outbox_session.dispose()
    logger.info("dispatch_outbox publisher loop stopped on worker shutdown")


@broker.on_event(TaskiqEvents.WORKER_STARTUP)
async def _start_audit_outbox_publisher(state: TaskiqState) -> None:
    """Поднимаем фоновый publisher для transactional audit outbox.

    Inline `_safe_flush_outbox()` после каждого commit'а task-lifecycle
    покрывает 99% happy-path. Этот loop — страховка на сценарий
    «loging_service недоступен пару минут / низкий QPS»: иначе строки
    `audit_outbox` копятся без shipping'а.
    """
    task = asyncio.create_task(
        audit_outbox_publisher.run_publisher_loop(),
        name="audit_outbox_publisher",
    )
    task.add_done_callback(_on_publisher_exit)
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
            "audit_outbox publisher loop raised on shutdown: %s",
            redact_error_message(f"{type(exc).__name__}: {exc}"),
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
    recovery. Stale-running теперь подбирает periodic `tasks.sweep_orphaned`
    (60s) через `list_orphaned_running` (heartbeat-aware), drain — это
    fast-path для штатного SIGTERM, sweep — fallback для kill -9 / OOM.

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
    # `RUNNING_TASKS` остаётся локальным импортом: подмодуль `src.tasks`
    # на module-level гонит регистрацию тасок через `from src.main import
    # broker` — циклично при загрузке `src.main`.
    from src.tasks._runner_state import RUNNING_TASKS

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

                # Снимаем pre-drain статус ДО `mark_*` — иначе ниже в
                # `details_payload` мы бы записали уже переписанный
                # `fresh.status` (QUEUED после mark_pending_for_retry или
                # FAILED после mark_failed) и оператор не отличил бы
                # «drain поймал running» от «drain поймал ещё queued».
                pre_drain_status = fresh.status

                error_message = "worker_shutdown: terminated by SIGTERM/shutdown event"

                # Берём блокирующий SELECT FOR UPDATE: если `_runner`
                # успел пройти `mark_running` (или `mark_pending_for_retry`)
                # между нашим `get_by_id` и нашим UPDATE'ом, его транзакция
                # держит lock — мы дождёмся релиза и увидим уже-актуальные
                # `attempt` / `worker_id` / `scheduled_retry_at`. Это
                # закрывает micro-race с `register_running_task` → drain
                # перетёр attempt/worker_id и потерял один retry.
                stmt = (
                    select(task_repo.Task)
                    .where(task_repo.Task.id == tid)
                    .with_for_update()
                )
                locked = (await session.execute(stmt)).scalar_one_or_none()
                if locked is None:
                    continue

                # Snapshot worker_id ДО `mark_*` — `mark_pending_for_retry`
                # ставит worker_id=NULL (задача между попытками «ничейная»),
                # после UPDATE из ORM-объекта мы бы прочитали None и
                # потеряли информацию «какой pod выполнил drain».
                from src.tasks._runner_state import get_worker_id as _drain_wid
                drain_worker_id = locked.worker_id or _drain_wid()

                if (
                    locked.status == TaskStatus.QUEUED
                    and locked.scheduled_retry_at is not None
                ):
                    # `_runner.run_task` уже переложил row в queued со своим
                    # `scheduled_retry_at` (его back-off). Drain не должен
                    # переписывать поля — иначе мы сдвинем retry на now() и
                    # обнулим `worker_id`, что обманет sweep. Audit пишем как
                    # «зацепили queued»; pre_drain_status уже снят выше.
                    marked = locked
                    will_retry = locked.attempt < locked.max_attempts
                    audit_severity = "WARNING" if will_retry else "ERROR"
                # Pre-commit window (`register_running_task` уже выполнился,
                # но `mark_running` ещё не закоммитился) — `scheduled_retry_at`
                # тут None, и без drain'а row остаётся без timestamp'а: никто
                # её не подберёт. Здесь mark_pending_for_retry безопасен,
                # потому что _runner физически не может быть в середине
                # UPDATE — мы держим FOR UPDATE lock.
                #
                # Retry vs terminal — то же правило, что в `_runner`.
                # scheduled_retry_at = now() — следующий стартующий worker
                # подхватит row через `_recover_scheduled_retries` (он
                # фильтрует по `scheduled_retry_at IS NOT NULL AND <= now()`).
                # Без timestamp'а recovery её не увидит, и задача висит
                # queued до orphan-sweep'а или ручного вмешательства.
                elif locked.attempt < locked.max_attempts:
                    marked = await task_repo.mark_pending_for_retry(
                        session,
                        locked,
                        error_message,
                        scheduled_retry_at=datetime.now(timezone.utc),
                    )
                    audit_severity = "WARNING"
                    will_retry = True
                else:
                    marked = await task_repo.mark_failed(
                        session, locked, error_message,
                    )
                    audit_severity = "ERROR"
                    will_retry = False

                # CAS-guard в mark_pending_for_retry/mark_failed возвращает
                # None, если row была cancel'нута между нашим get_by_id и
                # UPDATE. Без этого check'а мы бы писали audit
                # `task.worker_shutdown` поверх уже cancelled-task'и и
                # `details.will_retry=True` обманывал бы оператора — retry
                # не запланирован, потому что UPDATE не прошёл. Симметрия
                # с `_runner.run_task`, который ровно так же скипает audit
                # при CAS-miss.
                if marked is None:
                    logger.info(
                        "graceful shutdown: task_id=%s cancelled mid-drain, "
                        "skipping worker_shutdown audit",
                        tid,
                    )
                    continue

                # `pre_drain_status` фиксирует, в каком состоянии row была
                # на момент drain'а. QUEUED означает, что `mark_running` CAS
                # ещё не прошёл (register_running_task вызван до commit'а),
                # и `attempt=0` тут — это «нулевая попытка», а не «упало на
                # первой» — без этого поля оператор не отличит две ситуации.
                # `worker_id` (snapshot до mark_*) — фиксирует хост/реплику,
                # где случился drain. Без него SIEM/оператор не могут связать
                # `task.worker_shutdown` с конкретным pod'ом
                # (terminationGracePeriodSeconds, OOM, rolling-update).
                details_payload: dict = {
                    "task_id": tid,
                    "reason": "worker_shutdown",
                    "attempt": fresh.attempt,
                    "max_attempts": fresh.max_attempts,
                    "will_retry": will_retry,
                    "pre_drain_status": pre_drain_status,
                    "worker_id": drain_worker_id,
                }
                if fresh.target_server_id:
                    details_payload["server_id"] = fresh.target_server_id
                await task_repo.enqueue_audit(
                    session,
                    task_id=tid,
                    payload={
                        "action": "task.worker_shutdown",
                        "status": "failure",
                        "allowed": False,
                        "target_id": tid,
                        "target_type": "task",
                        "request_id": fresh.request_id,
                        "actor_id": fresh.created_by,
                        "details": details_payload,
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
    if _pools_closed:
        # Кто-то перетасовал порядок WORKER_SHUTDOWN-хуков и пулы уже
        # закрыты — flush_outbox по-любому уйдёт в use-after-aclose.
        # Тихо выходим: drain уже отметил task'ам финальный status,
        # publisher следующего старта добьёт row из outbox.
        logger.warning(
            "graceful shutdown: outbox flush skipped — http pools already closed"
        )
    else:
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
    """Закрыть pooled HTTP-клиенты и shared Redis-pool после graceful drain.

    Ставится последним среди WORKER_SHUTDOWN-хендлеров, чтобы:

      * `_stop_audit_outbox_publisher` уже отменил background loop;
      * `_drain_running_tasks` доделал best-effort flush_outbox через
        `audit_client.emit` (он сам же дергает наш пул, поэтому пулы
        должны жить до этого момента).

    Порядок shutdown'а (порядок регистрации хуков выше):

      1. `_stop_dispatch_outbox_publisher` — отменяет dispatch loop.
      2. `_stop_audit_outbox_publisher` — отменяет audit-loop.
      3. `_drain_running_tasks` — ждёт running impl, mark_pending_for_retry
         либо mark_failed survivor'ов, + best-effort `flush_outbox()`
         (использует тот же `_publish_one` — пулы ещё живы).
      4. Этот хук — закрывает HTTP-пулы и shared Redis-pool.

    `_drain_running_tasks._safe_flush_outbox` идёт ПОСЛЕ stop'а publisher
    loop'а сознательно: drain работает синхронно через `_publish_one`,
    не зависит от background loop'а. Если порядок поменять (drain
    раньше publisher-stop), drain мог бы конкурировать с loop'ом за
    одни и те же row'ы, и SKIP LOCKED-выборка работала бы вхолостую.

    После выхода из этого хука taskiq закроет broker, и FD-учёт
    httpx-/Redis-пулов должен быть чистым.
    """
    global _pools_closed
    await http_pool.aclose_all()
    await redis_pool.aclose()
    _pools_closed = True


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
    """Один проход recovery: per-row claim → commit → kiq.

    Общая логика для startup-хука (`_recover_scheduled_retries`) и
    periodic-task'и (`tasks_recover_scheduled_retries`). startup поднимает
    потерянные при крэше retry-планы, periodic — потерянные в долгоживущем
    процессе (asyncio.create_task GC'нулся, `_RETRY_TASKS` set теряет
    ссылку, `_delayed_kick` ловит CancelledError при чужой отмене и
    re-raise'ит без re-kick'а).

    Per-row короткая транзакция вместо одного длинного FOR UPDATE'а: на
    backlog'е в 100+ task'ов старый код держал lock на всю SELECT-batch
    пока шёл kiq-loop (N×Redis-RTT), блокируя соседние replica'и и
    sweep-задачи. Сейчас каждый row claim'ается отдельным
    `SELECT FOR UPDATE LIMIT 1 → UPDATE scheduled_retry_at=NULL → COMMIT`,
    после чего lock отпускается и `kiq` уходит без открытой транзакции.
    """
    from src.db.session import AsyncSessionLocal
    from src.repositories import task as task_repo
    from src.utils.redaction import redact_error_message

    # Жёсткий cap на тик: даже если backlog огромный, не молотим в одном
    # проходе всё подряд. Следующий cron-тик доберёт остаток. Дефолт
    # `WORKER_RETRY_RECOVERY_MAX_PER_TICK=200` хватает на штатные сценарии;
    # incident-recovery (тысячи row'ов после длительного downtime'а)
    # требует временно поднять env и вернуть обратно после разгребания.
    # Cron `*/1 * * * *` и startup-hook оба зовут эту функцию, FOR UPDATE
    # SKIP LOCKED делает race-safe — суммарная пропускная способность
    # будет до 2 × cap/min (при одновременном starup + cron), но claim
    # уникален per-row.
    MAX_PER_TICK = _settings.worker_retry_recovery_max_per_tick
    recovered = 0
    skipped = 0
    failed_kiq = 0

    while recovered + skipped + failed_kiq < MAX_PER_TICK:
        try:
            async with AsyncSessionLocal() as session:
                t = await task_repo.claim_one_due_scheduled_retry(session)
                if t is None:
                    # Очередь due-task'ов исчерпана — нормальный выход.
                    await session.commit()
                    break
                task_id = t.id
                task_kind = t.task_kind
                attempt = t.attempt or 0
                await session.commit()
        except Exception as exc:  # noqa: BLE001
            redacted = redact_error_message(f"{type(exc).__name__}: {exc}")
            logger.warning(
                "scheduled_retries recovery: claim failed: %s",
                redacted,
            )
            # DB-flap на одном claim'е не должен класть весь recovery-цикл —
            # `continue` оставляет шанс остальным due-row'ам в backlog'е.
            # Cap-by-budget (`recovered+skipped+failed_kiq < MAX_PER_TICK`)
            # всё равно ограничивает proход; пустая выборка либо новый
            # exception выведут loop сами.
            failed_kiq += 1
            continue

        # Lock уже отпущен — kiq без открытой транзакции.
        target_task = broker.find_task(task_kind)
        if target_task is None:
            # Неизвестный task_kind: row claim'нута (scheduled_retry_at=NULL),
            # надо явно вернуть её, иначе она «потеряется». Здесь это
            # симптом deployment drift'а — operator-alert уровень.
            #
            # release_claimed_retry без аргумента ставит scheduled_retry_at=now(),
            # и следующий тик через минуту снова claim'нет ту же row, упрётся в
            # тот же `find_task is None` и пожжёт MAX_PER_TICK на пустой
            # busy-loop. Toggle с floor=60s через computed backoff даёт
            # operator'у окно поднять missing handler без того, чтобы recovery
            # пожирал весь cap каждую минуту.
            from src.tasks._runner import _compute_backoff_delay
            try:
                computed = _compute_backoff_delay(max(attempt, 1))
            except Exception:  # noqa: BLE001 — backoff'у нельзя ронять recovery
                computed = 0.0
            retry_in = max(60.0, computed)
            scheduled_at = datetime.now(timezone.utc) + timedelta(seconds=retry_in)
            logger.warning(
                "scheduled_retries recovery: broker does not know "
                "task_kind=%s (task_id=%s); deferring claim by %.0fs",
                task_kind,
                task_id,
                retry_in,
            )
            try:
                async with AsyncSessionLocal() as session:
                    await task_repo.release_claimed_retry(
                        session, task_id, scheduled_retry_at=scheduled_at,
                    )
                    await session.commit()
            except Exception as exc:  # noqa: BLE001
                redacted = redact_error_message(
                    f"{type(exc).__name__}: {exc}"
                )
                logger.warning(
                    "scheduled_retries recovery: release after unknown "
                    "task_kind failed task_id=%s: %s",
                    task_id,
                    redacted,
                )
            skipped += 1
            continue

        try:
            await target_task.kicker().kiq(task_id)
            recovered += 1
        except Exception as exc:  # noqa: BLE001
            redacted = redact_error_message(f"{type(exc).__name__}: {exc}")
            logger.warning(
                "scheduled_retries recovery: re-kick failed "
                "task_id=%s task_kind=%s: %s",
                task_id,
                task_kind,
                redacted,
            )
            failed_kiq += 1
            # Возвращаем row обратно в pool — следующий тик подберёт.
            try:
                async with AsyncSessionLocal() as session:
                    await task_repo.release_claimed_retry(session, task_id)
                    await session.commit()
            except Exception as inner:  # noqa: BLE001
                redacted = redact_error_message(
                    f"{type(inner).__name__}: {inner}"
                )
                logger.warning(
                    "scheduled_retries recovery: release after kiq-fail "
                    "task_id=%s: %s",
                    task_id,
                    redacted,
                )

    if recovered or failed_kiq or skipped:
        logger.info(
            "scheduled_retries recovery: re-kicked=%s failed_kiq=%s skipped=%s",
            recovered,
            failed_kiq,
            skipped,
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
    """Демо periodic task — тикает раз в минуту, когда scheduler enabled.

    Расписание — `*/1 * * * *` (минимальная cron-гранулярность). DB-видимости
    нет — для неё рядом крутится `worker.heartbeat`, который UPSERT'ит
    `worker_heartbeats`. Эта же task пишет logger.info «alive» — заглушка
    под будущий counter-snapshot (DLQ/breaker-skips/dropped_no_api_key)
    с тем же cron'ом, чтобы оператор видел в k8s log-aggregator регулярный
    маркер health'а worker'а.

    Когда `SCHEDULER_ENABLED=false` — task всё равно регистрируется на
    broker'е (нужна на worker-стороне, чтобы `find_task` её нашёл), но
    без `schedule` labels — scheduler её игнорирует.
    """
    # Counter-snapshot — дешёвый workaround под /metrics-endpoint, которого
    # пока нет. Если кто-то отдельно реализует Prometheus-экспортер, эту
    # snapshot-строку можно убрать. До тех пор INFO-строка раз в минуту в
    # journald позволяет grep'нуть инцидент задним числом.
    from src.services import audit_client as _ac
    from src.services import audit_outbox_publisher as _aop

    logger.info(
        "system.heartbeat tick — worker alive; "
        "audit_dlq_total=%s breaker_skips_total=%s dropped_no_api_key_total=%s",
        _aop.get_dlq_total(),
        _aop.get_breaker_skips_total(),
        _ac.get_dropped_no_api_key_total(),
    )


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
                orphan_details: dict = {
                    "task_id": fresh.id,
                    "reason": "worker_orphaned",
                    "worker_id": fresh.worker_id,
                    "attempt": fresh.attempt,
                    "max_attempts": fresh.max_attempts,
                }
                if fresh.target_server_id:
                    orphan_details["server_id"] = fresh.target_server_id
                await task_repo.enqueue_audit(
                    session,
                    task_id=fresh.id,
                    payload={
                        "action": "task.worker_orphaned",
                        "status": "failure",
                        "allowed": False,
                        "target_id": fresh.id,
                        "target_type": "task",
                        "request_id": fresh.request_id,
                        "actor_id": fresh.created_by,
                        "details": orphan_details,
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


@broker.task("dispatch_outbox.poll")
async def dispatch_outbox_poll_task() -> None:
    """Operator-ручка / ad-hoc kick'нуть один проход publisher'а.

    Штатно publisher работает фоновым `asyncio.create_task` loop'ом (см.
    `_start_dispatch_outbox_publisher`) с 2-секундным интервалом — таскю
    кидать руками не нужно. Регистрация здесь нужна, чтобы taskiq broker
    знал имя `dispatch_outbox.poll` (для testkit'а, ручного `kiq` из
    операторской консоли или будущего scheduler'а с sub-minute гранулярностью).
    """
    from src.tasks import dispatch_outbox

    await dispatch_outbox.poll_once()


@broker.task(
    "dispatch_outbox.cleanup_old",
    # 03:15 MSK = 00:15 UTC. Сдвиг от `tasks.cleanup_completed_old` (00:00 UTC)
    # и `audit_outbox.cleanup_published_old` (00:30 UTC) — три housekeeping
    # task'и не пересекаются по DB-write нагрузке.
    schedule=[{"cron": "15 0 * * *"}] if _settings.scheduler_enabled else [],
)
async def dispatch_outbox_cleanup_old_task() -> None:
    """Daily retention для dispatch_outbox (см. tasks/dispatch_outbox.py)."""
    from src.tasks import dispatch_outbox

    await dispatch_outbox.cleanup_old()


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

    # taskiq broker сериализует payload через JSON, но runtime-проверки на
    # тип аргументов не делает. Если кто-то kiq'нет `kiq("not-an-int")`,
    # SQLAlchemy попытается каст'нуть string → int внутри `session.get` и
    # бросит ProgrammingError. Отбиваем сразу, без открытия сессии.
    if not isinstance(row_id, int) or isinstance(row_id, bool):
        logger.warning(
            "internal.outbox_re_attempt: row_id must be int, got %r",
            row_id,
        )
        return False

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
    """Фоновая постепенная ре-шифрация секретов через outbox-pattern.

    Прежний поток (`POST /reencrypt_batch`) держал AsyncSession server_service'а
    открытой на весь decrypt-N → encrypt-N → UPDATE-N цикл и мог вычистить
    pool. Текущий поток разрезает работу на короткие транзакции:

      1. `SECRETS_REENCRYPT_ENABLED=false` → выйти молча (dev/test).
      2. Если `RUNNING_TASKS` непуст → skip-tick + audit (не конкурируем
         с power/SSH за DB-write'ы; следующий cron `*/5` снова попробует).
      3. `GET /internal/secrets/migration_status` для observability и
         APP_ENV-guard'а. Mismatch APP_ENV → abort + audit failure.
      4. Если `outbox.pending + outbox.processing == 0`, но `remaining > 0`
         (после bump'а версии ключа ещё ничего не посеяно) →
         `POST /reencrypt_outbox/seed` чтобы наполнить очередь.
      5. `GET /reencrypt_outbox/pending?limit=N` — claim батча (на сервере
         FOR UPDATE SKIP LOCKED, короткая транзакция).
      6. Для каждого claim'нутого row'а POST `.../{id}/done` — server
         делает per-row decrypt+encrypt+UPDATE в своей короткой транзакции.
         Если POST упал на HTTP/transport — POST `.../{id}/failed` чтобы
         row не залип в processing.

    Worker не держит мастер-ключ: crypto остаётся на server-side. Pool
    свободен — каждый запрос обслуживается отдельной короткой сессией.

    Ошибки внутри тика логируются (`redact_error_message`) и НЕ пробрасываются
    — periodic-task не должен крэшить scheduler-loop. Все audit-события идут
    через transactional outbox (`enqueue_audit` + `commit`), а не через прямой
    `audit_client.emit`.
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

        Same-pattern, что и у `_runner.run_task::_safe_flush_outbox`:
        commit потом best-effort `flush_outbox()`. Background-loop добьёт
        row, если happy-path flush упал.
        """
        try:
            async with AsyncSessionLocal() as session:
                await task_repo.enqueue_audit(session, task_id=None, payload=payload)
                await session.commit()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — best-effort audit
            logger.debug(
                "secrets.reencrypt_lazy: outbox enqueue failed: %s",
                redact_error_message(f"{type(exc).__name__}: {exc}"),
            )
            return
        try:
            await audit_outbox_publisher.flush_outbox()
        except asyncio.CancelledError:
            raise
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

    # APP_ENV guard — server_service сообщает свой APP_ENV в status.
    # Mismatch значит, что worker указывает на чужой контур и может переписать
    # чужие секреты — abort.
    remote_app_env = str(status.get("app_env") or "").strip()
    local_app_env = settings.app_env.strip()
    if remote_app_env and remote_app_env.lower() != local_app_env.lower():
        logger.error(
            "secrets.reencrypt_lazy: APP_ENV mismatch (worker=%s, server_service=%s) — aborting tick",
            local_app_env,
            remote_app_env,
        )
        await _enqueue_outbox_audit({
            "action": "secrets.reencrypt_tick",
            "status": "failure",
            "allowed": False,
            "target_type": "secret",
            "severity": "ERROR",
            "details": {
                "skipped": True,
                "reason": "app_env_mismatch",
                "worker_app_env": local_app_env,
                "server_service_app_env": remote_app_env,
            },
        })
        return

    remaining = int(status.get("remaining", 0))
    active_version = status.get("active_version")
    outbox_snapshot = status.get("outbox") or {}
    pending = int(outbox_snapshot.get("pending", 0))
    processing = int(outbox_snapshot.get("processing", 0))

    if remaining <= 0 and pending == 0 and processing == 0:
        logger.debug(
            "secrets.reencrypt_lazy: nothing to do (remaining=0, outbox empty, active=v%s)",
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

    # Если remaining>0, но outbox пуст — server-service ещё не сидил
    # outbox после bump'а версии. Запросим разовый seed; результат
    # подхватим тем же claim-ом ниже.
    seeded = 0
    if remaining > 0 and pending == 0 and processing == 0:
        try:
            seed_result = await server_service_client.seed_reencrypt_outbox(
                limit=max(settings.secrets_reencrypt_batch_size, 1) * 10
            )
            seeded = int(seed_result.get("inserted", 0))
        except CredentialFetchError as exc:
            logger.warning(
                "secrets.reencrypt_lazy: outbox seed failed: %s",
                redact_error_message(f"{exc.error_code}: {exc.message}"),
            )
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "secrets.reencrypt_lazy: outbox seed unexpected error: %s",
                redact_error_message(f"{type(exc).__name__}: {exc}"),
            )
            return

    # Claim'аем батч pending row'ов. Server-side короткая транзакция;
    # параллельные replica'и SKIP LOCKED'ом не пересекаются.
    try:
        items = await server_service_client.claim_reencrypt_outbox_pending(
            limit=settings.secrets_reencrypt_batch_size
        )
    except CredentialFetchError as exc:
        logger.warning(
            "secrets.reencrypt_lazy: claim failed: %s",
            redact_error_message(f"{exc.error_code}: {exc.message}"),
        )
        return
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "secrets.reencrypt_lazy: claim unexpected error: %s",
            redact_error_message(f"{type(exc).__name__}: {exc}"),
        )
        return

    processed = 0
    skipped = 0
    errors = 0

    for item in items:
        outbox_id = item.get("id")
        try:
            result = await server_service_client.finalize_reencrypt_outbox_done(
                outbox_id
            )
            if result.get("skipped"):
                skipped += 1
            else:
                processed += 1
        except ValueError:
            # `validate_outbox_id` отбил мусорный id — единственный источник
            # истины формата, без дублирующего isinstance-pre-check здесь.
            # Лог нужен на forensic-разбор, если server_service вдруг отдаст
            # битый id; `%r` покрывает None / dict / non-string.
            errors += 1
            logger.warning(
                "secrets.reencrypt_lazy: malformed outbox_id from server_service: %r",
                outbox_id,
            )
            # finalize_failed чтобы row не остался в `processing` навсегда:
            # claim уже забрал её, и без явного перевода в failed следующий
            # тик ничего не подберёт (claim берёт только pending), а оператор
            # увидит row, зависшую без видимых причин. Best-effort: сам id
            # битый, finalize_failed может тоже не пройти валидацию на стороне
            # server_service — тогда пишем в лог и идём дальше, cleanup-задача
            # позже подберёт.
            try:
                await server_service_client.finalize_reencrypt_outbox_failed(
                    outbox_id, error="malformed outbox_id from server_service",
                )
            except Exception as inner:  # noqa: BLE001
                logger.warning(
                    "secrets.reencrypt_lazy: finalize_failed on malformed id %r: %s",
                    outbox_id,
                    redact_error_message(f"{type(inner).__name__}: {inner}"),
                )
            continue
        except CredentialFetchError as exc:
            errors += 1
            logger.warning(
                "secrets.reencrypt_lazy: finalize_done failed id=%r: %s",
                outbox_id,
                redact_error_message(f"{exc.error_code}: {exc.message}"),
            )
            # Пометить failed — иначе row залипнет в `processing` до
            # ручного вмешательства. Best-effort: если и failed не
            # уходит, оставляем как есть; следующий тик не повторит
            # claim (row не в pending), но cleanup'ом не подберём.
            try:
                await server_service_client.finalize_reencrypt_outbox_failed(
                    outbox_id, error=f"{exc.error_code}: {exc.message}"
                )
            except Exception as inner:  # noqa: BLE001
                logger.warning(
                    "secrets.reencrypt_lazy: finalize_failed also failed id=%r: %s",
                    outbox_id,
                    redact_error_message(f"{type(inner).__name__}: {inner}"),
                )
        except Exception as exc:  # noqa: BLE001
            errors += 1
            logger.warning(
                "secrets.reencrypt_lazy: finalize_done unexpected error id=%r: %s",
                outbox_id,
                redact_error_message(f"{type(exc).__name__}: {exc}"),
            )
            # Зеркало CredentialFetchError-ветки: без finalize_failed row
            # застрянет в `processing`, следующий scheduler-цикл claim не
            # подберёт (claim берёт только pending), и cleanup'ом тоже не
            # вытащим. Best-effort failed-маркер.
            try:
                await server_service_client.finalize_reencrypt_outbox_failed(
                    outbox_id, error=f"{type(exc).__name__}: {exc}"
                )
            except Exception as inner:  # noqa: BLE001
                logger.warning(
                    "secrets.reencrypt_lazy: finalize_failed also failed id=%r: %s",
                    outbox_id,
                    redact_error_message(f"{type(inner).__name__}: {inner}"),
                )

    logger.info(
        "secrets.reencrypt_lazy: processed=%s skipped=%s errors=%s "
        "claimed=%s seeded=%s remaining_before=%s",
        processed,
        skipped,
        errors,
        len(items),
        seeded,
        remaining,
    )
    audit_details: dict = {
        "processed": processed,
        "skipped": skipped,
        "errors": errors,
        "claimed": len(items),
        "seeded": seeded,
        "remaining_before": remaining,
        "active_version": active_version,
        "batch_size": settings.secrets_reencrypt_batch_size,
    }
    if errors > 0:
        # Partial-failure: часть row'ов в processing-state не финализирована
        # (finalize_done упал, finalize_failed best-effort). `allowed=False`
        # — чтобы scheduler/sweep не считали тик за «всё хорошо» и могли
        # отличить чистый success от warning'а по одному полю.
        audit_details["reason"] = "finalize_errors"
    await _enqueue_outbox_audit({
        "action": "secrets.reencrypt_tick",
        "status": "success" if errors == 0 else "warning",
        "allowed": errors == 0,
        "target_type": "secret",
        "details": audit_details,
    })

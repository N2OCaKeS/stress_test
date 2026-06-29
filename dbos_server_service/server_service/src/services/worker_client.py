"""Мост к server_worker.

server_service делает две вещи, чтобы передать работу worker'у:

  1. INSERT в ``dev_server_worker.tasks`` через отдельный async-engine
     (cross-DB на том же Postgres-кластере). Эта строка — персистентный
     контракт: worker мутирует ``status`` / ``started_at`` /
     ``completed_at`` именно на ней.
  2. INSERT в ``dispatch_outbox`` (server_service-БД) с готовым payload'ом
     и `task_kind`. Outbox-row пишется в ту же транзакцию, что и доменные
     изменения caller'а — caller commit'нет одним атомарным `db.commit()`.

Публикация в Redis из этого модуля убрана. Её делает отдельный poller в
server_worker (Phase C): выбирает `dispatched_at IS NULL`, шлёт в брокер
и проставляет `dispatched_at = now()`. Падение между commit'ом и publish'ем
безопасно — следующий тик poller'а добёт строку.

Cross-DB asymmetry. INSERT в worker-БД и INSERT в outbox физически в разных
PostgreSQL-БД (на одном кластере), atomic 2PC мы не используем. Если worker-БД
INSERT успел, а caller'ский commit упал — лежит orphan-task без outbox, worker
её не подберёт. Этот сценарий узкий (commit на локальной БД редко падает) и
безопасный (orphan-task не запускается без публикации). Symmetric scenario —
outbox остался без worker-row — невозможен, потому что INSERT в worker-БД
идёт первым: если он не прошёл, до outbox-INSERT'а мы не доходим.
"""

import asyncio
import contextlib
import json
import logging
from datetime import datetime, timezone

import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from taskiq_redis import ListQueueBroker

from src.core.config import get_settings
from src.core.exceptions import ConflictError, ServiceUnavailableError
from src.repositories import dispatch_outbox as dispatch_outbox_repo
from src.services import audit_service, metrics
from src.services.redis_stash_crypto import (
    aad_for_redis_stash,
    encrypt_stash,
    stash_id_from_key,
)
from src.utils.ids import task_id

logger = logging.getLogger(__name__)

# Префикс Redis-ключа для одноразовых bootstrap-кред prepare'а. Креды лежат
# под `dbos:prepare_creds:<task_id>` с TTL — в task-payload едет только ссылка
# на ключ, plaintext в персистентную worker-БД не попадает.
PREPARE_CREDS_KEY_PREFIX = "dbos:prepare_creds:"


def prepare_creds_key(task_id_value: str) -> str:
    """Redis-ключ для bootstrap-кред конкретной prepare-задачи."""
    return f"{PREPARE_CREDS_KEY_PREFIX}{task_id_value}"


# Префикс Redis-ключа для inline-кред provision-таски (`account.provision`).
# Симметрично `PREPARE_CREDS_KEY_PREFIX`, но своя scope: prepare держит
# bootstrap-логин/пароль ОС-юзера, dispatch держит password+ssh_private_key
# аккаунта для useradd/chpasswd на боксе.
DISPATCH_CREDS_KEY_PREFIX = "dbos:dispatch_creds:"


def dispatch_creds_key(stash_id_value: str) -> str:
    """Redis-ключ для inline-кред конкретного provision dispatch'а."""
    return f"{DISPATCH_CREDS_KEY_PREFIX}{stash_id_value}"


# Префикс Redis-ключа для кред интерактивной SSH-консоли. Логин/пароль
# выбранного server_account'а кладутся под `dbos:console_creds:<ccd_id>` с TTL;
# worker читает их по ссылке из `start`-control-сообщения и коннектится под
# аккаунтом (password-auth). Своя scope, чтобы worker-валидатор отличал её от
# provision-stash'а (`dbos:dispatch_creds:`).
CONSOLE_CREDS_KEY_PREFIX = "dbos:console_creds:"


def console_creds_key(stash_id_value: str) -> str:
    """Redis-ключ для кред конкретной console-сессии."""
    return f"{CONSOLE_CREDS_KEY_PREFIX}{stash_id_value}"


# ── Интерактивная SSH-консоль: pub/sub каналы console-моста ──────────────
# worker не держит HTTP/WS-сервера, поэтому транспорт между клиентским
# WebSocket'ом и удалённым shell'ом — Redis pub/sub. server_service публикует
# `start`/`stop` в control-канал и мостит WS на data-каналы; worker слушает
# `console:ctl:*`, поднимает PTY и отвечает в те же каналы. Префиксы держим
# в sync с `server_worker/src/services/console_bridge.py`.
CONSOLE_CTL_CHANNEL_PREFIX = "console:ctl:"
CONSOLE_IN_CHANNEL_PREFIX = "console:in:"
CONSOLE_OUT_CHANNEL_PREFIX = "console:out:"


def console_ctl_channel(session_id: str) -> str:
    return f"{CONSOLE_CTL_CHANNEL_PREFIX}{session_id}"


def console_in_channel(session_id: str) -> str:
    return f"{CONSOLE_IN_CHANNEL_PREFIX}{session_id}"


def console_out_channel(session_id: str) -> str:
    return f"{CONSOLE_OUT_CHANNEL_PREFIX}{session_id}"


def get_worker_redis() -> "aioredis.Redis":
    """Вернуть Redis-клиент к worker-брокеру для console pub/sub.

    Переиспользует pooled `_creds_redis_client` (поднятый в lifespan), иначе
    строит per-call клиент. Для console-моста (subscribe + длинный listen)
    caller обязан закрывать per-call клиент сам — поэтому когда пул не поднят,
    отдаём свежий клиент, владение которым переходит caller'у.

    Незаданный `SERVER_WORKER_REDIS_URL` → `ServiceUnavailableError(
    WORKER_REDIS_NOT_CONFIGURED)`, как и остальной dispatch.
    """
    settings = get_settings()
    if not settings.server_worker_redis_url:
        raise ServiceUnavailableError(
            error_code="WORKER_REDIS_NOT_CONFIGURED",
            message="SERVER_WORKER_REDIS_URL is not set",
        )
    if _creds_redis_client is not None:
        return _creds_redis_client
    return aioredis.from_url(settings.server_worker_redis_url)


async def publish_console_control(session_id: str, message: dict) -> None:
    """Опубликовать control-сообщение (`start`/`stop`) для console-сессии.

    Сообщение — JSON в `console:ctl:<sid>`. Используется WS-эндпоинтом на
    connect (start) и disconnect (stop). Pooled клиент не закрываем (общий);
    при fallback-клиенте закрываем после publish.
    """
    client = get_worker_redis()
    own = client is not _creds_redis_client
    try:
        await client.publish(console_ctl_channel(session_id), json.dumps(message))
    finally:
        if own:
            await client.aclose()


# Module-level state. При `uvicorn --workers >1` каждый воркер — отдельный
# процесс с собственным event loop и своим module-level state (включая lock
# и `_broker_started`); taskiq устроен так, что каждый воркер имеет свой
# broker — это by design.
_worker_engine = None
_worker_session_factory = None
_worker_broker: ListQueueBroker | None = None
_broker_started = False
# Lock защищает race на `_ensure_broker_started`: два concurrent `dispatch_task`
# в одном event loop могут оба пройти проверку `not _broker_started` до того,
# как первый успеет выставить флаг, и оба вызвать `broker.startup()` → второй
# startup может упасть / привести к inconsistent state в taskiq-redis
# (идемпотентность — implementation detail библиотеки, не контракт).
#
# Lock — singleton per event loop. Multi-process uvicorn → каждый процесс
# свой broker (by design taskiq), lock в этом сценарии работать и не должен.
_broker_lock = asyncio.Lock()

# Pooled aioredis-клиент для стэша worker-кред: prepare-bootstrap, dispatch
# (provision/rotate), console-сессии и их delete. До этого каждый стор дёргал
# `aioredis.from_url(...)` на вызов — burst исчерпывал FD'ы и connection-budget
# Redis'а. Поднимается из lifespan в `src/main.py` (см. там же `aclose`).
# Если None — fallback на per-call client (для unit-тестов вне lifespan).
_creds_redis_client: aioredis.Redis | None = None


def _engine_factory():
    """Лениво строит async-engine для cross-DB вставок в `dev_server_worker.tasks`.

    Отдельный engine, не основной session — чтобы pool worker-БД не
    конкурировал с пулом для собственных запросов сервиса.
    """
    global _worker_engine, _worker_session_factory
    settings = get_settings()
    if not settings.server_worker_database_url:
        raise ServiceUnavailableError(
            error_code="WORKER_DB_NOT_CONFIGURED",
            message="SERVER_WORKER_DATABASE_URL is not set",
        )
    if _worker_engine is None:
        _worker_engine = create_async_engine(
            settings.server_worker_database_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=5,
            pool_recycle=1800,
        )
        _worker_session_factory = async_sessionmaker(
            bind=_worker_engine, expire_on_commit=False
        )
    return _worker_session_factory


def _build_broker() -> ListQueueBroker:
    """Создать broker и зарегистрировать task-name стабы (lazy, singleton).

    Стабы существуют только чтобы можно было вызвать ``.kiq()`` из этого
    сервиса — настоящие handler'ы с теми же именами живут в server_worker.
    taskiq резолвит handler по строковому имени, отдельный dispatch-map
    нам не нужен.

    **Dispatch map (task_kind → endpoint).**

    Сейчас все task-kinds активно dispatch'атся из endpoints;
    501-заглушек больше нет. Соответствие task-kind'ов и endpoint'ов:

    * ``power.on`` / ``power.off`` / ``power.reboot`` —
      `endpoints/ipmi.py::_dispatch_power`.
    * ``power.status`` — `endpoints/worker_dispatch.py::server_power_status`
      (live BMC-probe).
    * ``inventory.sync`` — `endpoints/worker_dispatch.py::server_inventory_sync`.
    * ``installed_packages.list`` —
      `endpoints/installed_packages.py::list_installed_packages` (live SSH).
    * ``users.inventory`` —
      `endpoints/worker_dispatch.py::server_users_inventory` (getent reconcile).
    * ``account.rotate_password`` —
      `endpoints/worker_dispatch.py::account_rotate_password_dispatch`
      (точечная `?server_id=` / массовая fan-out).
    * ``account.provision`` / ``account.update_on_host`` /
      ``account.deprovision`` —
      `endpoints/worker_dispatch.py::account_provision_on_host` /
      `account_update_on_host` / `account_deprovision`. Плюс fan-out
      `update_on_host` из PATCH аккаунта (`fanout_update_on_host`).
    * ``ipmi.rotate_password`` —
      `endpoints/worker_dispatch.py::ipmi_rotate_credentials_dispatch`
      (worker делает verify-then-submit: PATCH пароля на BMC, read-only
      verify под новым паролем, затем submit в server_service — см.
      `server_worker/src/tasks/passwords.py::ipmi_rotate_password`).
    * ``server.prepare`` — `endpoints/worker_dispatch.py::server_prepare`
      (bootstrap-креды едут через Redis по `bootstrap_creds_key`).
    """
    global _worker_broker
    settings = get_settings()
    if not settings.server_worker_redis_url:
        raise ServiceUnavailableError(
            error_code="WORKER_REDIS_NOT_CONFIGURED",
            message="SERVER_WORKER_REDIS_URL is not set",
        )
    if _worker_broker is not None:
        return _worker_broker

    broker = ListQueueBroker(url=settings.server_worker_redis_url)

    # ── Стабы регистрируем под все диспатчуемые task-kinds. Реальные
    # handler'ы с теми же именами живут в server_worker; здесь нужны только
    # пустые `async def` под `.kiq()` (taskiq резолвит handler по имени).

    @broker.task("power.on")
    async def _power_on(task_id: str) -> None:  # noqa: ARG001
        return None

    @broker.task("power.off")
    async def _power_off(task_id: str) -> None:  # noqa: ARG001
        return None

    @broker.task("power.reboot")
    async def _power_reboot(task_id: str) -> None:  # noqa: ARG001
        return None

    @broker.task("power.status")
    async def _power_status(task_id: str) -> None:  # noqa: ARG001
        return None

    @broker.task("inventory.sync")
    async def _inventory_sync(task_id: str) -> None:  # noqa: ARG001
        return None

    @broker.task("account.rotate_password")
    async def _account_rotate(task_id: str) -> None:  # noqa: ARG001
        return None

    @broker.task("ipmi.rotate_password")
    async def _ipmi_rotate(task_id: str) -> None:  # noqa: ARG001
        return None

    @broker.task("installed_packages.list")
    async def _installed_packages_list(task_id: str) -> None:  # noqa: ARG001
        return None

    @broker.task("users.inventory")
    async def _users_inventory(task_id: str) -> None:  # noqa: ARG001
        return None

    @broker.task("account.provision")
    async def _account_provision(task_id: str) -> None:  # noqa: ARG001
        return None

    @broker.task("account.update_on_host")
    async def _account_update_on_host(task_id: str) -> None:  # noqa: ARG001
        return None

    @broker.task("account.deprovision")
    async def _account_deprovision(task_id: str) -> None:  # noqa: ARG001
        return None

    @broker.task("server.prepare")
    async def _server_prepare(task_id: str) -> None:  # noqa: ARG001
        return None

    @broker.task("management_user_sync")
    async def _management_user_sync(task_id: str) -> None:  # noqa: ARG001
        return None

    _worker_broker = broker
    return broker


async def shutdown_broker() -> None:
    """Закрыть taskiq-broker и сбросить модульные слоты.

    Зовётся из lifespan на shutdown сервиса; без него Redis-соединение
    брокера полагается на GC, а в тестах с несколькими lifespan'ами
    подряд (`app.router.lifespan_context`) предыдущий `_worker_broker` /
    `_broker_started` течёт в следующий запуск и портит изоляцию.

    Best-effort: ошибки `broker.shutdown()` глотаем — на пути shutdown
    важнее, чтобы lifespan не упал, чем чтобы Redis-таймауты доехали.
    """
    global _worker_broker, _broker_started, _worker_engine, _worker_session_factory
    broker = _worker_broker
    if broker is not None and _broker_started:
        # broker.shutdown() best-effort: Redis недоступен, разрыв соединения —
        # неважно, lifespan продолжаем закрывать. Suppress вместо try/except: pass.
        with contextlib.suppress(Exception):
            await broker.shutdown()
    _worker_broker = None
    _broker_started = False
    # Engine для cross-DB вставок в `dev_server_worker.tasks` тоже надо
    # закрыть — иначе в тестах с несколькими lifespan-циклами старый engine
    # держит pool до 5+5 connections к worker-БД.
    engine = _worker_engine
    if engine is not None:
        with contextlib.suppress(Exception):
            await engine.dispose()
    _worker_engine = None
    _worker_session_factory = None


async def _ensure_broker_started() -> None:
    """Идемпотентный broker startup с защитой от race в одном event loop.

    Double-check паттерн: первый check без lock (fast path для уже-стартованного
    brokerа — без contention'а), второй check внутри lock (защищает от двух
    concurrent вызовов, прошедших первую проверку одновременно).

    Если `broker.startup()` падает (Redis недоступен) — `_broker_started`
    остаётся `False`, lock освобождается, следующий вызов попробует снова.
    `_build_broker` сам raise'ит `ServiceUnavailableError(WORKER_REDIS_NOT_CONFIGURED)`
    до lock'а, чтобы не сериализовать missing-config ошибки.
    """
    global _broker_started
    broker = _build_broker()
    if _broker_started:
        return
    async with _broker_lock:
        if _broker_started:
            return
        await broker.startup()
        _broker_started = True


async def _get_task_by_idempotency_key(
    key: str,
) -> tuple[str, str, str | None] | None:
    """SELECT существующего tasks по idempotency_key.

    Возвращает `(task_id, task_kind, target_server_id)` либо `None`. Caller
    (`dispatch_task`) сверяет `task_kind` / `target_server_id` ранее
    зарегистрированной задачи с новым запросом: расхождение значит, что
    клиент reuse'ит ключ под другую операцию, и мы не имеем права молча
    отдать старый task_id (confused-deputy).

    Используется cross-DB worker-engine — `tasks` живёт в dev_server_worker.
    """
    session_factory = _engine_factory()
    async with session_factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT id, task_kind, target_server_id FROM tasks "
                    "WHERE idempotency_key = :key"
                ),
                {"key": key},
            )
        ).first()
        if row is None:
            return None
        return row[0], row[1], row[2]


async def lookup_existing_task(
    idempotency_key: str,
) -> tuple[str, str, str | None] | None:
    """Публичный SELECT существующего task'а по idempotency_key.

    Возвращает `(task_id, task_kind, target_server_id)` либо `None`. Caller
    использует ответ, чтобы решить, надо ли вообще генерить новые секреты
    перед dispatch'ом: на idempotent-replay сторонние мутации (creds в БД,
    stash в Redis) делать нельзя, иначе бокс и server-БД разойдутся.
    Логика сверки kind/target — на caller'е (он знает, какой error_code
    бить и какие audit-details писать).
    """
    return await _get_task_by_idempotency_key(idempotency_key)


def _ensure_idempotency_matches(
    *,
    existing: tuple[str, str, str | None],
    task_kind: str,
    target_server_id: str | None,
) -> None:
    """Проверить, что reuse'нутый Idempotency-Key пришёл на ту же операцию.

    Клиент имеет право повторить тот же POST с тем же ключом — это
    идемпотентный retry. Но если он ткнул тот же ключ для другой операции
    (другой `task_kind` или другой `target_server_id`), отдать ему старый
    task_id значит соврать про то, что мы поставили: confused-deputy.
    В таком случае поднимаем 409 `IDEMPOTENCY_KEY_REUSE_CONFLICT` — клиент
    обязан сгенерировать новый ключ.
    """
    _existing_id, existing_kind, existing_target = existing
    if existing_kind == task_kind and existing_target == target_server_id:
        return
    raise ConflictError(
        error_code="IDEMPOTENCY_KEY_REUSE_CONFLICT",
        message=(
            "Idempotency-Key already used for a different operation "
            "(task_kind/target_server_id mismatch)"
        ),
        details={
            "existing_task_kind": existing_kind,
            "existing_target_server_id": existing_target,
            "requested_task_kind": task_kind,
            "requested_target_server_id": target_server_id,
        },
    )


# Приоритет worker-task'и: больше = раньше в claim'е воркера. Источник
# семантики — `server_worker/src/models/task.py::Task.priority`. server_service
# не импортирует пакет server_worker (отдельная БД/codebase), поэтому уровни
# дублируются локально. При изменении набора синхронизировать вручную.
TASK_PRIORITY_NORMAL = 0
TASK_PRIORITY_HIGH = 100


async def _insert_task_row(
    *,
    new_task_id: str,
    task_kind: str,
    target_server_id: str | None,
    target_resource_id: str | None,
    payload: dict,
    created_by: str | None,
    request_id: str | None,
    idempotency_key: str | None = None,
    priority: int = TASK_PRIORITY_NORMAL,
) -> None:
    """Сырая INSERT-операция в `dev_server_worker.tasks`. Параметры через bound."""
    session_factory = _engine_factory()
    async with session_factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO tasks (
                    id, task_kind, target_server_id, target_resource_id,
                    payload, status, attempt, max_attempts, priority,
                    created_by, request_id, idempotency_key
                ) VALUES (
                    :id, :task_kind, :target_server_id, :target_resource_id,
                    CAST(:payload AS jsonb), 'queued', 0, 3, :priority,
                    :created_by, :request_id, :idempotency_key
                )
                """
            ),
            {
                "id": new_task_id,
                "task_kind": task_kind,
                "target_server_id": target_server_id,
                "target_resource_id": target_resource_id,
                "payload": json.dumps(payload),
                "priority": priority,
                "created_by": created_by,
                "request_id": request_id,
                "idempotency_key": idempotency_key,
            },
        )
        await session.commit()


# Cancellable-статусы taskiq lifecycle — task только в queued/running ещё
# можно перевести в cancelled; succeeded/failed/cancelled — terminal.
#
# Источник правды по самим именам статусов — `server_worker/src/models/
# task.py::TaskStatus` (SQLAlchemy enum), оттуда же — CAS-предикаты в
# `server_worker/src/repositories/task.py`. Здесь — локальная копия
# строковых литералов, потому что server_service не импортирует пакет
# server_worker (отдельная БД, отдельная codebase, отдельный сервис).
# При изменении набора terminal/non-terminal статусов в server_worker
# нужно вручную синхронизировать этот set.
_CANCELLABLE_STATUSES: frozenset[str] = frozenset({"queued", "running"})


async def _fetch_task_status_and_meta(task_id_value: str) -> dict | None:
    """Подгрузить статус и метаданные target'а task'и из dev_server_worker.tasks.

    Возвращает dict со status / target_server_id / task_kind / created_by либо
    None, если row не существует. `created_by` нужен cancel-эндпоинту для
    отличения системных task'ов (heartbeat/sweep/cleanup_completed — created_by
    IS NULL, target_server_id IS NULL) от пользовательских: системные требуют
    отдельной гарды по платформенной роли.
    """
    session_factory = _engine_factory()
    async with session_factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT status, target_server_id, task_kind, created_by "
                    "FROM tasks WHERE id = :id"
                ),
                {"id": task_id_value},
            )
        ).first()
        if row is None:
            return None
        return {
            "status": row[0],
            "target_server_id": row[1],
            "task_kind": row[2],
            "created_by": row[3],
        }


# Колонки таблицы `dev_server_worker.tasks`, которые отдаёт read. Имена
# здесь — как в БД; маппинг в API-поля (`task_kind`→`kind` и т.д.) делает
# endpoint при сборке `TaskRead`. `payload` сознательно не тащим: там может
# быть ссылка на Redis-stash кред, а в карточку задачи он не нужен.
_TASK_READ_COLUMNS = (
    "id, task_kind, status, target_server_id, target_resource_id, "
    "attempt, last_error, result, enqueued_at, started_at, "
    "completed_at, created_by, priority"
)


def _row_to_task_dict(row) -> dict:
    """Кортеж SELECT'а `_TASK_READ_COLUMNS` → dict с именами колонок БД."""
    return {
        "id": row[0],
        "task_kind": row[1],
        "status": row[2],
        "target_server_id": row[3],
        "target_resource_id": row[4],
        "attempt": row[5],
        "last_error": row[6],
        "result": row[7],
        "enqueued_at": row[8],
        "started_at": row[9],
        "completed_at": row[10],
        "created_by": row[11],
        "priority": row[12],
    }


async def get_task(task_id_value: str) -> dict | None:
    """Полная строка task'и из dev_server_worker.tasks для detail-вьюхи.

    Возвращает dict с ключами-именами колонок БД (включая полный `result`
    и `last_error`) либо None, если row нет. Visibility/permission решает
    endpoint — здесь чистый read по PK.
    """
    session_factory = _engine_factory()
    async with session_factory() as session:
        row = (
            await session.execute(
                text(f"SELECT {_TASK_READ_COLUMNS} FROM tasks WHERE id = :id"),
                {"id": task_id_value},
            )
        ).first()
        if row is None:
            return None
        return _row_to_task_dict(row)


async def list_tasks(
    *,
    status: str | None = None,
    task_kind: str | None = None,
    server_ids: list[str] | None = None,
    include_infra: bool = False,
    created_by: str | None = None,
    limit: int,
    offset: int,
) -> tuple[list[dict], int]:
    """Страница task'ов из dev_server_worker.tasks + total под тем же фильтром.

    Dept-scope резолвится на стороне endpoint'а и приходит сюда уже как
    `server_ids` — множество видимых caller'у серверов. Семантика:

    * `server_ids is None` → ограничения по серверу нет (вызывающий уже решил,
      что caller видит всё — на сегодня такого пути нет, оставлено под будущие
      cluster-wide роли).
    * `server_ids == []` и `include_infra=False` → пусто (нечего показывать).
    * `server_ids` непустой → `target_server_id IN (...)`; при `include_infra`
      дополнительно подмешиваются строки с `target_server_id IS NULL`
      (инфра-задачи без сервера — видны только admin/operator-роли).

    `created_by` (если задан) дополнительно сужает выборку до задач, которые
    поставил сам caller — для непривилегированного reader'а, который видит
    только свои задачи в рамках своего отдела.

    `result` тащим целиком и усекаем в summary уже на стороне endpoint'а —
    отдельный «лёгкий» SELECT без JSONB не делаем, чтобы не плодить вторую
    форму запроса; усечение дешевле, чем второй round-trip.

    Сортировка — `enqueued_at DESC` (свежие сверху, индекс
    `ix_tasks_status_enqueued`). total — COUNT под идентичным WHERE, для
    `X-Total-Count`.
    """
    where_parts: list[str] = []
    params: dict = {}

    if status is not None:
        where_parts.append("status = :status")
        params["status"] = status
    if task_kind is not None:
        where_parts.append("task_kind = :task_kind")
        params["task_kind"] = task_kind
    if created_by is not None:
        where_parts.append("created_by = :created_by")
        params["created_by"] = created_by

    # Dept-scope по серверу. `IN (...)` собирается из именованных bind'ов, чтобы
    # не клеить идентификаторы в SQL строкой. Пустой список серверов без
    # include_infra означает «caller видит 0 задач» — выходим коротко.
    if server_ids is not None:
        if server_ids:
            placeholders = []
            for idx, sid in enumerate(server_ids):
                key = f"sid_{idx}"
                params[key] = sid
                placeholders.append(f":{key}")
            in_clause = "target_server_id IN (" + ", ".join(placeholders) + ")"
            if include_infra:
                where_parts.append(f"({in_clause} OR target_server_id IS NULL)")
            else:
                where_parts.append(in_clause)
        elif include_infra:
            where_parts.append("target_server_id IS NULL")
        else:
            return [], 0

    where_sql = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""

    session_factory = _engine_factory()
    async with session_factory() as session:
        total = int(
            (
                await session.execute(
                    text(f"SELECT COUNT(*) FROM tasks{where_sql}"), params,
                )
            ).scalar_one()
        )
        if total == 0:
            return [], total
        rows = (
            await session.execute(
                text(
                    f"SELECT {_TASK_READ_COLUMNS} FROM tasks{where_sql} "
                    "ORDER BY enqueued_at DESC, id DESC LIMIT :limit OFFSET :offset"
                ),
                {**params, "limit": limit, "offset": offset},
            )
        ).all()
        return [_row_to_task_dict(r) for r in rows], total


# История package-запросов тащит ещё и `payload` (запрошенный паттерн),
# который generic `_TASK_READ_COLUMNS` сознательно прячет — у prepare/provision
# payload несёт ссылку на Redis-stash с кредами. Здесь это безопасно: выборка
# жёстко прибита к kind'у `installed_packages.list`, чей payload — только
# `{patterns, pattern, max_rows, ...}`, без секретов.
_PACKAGE_HISTORY_COLUMNS = (
    "id, status, payload, result, enqueued_at, completed_at, created_by, last_error"
)
_PACKAGE_HISTORY_KIND = "installed_packages.list"


async def list_package_history(
    *,
    server_id: str,
    limit: int,
    offset: int,
) -> tuple[list[dict], int]:
    """Страница прошлых `installed_packages.list`-задач одного сервера.

    Отдельный read от generic `list_tasks`: тащит `payload` (что запрашивали),
    который тот не отдаёт. Жёстко ограничен kind'ом `installed_packages.list`,
    поэтому payload без ссылок на креды (в отличие от prepare/provision).
    Сортировка `enqueued_at DESC` (свежие сверху), `total` — COUNT под тем же
    фильтром для `X-Total-Count`. Visibility/permission решает endpoint.
    """
    params = {"sid": server_id, "kind": _PACKAGE_HISTORY_KIND}
    where_sql = "WHERE task_kind = :kind AND target_server_id = :sid"
    session_factory = _engine_factory()
    async with session_factory() as session:
        total = int(
            (
                await session.execute(
                    text(f"SELECT COUNT(*) FROM tasks {where_sql}"), params,
                )
            ).scalar_one()
        )
        if total == 0:
            return [], total
        rows = (
            await session.execute(
                text(
                    f"SELECT {_PACKAGE_HISTORY_COLUMNS} FROM tasks {where_sql} "
                    "ORDER BY enqueued_at DESC, id DESC LIMIT :limit OFFSET :offset"
                ),
                {**params, "limit": limit, "offset": offset},
            )
        ).all()
        return [
            {
                "id": r[0],
                "status": r[1],
                "payload": r[2],
                "result": r[3],
                "enqueued_at": r[4],
                "completed_at": r[5],
                "created_by": r[6],
                "last_error": r[7],
            }
            for r in rows
        ], total


async def cancel_task(
    *,
    task_id_value: str,
    cancelled_by: str | None,
    cancel_reason: str | None,
) -> dict:
    """Atomic CAS-перевод задачи в CANCELLED. Возвращает финальное состояние.

    Поведение:

    * row нет → возвращаем ``{"found": False}``. Caller (endpoint) поднимет
      404 ``TASK_NOT_FOUND``.
    * row есть в `queued`/`running` → UPDATE WHERE status IN (...). Если
      UPDATE затронул строку — возвращаем ``{"found": True, "cancelled":
      True, "task_kind": ..., "target_server_id": ...}``.
    * row есть, но статус уже terminal (`succeeded`/`failed`/`cancelled`) —
      возвращаем ``{"found": True, "cancelled": False, "previous_status":
      ...}``. Caller поднимет 409 ``TASK_NOT_CANCELLABLE``.

    CAS условие пишется в `WHERE status IN ('queued','running')`, поэтому
    параллельный mark_succeeded из worker'а либо мы — кто первый. Без CAS
    можно было бы перетереть finalize'нувшийся row и потерять `last_error`.
    """
    now_iso = datetime.now(timezone.utc)
    session_factory = _engine_factory()
    async with session_factory() as session:
        meta = (
            await session.execute(
                text(
                    "SELECT status, task_kind, target_server_id "
                    "FROM tasks WHERE id = :id FOR UPDATE"
                ),
                {"id": task_id_value},
            )
        ).first()
        if meta is None:
            await session.rollback()
            return {"found": False}
        previous_status = meta[0]
        task_kind = meta[1]
        target_server_id = meta[2]
        if previous_status not in _CANCELLABLE_STATUSES:
            await session.rollback()
            return {
                "found": True,
                "cancelled": False,
                "previous_status": previous_status,
                "task_kind": task_kind,
                "target_server_id": target_server_id,
            }
        # CAS-форма UPDATE на тот же `status IN (...)` — на случай, если
        # между SELECT FOR UPDATE и UPDATE кто-то всё-таки умудрился
        # finalize'нуть row (advisory-lock из FOR UPDATE не блокирует
        # UPDATE из той же транзакции, но тут лишним не будет).
        result = await session.execute(
            text(
                """
                UPDATE tasks
                SET status = 'cancelled',
                    cancelled_by = :cancelled_by,
                    cancelled_at = :cancelled_at,
                    cancel_reason = :cancel_reason,
                    completed_at = :cancelled_at
                WHERE id = :id AND status IN ('queued', 'running')
                """
            ),
            {
                "id": task_id_value,
                "cancelled_by": cancelled_by,
                "cancelled_at": now_iso,
                "cancel_reason": cancel_reason,
            },
        )
        if (result.rowcount or 0) == 0:
            await session.rollback()
            # SELECT увидел cancellable, UPDATE — нет: race с worker'ом,
            # успевшим finalize'нуть task. Перечитаем фактический статус,
            # чтобы и response, и audit получили актуальное значение, а не
            # устаревшее queued/running, которое мы успели прочитать первым.
            re_meta = (
                await session.execute(
                    text("SELECT status FROM tasks WHERE id = :id"),
                    {"id": task_id_value},
                )
            ).first()
            if re_meta is not None:
                previous_status = re_meta[0]
            return {
                "found": True,
                "cancelled": False,
                "previous_status": previous_status,
                "task_kind": task_kind,
                "target_server_id": target_server_id,
            }
        await session.commit()
        return {
            "found": True,
            "cancelled": True,
            "previous_status": previous_status,
            "task_kind": task_kind,
            "target_server_id": target_server_id,
        }


async def _delete_task_row(task_id_to_delete: str) -> None:
    """Best-effort DELETE для отката zombie-row после неудачной публикации в Redis.

    Используется только из `dispatch_task` при сбое outbox-INSERT'а —
    INSERT в `dev_server_worker.tasks` уже закоммичен, и без отката
    строка останется `queued` навсегда (poller её не подберёт, потому
    что outbox-row нет, в Redis-очереди публикация не произойдёт).

    Если DELETE сам упадёт (та же worker-БД может быть недоступна — но это
    маловероятно: INSERT только что прошёл) — глушим исключение и даём
    наружу подняться оригинальной ошибке публикации. В худшем сценарии
    останется один zombie-row, но мы хотя бы сообщили клиенту 503 (см.
    `dispatch_task` ниже) — это лучше, чем 500 от вторичной ошибки DELETE'а.
    """
    try:
        session_factory = _engine_factory()
        async with session_factory() as session:
            await session.execute(
                text("DELETE FROM tasks WHERE id = :id"),
                {"id": task_id_to_delete},
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        # best-effort rollback — оригинальная ошибка важнее. Структурный
        # warning даёт операторам сигнал «остался orphan worker-row, без
        # выполнения, без публикации в Redis» — он безопасен, но забивает
        # таблицу tasks до retention cleanup'а. Счётчик `worker_dispatch_orphans`
        # бьём здесь, а не при удачном DELETE: успешная компенсация orphan'а не
        # оставляет, growth счётчика — это именно «реальный orphan лёг».
        metrics.increment_worker_dispatch_orphans()
        logger.warning(
            "worker-row compensation delete failed, orphan task row remains",
            extra={
                "event": "orphan_worker_row",
                "task_id": task_id_to_delete,
                "exc_type": exc.__class__.__name__,
            },
        )
        # Парный audit-event в loging — SIEM видит факт orphan'а отдельно от
        # логов pod'а. emit best-effort, на ошибке audit-канала глушим, иначе
        # рекурсия в compensation-fail'е никому не помогает.
        try:
            audit_service.emit(
                "worker_dispatch.orphan_detected",
                target_id=task_id_to_delete,
                target_type="task",
                status="failure",
                allowed=True,
                details={"compensation_exc": exc.__class__.__name__},
            )
        except Exception:  # noqa: BLE001
            pass


async def delete_prepare_creds(creds_key: str) -> None:
    """Снять bootstrap-креды из Redis (best-effort).

    Зовётся из server_prepare_dispatch, когда `dispatch_task` падает уже после
    `store_prepare_creds` — иначе plaintext-пароль висит в Redis до истечения
    TTL. Сама операция не должна валить ответ клиенту: если Redis недоступен
    или ключ уже исчез — глушим исключение.
    """
    settings = get_settings()
    if not settings.server_worker_redis_url:
        return
    pooled = _creds_redis_client
    try:
        if pooled is not None:
            await pooled.delete(creds_key)
            return
        client = aioredis.from_url(settings.server_worker_redis_url)
        try:
            await client.delete(creds_key)
        finally:
            await client.aclose()
    except Exception:  # noqa: BLE001
        # Best-effort: TTL подчистит ключ, если DEL не прошёл.
        pass


async def store_prepare_creds(creds_key: str, creds: dict) -> None:
    """Положить bootstrap-креды в Redis под ключ `creds_key` с TTL.

    Сами креды (login/password plaintext) в task-payload не попадают — туда
    едет только `creds_key`. TTL задаётся `PREPARE_CREDS_TTL_SECONDS`; по его
    истечении ключ исчезает сам, что подчищает креды без явного удаления и
    ограничивает окно их жизни.

    Переиспользуем тот же Redis, что и taskiq-broker (`SERVER_WORKER_REDIS_URL`).
    При незаданном URL — `ServiceUnavailableError(WORKER_REDIS_NOT_CONFIGURED)`,
    как и остальной dispatch.
    """
    settings = get_settings()
    if not settings.server_worker_redis_url:
        raise ServiceUnavailableError(
            error_code="WORKER_REDIS_NOT_CONFIGURED",
            message="SERVER_WORKER_REDIS_URL is not set",
        )
    # Envelope-шифрование перед записью в Redis: plaintext (bootstrap_password)
    # больше не оседает в Redis-keyspace в открытом виде. AAD binding'уется
    # к stash-id (хвост ключа), swap-attack ловится InvalidTag на decrypt'е.
    token = encrypt_stash(
        json.dumps(creds),
        aad=aad_for_redis_stash(stash_id_from_key(creds_key)),
    )
    pooled = _creds_redis_client
    if pooled is not None:
        await pooled.set(
            creds_key, token, ex=settings.prepare_creds_ttl_seconds,
        )
        return
    # Fallback: lifespan не поднял пул (unit-тест / standalone-вызов). Чтобы
    # не ломать существующее поведение, делаем разовый клиент и закрываем.
    client = aioredis.from_url(settings.server_worker_redis_url)
    try:
        await client.set(
            creds_key, token, ex=settings.prepare_creds_ttl_seconds,
        )
    finally:
        await client.aclose()


async def delete_dispatch_creds(stash_key: str) -> None:
    """Снять inline-creds из Redis (best-effort).

    Зовётся из `_dispatch_account_on_host` когда `dispatch_task` падает уже
    после `store_dispatch_creds` — иначе plaintext password + private key висят
    в Redis до истечения TTL. Сама операция не должна валить ответ клиенту:
    если Redis недоступен или ключ уже исчез — глушим исключение.
    """
    settings = get_settings()
    if not settings.server_worker_redis_url:
        return
    pooled = _creds_redis_client
    try:
        if pooled is not None:
            await pooled.delete(stash_key)
            return
        client = aioredis.from_url(settings.server_worker_redis_url)
        try:
            await client.delete(stash_key)
        finally:
            await client.aclose()
    except Exception:  # noqa: BLE001
        # Best-effort: TTL подчистит ключ, если DEL не прошёл.
        pass


async def store_dispatch_creds(stash_key: str, creds: dict) -> None:
    """Положить inline-creds provision-таски в Redis под `stash_key` с TTL.

    Сами секреты (`password_plaintext`, `ssh_private_key_plaintext`) в
    task-payload не попадают — туда едет только `creds_stash_key`. Без этого
    plaintext висел бы в `dev_server_worker.tasks.payload` JSONB до retention
    cleanup'а (до 30 дней) и был бы виден любому с read к worker-БД.

    TTL задаётся `dispatch_creds_ttl_seconds`; по его истечении ключ исчезает
    сам. Воркер читает creds одной операцией, потом явно DEL'ит. Если на retry'е
    stash потерян (TTL/Redis-restart), worker'ский inline-stash под task_id
    (`_PROVISION_INLINE_KEY_PREFIX`) даёт второй слой — туда worker
    переписывает creds на первой попытке, см. `tasks/users.py`.

    Переиспользуем тот же Redis, что и taskiq-broker (`SERVER_WORKER_REDIS_URL`).
    При незаданном URL — `ServiceUnavailableError(WORKER_REDIS_NOT_CONFIGURED)`,
    как и остальной dispatch.
    """
    settings = get_settings()
    if not settings.server_worker_redis_url:
        raise ServiceUnavailableError(
            error_code="WORKER_REDIS_NOT_CONFIGURED",
            message="SERVER_WORKER_REDIS_URL is not set",
        )
    # Envelope-шифрование перед записью в Redis: plaintext password +
    # ssh_private_key больше не оседают в Redis-keyspace в открытом виде.
    # AAD binding'уется к stash-id (хвост ключа), swap-attack ловится
    # InvalidTag на decrypt'е воркером.
    token = encrypt_stash(
        json.dumps(creds),
        aad=aad_for_redis_stash(stash_id_from_key(stash_key)),
    )
    pooled = _creds_redis_client
    if pooled is not None:
        await pooled.set(
            stash_key, token, ex=settings.dispatch_creds_ttl_seconds,
        )
        return
    client = aioredis.from_url(settings.server_worker_redis_url)
    try:
        await client.set(
            stash_key, token, ex=settings.dispatch_creds_ttl_seconds,
        )
    finally:
        await client.aclose()


async def store_console_creds(stash_key: str, creds: dict) -> None:
    """Положить креды console-сессии в Redis под `stash_key` с TTL.

    `creds` — `{"login", "password", "ssh_private_key"}` (resolve через
    `server_account.resolve_bootstrap_credentials`). В pub/sub-сообщениях и
    в task-payload'ах едет только ссылка `creds_stash_key`; plaintext пароля
    нигде, кроме шифрованного Redis-stash'а, не оседает.

    Envelope-шифрование симметрично provision-stash'у: AAD биндится к stash-id
    (хвост ключа), swap-attack ловится InvalidTag на decrypt'е воркером. TTL —
    `dispatch_creds_ttl_seconds` (одноразовый short-lived ключ). Незаданный
    `SERVER_WORKER_REDIS_URL` → `ServiceUnavailableError`.
    """
    settings = get_settings()
    if not settings.server_worker_redis_url:
        raise ServiceUnavailableError(
            error_code="WORKER_REDIS_NOT_CONFIGURED",
            message="SERVER_WORKER_REDIS_URL is not set",
        )
    token = encrypt_stash(
        json.dumps(creds),
        aad=aad_for_redis_stash(stash_id_from_key(stash_key)),
    )
    pooled = _creds_redis_client
    if pooled is not None:
        await pooled.set(
            stash_key, token, ex=settings.dispatch_creds_ttl_seconds,
        )
        return
    client = aioredis.from_url(settings.server_worker_redis_url)
    try:
        await client.set(
            stash_key, token, ex=settings.dispatch_creds_ttl_seconds,
        )
    finally:
        await client.aclose()


async def delete_console_creds(stash_key: str) -> None:
    """Снять креды console-сессии из Redis (best-effort).

    Зовётся когда WS-сессию не удалось поднять после `store_console_creds` —
    иначе plaintext пароля висит в Redis до истечения TTL. Ошибки глушим: TTL
    подчистит ключ сам.
    """
    settings = get_settings()
    if not settings.server_worker_redis_url:
        return
    pooled = _creds_redis_client
    try:
        if pooled is not None:
            await pooled.delete(stash_key)
            return
        client = aioredis.from_url(settings.server_worker_redis_url)
        try:
            await client.delete(stash_key)
        finally:
            await client.aclose()
    except Exception:  # noqa: BLE001
        pass


async def _dispatch_task_inner(
    *,
    db: AsyncSession,
    task_kind: str,
    target_server_id: str | None,
    payload: dict,
    created_by: str | None,
    request_id: str | None,
    target_resource_id: str | None,
    idempotency_key: str | None,
    priority: int = TASK_PRIORITY_NORMAL,
) -> tuple[str, bool]:
    """Общее ядро dispatch'а. Возвращает `(task_id, idempotent_hit)`.

    Зовётся из публичных `dispatch_task` / `dispatch_task_with_hit`. Сами
    публичные методы делятся ради явного контракта возврата, ядро их не
    дублирует.

    `db` — server_service-сессия caller'а. В неё пишется outbox-row; commit
    делает caller вместе со своими доменными изменениями. На idempotent-hit
    outbox-INSERT'а нет — предыдущий dispatch уже его записал.
    """
    if idempotency_key is not None:
        existing = await _get_task_by_idempotency_key(idempotency_key)
        if existing is not None:
            _ensure_idempotency_matches(
                existing=existing,
                task_kind=task_kind,
                target_server_id=target_server_id,
            )
            return existing[0], True

    new_id = task_id()
    try:
        await _insert_task_row(
            new_task_id=new_id,
            task_kind=task_kind,
            target_server_id=target_server_id,
            target_resource_id=target_resource_id,
            payload=payload,
            created_by=created_by,
            request_id=request_id,
            idempotency_key=idempotency_key,
            priority=priority,
        )
    except IntegrityError:
        # Race на UNIQUE(idempotency_key): другой процесс успел вставить
        # задачу с этим ключом между нашим SELECT и INSERT. Повторный SELECT
        # должен её увидеть и вернуть существующий id — это идемпотентный
        # путь, outbox-INSERT не делаем (предыдущий dispatch уже его записал).
        # Снова сверяем kind/server, чтобы race не пробил confused-deputy.
        if idempotency_key is not None:
            existing = await _get_task_by_idempotency_key(idempotency_key)
            if existing is not None:
                _ensure_idempotency_matches(
                    existing=existing,
                    task_kind=task_kind,
                    target_server_id=target_server_id,
                )
                return existing[0], True
        raise ConflictError(
            error_code="TASK_IDEMPOTENT_CONFLICT",
            message="Task insert failed and idempotent retry did not resolve",
        ) from None

    # Outbox-INSERT в server_service-БД. Сам payload — тот же, что воркер
    # получит из таблицы tasks (worker читает по `task_id`), но кладём и
    # `task_kind`, чтобы poller знал, в какую taskiq-очередь публиковать
    # без дополнительного SELECT'а из worker-БД.
    #
    # commit не делаем — owner транзакции caller. Это и есть суть outbox'а:
    # если caller'ский commit упадёт, outbox-row не появится, и worker-row
    # станет orphan'ом (никто не опубликует его в Redis). Без orphan'а
    # poller бы дублировал ещё-не-выполненную задачу при retry.
    try:
        await dispatch_outbox_repo.insert(
            db,
            task_id=new_id,
            task_kind=task_kind,
            payload=payload,
            priority=priority,
        )
    except Exception as exc:  # noqa: BLE001
        # Outbox не записался — worker-row уже закоммичен (cross-DB), его не
        # снять без 2PC. Лучшее, что можем — попытка best-effort DELETE'а
        # worker-row, чтобы не плодить orphan'ов; ошибки DELETE'а глотаем,
        # потому что наружу важнее поднять оригинальную причину фейла.
        # Структурный лог нужен SIEM'у/мониторингу для счётчика compensation'ов:
        # если он растёт — outbox-engine у server_service'а ломается часто.
        logger.warning(
            "dispatch_task outbox write failed, compensating worker-row delete",
            extra={
                "event": "dispatch_outbox_failed",
                "task_id": new_id,
                "task_kind": task_kind,
                "target_server_id": target_server_id,
                "exc_type": exc.__class__.__name__,
            },
        )
        await _delete_task_row(new_id)
        raise ServiceUnavailableError(
            error_code="WORKER_UNREACHABLE",
            message=f"Failed to write dispatch_outbox row: {exc.__class__.__name__}",
        ) from exc
    # Worker-row + outbox-row записаны успешно. Caller дальше делает commit
    # своей транзакции; если он упадёт, outbox-row откатится, а worker-row
    # останется orphan'ом без публикации в Redis (poller её не подберёт —
    # безопасно, но требует мониторинга). Сценарий узкий: локальный commit
    # после нескольких удачных async-операций обычно проходит.
    #
    # Ops-метрика: pending_depth +1 на успешный INSERT outbox-row. Decrement
    # делается на стороне poller'а при `mark_dispatched`; in-process у нас
    # счётчик-аппроксимация, точный snapshot можно поднять через
    # `dispatch_outbox_repo.pending_count`.
    metrics.set_dispatch_outbox_pending_depth(
        metrics.get_dispatch_outbox_pending_depth() + 1,
    )
    logger.info(
        "dispatch_task queued (worker-row + outbox)",
        extra={
            "event": "dispatch_task_queued",
            "task_id": new_id,
            "task_kind": task_kind,
            "target_server_id": target_server_id,
            "idempotency_key": idempotency_key,
        },
    )
    return new_id, False


async def dispatch_task(
    *,
    db: AsyncSession,
    task_kind: str,
    target_server_id: str | None,
    payload: dict,
    created_by: str | None,
    request_id: str | None,
    target_resource_id: str | None = None,
    idempotency_key: str | None = None,
    priority: int = TASK_PRIORITY_NORMAL,
) -> str:
    """INSERT task row + INSERT outbox row. Возвращает новый task_id.

    `priority` — приоритет в claim'е воркера (больше = раньше); дефолт
    `TASK_PRIORITY_NORMAL`. Высокоприоритетный фан-аут передаёт
    `TASK_PRIORITY_HIGH`. На idempotent-hit игнорируется: приоритет уже
    зафиксирован при первой постановке.

    Caller'ам, которым нужен флаг idempotent-replay'я (audit-details), —
    использовать `dispatch_task_with_hit`. Разделение на два метода вместо
    параметра `return_hit` сохраняет статическую типизацию: возврат
    однозначно `str`, без union'а.

    Если передан ``idempotency_key`` — сначала ищем существующий task с этим
    ключом (через cross-DB engine). Найден — возвращаем его id без записи
    в outbox (предыдущий dispatch уже там).

    БД `dev_server_worker.tasks` имеет UNIQUE на ``idempotency_key`` —
    при гонке между двумя процессами с одним ключом INSERT упадёт с
    IntegrityError: ловим, делаем повторный SELECT, возвращаем найденный id.
    Если после второй попытки строка всё ещё не найдена — поднимаем
    ConflictError(409, TASK_IDEMPOTENT_CONFLICT).

    Outbox-INSERT идёт в `db` (server_service-сессия caller'а) и НЕ
    коммитится здесь — caller commit'нет вместе со своими доменными
    изменениями. Это даёт атомарность outbox-write+domain-write в рамках
    server_service-БД. Worker-row в `dev_server_worker.tasks` коммитится
    eagerly (cross-DB, atomic 2PC мы не используем); если caller'ский
    commit упадёт, останется orphan-task без outbox, и poller его не
    опубликует — безопасно (задача не запустится), но требует мониторинга.

    Поднимается `ServiceUnavailableError`:

    * исходный error_code (`WORKER_DB_NOT_CONFIGURED`) — пробрасывается;
    * фейл outbox-INSERT'а → `WORKER_UNREACHABLE` (с chained __cause__),
      worker-row best-effort удаляется.
    """
    new_id, _hit = await _dispatch_task_inner(
        db=db,
        task_kind=task_kind,
        target_server_id=target_server_id,
        payload=payload,
        created_by=created_by,
        request_id=request_id,
        target_resource_id=target_resource_id,
        idempotency_key=idempotency_key,
        priority=priority,
    )
    return new_id


async def dispatch_task_with_hit(
    *,
    db: AsyncSession,
    task_kind: str,
    target_server_id: str | None,
    payload: dict,
    created_by: str | None,
    request_id: str | None,
    target_resource_id: str | None = None,
    idempotency_key: str | None = None,
    priority: int = TASK_PRIORITY_NORMAL,
) -> tuple[str, bool]:
    """Как `dispatch_task`, но возвращает `(task_id, idempotent_hit)`.

    `idempotent_hit=True`, когда `idempotency_key` совпал с уже существующей
    строкой и новый outbox-row не писался. Caller (`_dispatch_power` /
    `server_prepare_dispatch`) кладёт флаг в audit details, чтобы SIEM
    отличал «новая task» от «idempotent replay» — иначе оба сценария
    неразличимы и нельзя посчитать долю реальных повторов.
    """
    return await _dispatch_task_inner(
        db=db,
        task_kind=task_kind,
        target_server_id=target_server_id,
        payload=payload,
        created_by=created_by,
        request_id=request_id,
        target_resource_id=target_resource_id,
        idempotency_key=idempotency_key,
        priority=priority,
    )

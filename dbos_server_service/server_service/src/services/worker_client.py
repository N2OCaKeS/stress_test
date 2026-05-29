"""Мост к server_worker.

server_service делает две вещи, чтобы передать работу worker'у:

  1. INSERT в ``dev_server_worker.tasks`` через отдельный async-engine
     (cross-DB на том же Postgres-кластере). Эта строка — персистентный
     контракт: worker мутирует ``status`` / ``started_at`` /
     ``completed_at`` именно на ней.
  2. Publish ``task_id`` в Redis через taskiq ListQueueBroker — используя
     pre-registered task-name стабы, единственная задача которых —
     включить ``.kiq()``. Настоящий handler с тем же именем живёт в
     server_worker.

**Zombie-task защита.** Шаги 1 и 2 не атомарны — INSERT коммитится в
`dev_server_worker.tasks` ДО `broker.kiq`. Если Redis недоступен и
`broker.startup` или `stub.kiq` падают, строка `queued` остаётся в БД,
worker её не подберёт (zombie task).

Минимальный фикс без миграций: исключение → каскадно DELETE'им свежую
строку и поднимаем `ServiceUnavailableError("WORKER_UNREACHABLE")`. Клиент
получит 503 и может retry. При flaky Redis часть task'ов теряется, но
клиент знает, что dispatch не удался, и в БД zombie-строк не остаётся.
Симметрично с `WORKER_DB_NOT_CONFIGURED` / `WORKER_REDIS_NOT_CONFIGURED` /
`UNKNOWN_TASK_KIND` (см. `_dispatch_power` в `endpoints/ipmi.py`).

TODO: outbox-pattern (отдельная колонка `enqueued_at_redis` + фоновый
poller в worker'е) — нужна миграция и изменения в worker'е.
"""

import asyncio
import json

import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from taskiq_redis import ListQueueBroker

from src.core.config import get_settings
from src.core.exceptions import ConflictError, ServiceUnavailableError
from src.utils.ids import task_id

# Префикс Redis-ключа для одноразовых bootstrap-кред prepare'а. Креды лежат
# под `dbos:prepare_creds:<task_id>` с TTL — в task-payload едет только ссылка
# на ключ, plaintext в персистентную worker-БД не попадает.
PREPARE_CREDS_KEY_PREFIX = "dbos:prepare_creds:"


def prepare_creds_key(task_id_value: str) -> str:
    """Redis-ключ для bootstrap-кред конкретной prepare-задачи."""
    return f"{PREPARE_CREDS_KEY_PREFIX}{task_id_value}"


# Module-level state. При `uvicorn --workers >1` каждый воркер — отдельный
# процесс с собственным event loop и своим module-level state (включая lock
# и `_broker_started`); taskiq устроен так, что каждый воркер имеет свой
# broker — это by design.
_worker_engine = None
_worker_session_factory = None
_worker_broker: ListQueueBroker | None = None
_task_stubs: dict = {}
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

# Pooled aioredis-клиент для bootstrap-кред prepare'а. До этого
# `store_prepare_creds` дёргал `aioredis.from_url(...)` на каждый вызов —
# burst /prepare исчерпывал FD'ы и connection-budget Redis'а. Поднимается
# из lifespan в `src/main.py` (см. там же `aclose`). Если None — fallback
# на per-call client (для unit-тестов вне lifespan).
_prepare_redis_client: aioredis.Redis | None = None


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
        )
        _worker_session_factory = async_sessionmaker(
            bind=_worker_engine, expire_on_commit=False
        )
    return _worker_session_factory


def _build_broker() -> ListQueueBroker:
    """Создать broker и зарегистрировать task-name стабы (lazy, singleton).

    Стабы существуют только чтобы можно было вызвать ``.kiq()`` из этого
    сервиса — настоящие handler'ы с теми же именами живут в server_worker.

    **Dispatch map (task_kind → endpoint).**

    Все стабы из `_task_stubs` сейчас активно dispatch'атся из endpoints;
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
      (worker сейчас raise'ит NotImplementedError до запроса в iDRAC,
      см. SAFETY GUARD в `server_worker/src/tasks/passwords.py`).
    * ``server.prepare`` — `endpoints/worker_dispatch.py::server_prepare`
      (bootstrap-креды едут через Redis по `bootstrap_creds_key`).
    """
    global _worker_broker, _task_stubs
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

    _task_stubs = {
        "power.on": _power_on,
        "power.off": _power_off,
        "power.reboot": _power_reboot,
        "power.status": _power_status,
        "inventory.sync": _inventory_sync,
        "account.rotate_password": _account_rotate,
        "ipmi.rotate_password": _ipmi_rotate,
        "installed_packages.list": _installed_packages_list,
        "users.inventory": _users_inventory,
        "account.provision": _account_provision,
        "account.update_on_host": _account_update_on_host,
        "account.deprovision": _account_deprovision,
        "server.prepare": _server_prepare,
    }
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
    global _worker_broker, _broker_started
    broker = _worker_broker
    if broker is not None and _broker_started:
        try:
            await broker.shutdown()
        except Exception:  # noqa: BLE001
            pass
    _worker_broker = None
    _broker_started = False


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


async def _get_task_id_by_idempotency_key(key: str) -> str | None:
    """SELECT существующего tasks.id по idempotency_key. None если не нашли.

    Используется cross-DB worker-engine — `tasks` живёт в dev_server_worker.
    """
    session_factory = _engine_factory()
    async with session_factory() as session:
        row = (
            await session.execute(
                text("SELECT id FROM tasks WHERE idempotency_key = :key"),
                {"key": key},
            )
        ).first()
        return row[0] if row else None


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
) -> None:
    """Сырая INSERT-операция в `dev_server_worker.tasks`. Параметры через bound."""
    session_factory = _engine_factory()
    async with session_factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO tasks (
                    id, task_kind, target_server_id, target_resource_id,
                    payload, status, attempt, max_attempts,
                    created_by, request_id, idempotency_key
                ) VALUES (
                    :id, :task_kind, :target_server_id, :target_resource_id,
                    CAST(:payload AS jsonb), 'queued', 0, 3,
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
                "created_by": created_by,
                "request_id": request_id,
                "idempotency_key": idempotency_key,
            },
        )
        await session.commit()


async def _delete_task_row(task_id_to_delete: str) -> None:
    """Best-effort DELETE для отката zombie-row после неудачной публикации в Redis.

    Используется только из `dispatch_task` при сбое `broker.startup` /
    `stub.kiq` — INSERT уже закоммичен в `dev_server_worker.tasks`, и без
    отката строка останется `queued` навсегда (worker её не подберёт,
    потому что в Redis-очереди публикация не произошла).

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
    except Exception:  # noqa: BLE001
        # best-effort rollback — оригинальная ошибка важнее
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
    pooled = _prepare_redis_client
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
    pooled = _prepare_redis_client
    if pooled is not None:
        await pooled.set(
            creds_key, json.dumps(creds), ex=settings.prepare_creds_ttl_seconds,
        )
        return
    # Fallback: lifespan не поднял пул (unit-тест / standalone-вызов). Чтобы
    # не ломать существующее поведение, делаем разовый клиент и закрываем.
    client = aioredis.from_url(settings.server_worker_redis_url)
    try:
        await client.set(
            creds_key, json.dumps(creds), ex=settings.prepare_creds_ttl_seconds,
        )
    finally:
        await client.aclose()


async def dispatch_task(
    *,
    task_kind: str,
    target_server_id: str | None,
    payload: dict,
    created_by: str | None,
    request_id: str | None,
    target_resource_id: str | None = None,
    idempotency_key: str | None = None,
) -> str:
    """INSERT task row + kick worker'а. Возвращает новый task_id.

    Если передан ``idempotency_key`` — сначала ищем существующий task с этим
    ключом (через cross-DB engine). Найден — возвращаем его id без повторной
    публикации в брокер (идемпотентный путь, защита от дубль-кликов клиента
    и retries).

    БД `dev_server_worker.tasks` имеет UNIQUE на ``idempotency_key`` —
    при гонке между двумя процессами с одним ключом INSERT упадёт с
    IntegrityError: ловим, делаем повторный SELECT, возвращаем найденный id.
    Если после второй попытки строка всё ещё не найдена — поднимаем
    ConflictError(409, TASK_IDEMPOTENT_CONFLICT) как сигнал «что-то пошло не
    так на стороне worker-БД».

    **Zombie-task protection:** если `broker.startup` или `stub.kiq` падают
    после INSERT'а, `_delete_task_row(new_id)` откатывает строку. Иначе она
    лежит `queued` навсегда, worker её всё равно не подбирает (см. module
    docstring).
    Поднимается `ServiceUnavailableError`:

    * исходный error_code (`WORKER_DB_NOT_CONFIGURED` / `WORKER_REDIS_NOT_CONFIGURED`
      / `UNKNOWN_TASK_KIND`) — пробрасывается без оборачивания;
    * любая другая ошибка публикации → `WORKER_UNREACHABLE` (с chained __cause__).

    Клиент видит 503 envelope с конкретным error_code, может retry —
    например, с тем же `Idempotency-Key` (после rollback'а lookup вернёт None,
    dispatch создаст новую попытку).
    """
    if idempotency_key is not None:
        existing_id = await _get_task_id_by_idempotency_key(idempotency_key)
        if existing_id is not None:
            return existing_id

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
        )
    except IntegrityError:
        # Race на UNIQUE(idempotency_key): другой процесс успел вставить
        # задачу с этим ключом между нашим SELECT и INSERT. Повторный SELECT
        # должен её увидеть и вернуть существующий id — это идемпотентный
        # путь, никакого нового kick'а worker'у.
        if idempotency_key is not None:
            existing_id = await _get_task_id_by_idempotency_key(idempotency_key)
            if existing_id is not None:
                return existing_id
        raise ConflictError(
            error_code="TASK_IDEMPOTENT_CONFLICT",
            message="Task insert failed and idempotent retry did not resolve",
        )

    # ── Каскадный rollback на Redis-failure (zombie-task fix) ─────────────
    # `_insert_task_row` закоммитил row в `dev_server_worker.tasks`. Если
    # ниже что-то упадёт (`broker.startup` отвалился на Redis-connect,
    # `stub.kiq` не сделал RPUSH, или task_kind не зарегистрирован) —
    # DELETE'им строку и поднимаем `ServiceUnavailableError("WORKER_UNREACHABLE")`.
    # Без этого row лежит `queued` навсегда (zombie task).
    #
    # `ServiceUnavailableError` ловится в `endpoints/ipmi.py::_dispatch_power`
    # и эмитит `audit_service.emit(reason="worker_unreachable")` — клиент
    # получит 503 и сможет retry. `ServiceUnavailableError` из вложенных
    # вызовов (`_ensure_broker_started` поднимает `WORKER_REDIS_NOT_CONFIGURED`
    # при отсутствии env, `_engine_factory` — `WORKER_DB_NOT_CONFIGURED`,
    # явный raise ниже — `UNKNOWN_TASK_KIND`) проходит через первый except —
    # row откатан, исключение проброшено с оригинальным error_code, без
    # маскирования универсальным `WORKER_UNREACHABLE` поверх точного диагноза.
    try:
        await _ensure_broker_started()
        stub = _task_stubs.get(task_kind)
        if stub is None:
            raise ServiceUnavailableError(
                error_code="UNKNOWN_TASK_KIND",
                message=f"No taskiq stub registered for task kind '{task_kind}'",
            )
        await stub.kiq(new_id)
    except ServiceUnavailableError:
        # Подтипы (`WORKER_DB_NOT_CONFIGURED` / `WORKER_REDIS_NOT_CONFIGURED`
        # / `UNKNOWN_TASK_KIND`) уже несут точный error_code — откатываем row
        # и пробрасываем исходное исключение без оборачивания.
        await _delete_task_row(new_id)
        raise
    except Exception as exc:  # noqa: BLE001
        # Любая другая ошибка публикации (Redis-connect refused, RESP parse,
        # network timeout, taskiq internal error) — это эффективно
        # `WORKER_UNREACHABLE` с точки зрения клиента. Откатываем row и
        # поднимаем `ServiceUnavailableError`, чтобы клиент получил 503
        # (через app_exception_handler) и мог retry. Без оборачивания
        # клиенту улетит 500 — менее информативно и нарушает контракт
        # «инфраструктурные сбои = 503».
        await _delete_task_row(new_id)
        raise ServiceUnavailableError(
            error_code="WORKER_UNREACHABLE",
            message=f"Failed to publish task to worker broker: {exc.__class__.__name__}",
        ) from exc
    return new_id

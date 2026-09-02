"""Cancel-эндпоинт worker-task'и.

`POST /api/server/v1/tasks/{task_id}/cancel` — единственная точка
управления task-row'ой со стороны server_service. Row живёт в
`dev_server_worker.tasks`; server_service ходит туда через cross-DB engine
из `worker_client` (тот же, что dispatch_task инсёртит при постановке).

Поведение:

* permission: `(task, cancel)` — отдельная пара из матрицы. По-дефолту
  выдаётся системной роли `admin` сидингом миграции; кастомные роли без
  явного гранта получают 403.
* visibility: если у task есть `target_server_id`, проверяем dept-isolation
  на этом сервере (cross-dept → 404 ``TASK_NOT_FOUND``, как обычно).
  Системные task'и (``task_kind`` ∈ ``_SYSTEM_TASK_KINDS`` — это
  scheduler-registered autostart task'и: `system.heartbeat`,
  `worker.heartbeat`, `tasks.sweep_orphaned`, `tasks.recover_scheduled_retries`,
  `tasks.cleanup_completed_old`, `worker.cleanup_stale_heartbeats`,
  `audit_outbox.cleanup_published_old`, `secrets.reencrypt_lazy`) требуют
  платформенной роли ``account_admin``: dept-admin с (task, cancel) может
  ходить в свои серверные task'и, но не должен ломать кластерный worker
  health отменой heartbeat'а или housekeeping'а. Не account_admin → 403
  ``denied`` с ``reason=system_task_admin_required``. Whitelist по kind —
  явный контракт: новый системный kind должен явно попасть в множество,
  иначе работает обычный dept-isolation путь.
* cancellable-precondition: pending/running. Terminal (succeeded/failed/
  cancelled) → 409 ``TASK_NOT_CANCELLABLE``.
* применяется немедленно: queued — сразу cancelled, running — worker
  завершает текущий stage и видит status=cancelled при попытке terminal
  mark_succeeded/failed (CAS отбрасывает финализацию, финальный статус
  остаётся cancelled). Re-kick подавляется CAS'ом на mark_running в
  `_runner`. Force-kill процесса нет.
* vm.create cleanup: отмена `queued` (ещё не стартовавшего) `vm.create`
  снимает placeholder-строку ВМ (`target_resource_id`) — задача хаб не
  трогала, домена там нет. `running`-cancel строку ВМ не трогает (частично
  собранная ВМ — отдельная тема). details.vm_cleaned_up фиксирует исход.
* audit: `task.cancelled` (WARNING по дефолту) с details.task_kind /
  target_server_id / previous_status (+ vm_cleaned_up для queued vm.create).
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, Path, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action, EntityType, PlatformRole, VmTaskKind
from src.core.exceptions import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
)
from src.dependencies.auth import CurrentUserIdentity
from src.dependencies.db import get_db
from src.schemas.task import TaskCancelRequest, TaskCancelResponse, TaskRead
from src.services import audit_service, permissions, worker_client
from src.services import server as server_svc
from src.services import tasks as tasks_svc
from src.services.audit_helpers import emit_denied_on_authz_error

logger = logging.getLogger(__name__)

# Whitelist зарезервирован под scheduler-task'и, которые в будущем будут
# проходить через `worker_client.dispatch_task` (а значит через
# `cancel_task_endpoint`). На сегодня scheduler-task'и из server_worker
# (heartbeat/sweep/recover/cleanup/reencrypt) дисптачит сам worker напрямую
# в свою БД, минуя server_service: у их row'а нет `target_server_id` и
# нет meta-маршрута через dispatch_task, поэтому endpoint их попросту не
# видит — `_fetch_task_status_and_meta` возвращает None, и cancel
# отбивается 404 на :110-118 ещё до проверки whitelist'а. Реальный
# системный kind через whitelist пройдёт только тогда, когда планировщик
# сменит транспорт на dispatch_task.
#
# Defence-in-depth по двум фронтам:
#   1. `account_admin` платформенный admin отрезается раньше нас в
#      `platform_admin_guard` middleware (см. `src/middleware/
#      platform_admin_guard.py` BLOCKED_PLATFORM_ROLES). До нашей проверки
#      `identity.platform_role != ACCOUNT_ADMIN` доходит ТОЛЬКО dept-user
#      (department_admin / service-role'овый). Для него условие всегда
#      True → 403, и dept-admin с (task, cancel) не убивает кластерный
#      worker health. Ветка «account_admin прошёл whitelist» в текущей
#      сборке недостижима — это явная контрактная подпорка на случай
#      сужения BLOCKED_PLATFORM_ROLES или замены middleware: тогда
#      account_admin начнёт доходить сюда, и whitelist выдержит.
#   2. Даже если кто-то на тестовом стенде вручную поднимет task-row с
#      одним из этих kind'ов через dispatch_task, dept-admin не сможет
#      её отменить.
#
# Источник истины по самим kind-именам — server_worker autostart tasks,
# см. `server_worker/src/main.py` (`@broker.task(...)` с `schedule=[...]`).
_SYSTEM_TASK_KINDS = frozenset({
    "system.heartbeat",
    "worker.heartbeat",
    "tasks.sweep_orphaned",
    "tasks.recover_scheduled_retries",
    "tasks.cleanup_completed_old",
    "worker.cleanup_stale_heartbeats",
    "audit_outbox.cleanup_published_old",
    "secrets.reencrypt_lazy",
})

router = APIRouter(prefix="/tasks")


@router.get(
    "",
    response_model=list[TaskRead],
    summary="Список worker-task'ов, видимых вызывающему",
    description=(
        "Возвращает страницу task'ов из `dev_server_worker.tasks`, "
        "отсортированных по времени постановки (`enqueued_at DESC`). Тело — "
        "`list[TaskRead]`, общее число под фильтром — в заголовке "
        "`X-Total-Count`.\n\n"
        "Фильтры: `status` (queued/running/succeeded/failed/cancelled — "
        "`failed` = DLQ-вьюха в UI), `kind` (task_kind), `server_id`, "
        "`created_by` (user_id инициатора — накладывается поверх role-scope, "
        "видимость не расширяет: непривилегированный caller всё равно видит только свои). "
        "Пагинация: `limit` (1..200, default 50) + `offset`.\n\n"
        "Доступ: `(task, view)`. Caller видит только задачи серверов своего "
        "отдела; носитель кастомной роли без `admin` (и не department_admin) "
        "видит только свои задачи (`created_by`). Инфра-задачи без сервера "
        "видны только service-роли `admin`. Platform-админам "
        "(`account_admin`/`loging_admin`) вход запрещён middleware'ом — 403 "
        "PLATFORM_ADMIN_BUSINESS_DATA_DENIED."
    ),
    responses={
        200: {"description": "Страница task'ов; `X-Total-Count` в заголовке."},
        403: {"description": "Нет роли с `view` на task, либо platform-админ заблокирован."},
    },
)
async def list_tasks_endpoint(
    identity: CurrentUserIdentity,
    response: Response,
    db: AsyncSession = Depends(get_db),
    status: str | None = Query(default=None, description="Фильтр по статусу задачи."),
    kind: str | None = Query(default=None, description="Фильтр по task_kind."),
    server_id: str | None = Query(default=None, description="Фильтр по target_server_id."),
    created_by: str | None = Query(
        default=None,
        description=(
            "Фильтр по инициатору (user_id). Накладывается поверх role-scope: "
            "непривилегированный caller видит только свои, привилегированный "
            "caller с этим фильтром сужает выдачу до конкретного инициатора."
        ),
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
) -> list[TaskRead]:
    with emit_denied_on_authz_error(
        "task.view",
        target_type="task",
        identity=identity,
    ):
        items, total = await tasks_svc.list_tasks(
            db, identity,
            status=status, kind=kind, server_id=server_id, created_by=created_by,
            limit=limit, offset=offset,
        )
    response.headers["X-Total-Count"] = str(total)
    return items


@router.get(
    "/{task_id}",
    response_model=TaskRead,
    summary="Деталь одной worker-task'и (полный result/last_error)",
    description=(
        "Возвращает `TaskRead` с полным `result` и `last_error`. Доступ — "
        "`(task, view)` + dept-visibility (чужой отдел маскируется под 404). "
        "Носитель кастомной роли без `admin` видит только свои задачи — чужая "
        "задача того же отдела маскируется под 404. "
        "Инфра-задача без сервера видна только service-роли `admin`."
    ),
    responses={
        403: {"description": "Нет роли с `view` на task, либо platform-админ заблокирован."},
        404: {"description": "Task не найдена (нет row либо cross-dept / невидимый сервер)."},
    },
)
async def get_task_endpoint(
    identity: CurrentUserIdentity,
    task_id: str = Path(..., min_length=1, max_length=64),
    db: AsyncSession = Depends(get_db),
) -> TaskRead:
    with emit_denied_on_authz_error(
        "task.view",
        target_id=task_id,
        target_type="task",
        identity=identity,
    ):
        return await tasks_svc.get_task(db, identity, task_id)


@router.post(
    "/{task_id}/cancel",
    response_model=TaskCancelResponse,
    summary="Отменить pending/running worker-task'у (опц. с reason)",
    description=(
        "Помечает row в `dev_server_worker.tasks` как `cancelled`. "
        "Pending-task пропускается перед запуском через CAS на mark_running. "
        "Running-task graceful: текущий stage доживает, следующий не стартует. "
        "Force-kill нет. Body опционально несёт `{reason: str}` — фиксируется "
        "в `cancel_reason` колонке и в audit-event'е `task.cancelled`. "
        "Доступ: `(task, cancel)` — по-дефолту только `admin`."
    ),
    responses={
        403: {"description": "Нет роли с `cancel` на task либо системная task требует account_admin."},
        404: {"description": "Task не найдена (нет row либо cross-dept по target_server_id)."},
        409: {"description": "Task в терминальном статусе (succeeded/failed/cancelled) и не подлежит отмене."},
    },
)
async def cancel_task_endpoint(
    identity: CurrentUserIdentity,
    task_id: str = Path(..., min_length=1, max_length=64),
    body: TaskCancelRequest | None = Body(default=None),
    db: AsyncSession = Depends(get_db),
) -> TaskCancelResponse:
    audit_action = "task.cancelled"
    reason = body.reason if body is not None else None

    # 1. Role-check (permission → visibility, как в остальных endpoint'ах).
    with emit_denied_on_authz_error(
        audit_action,
        target_id=task_id,
        target_type="task",
        identity=identity,
    ):
        await permissions.require_action(db, identity, EntityType.TASK, Action.CANCEL)

    # 2. Подгрузить статус и метаданные target'а из worker-БД.
    try:
        meta = await worker_client._fetch_task_status_and_meta(task_id)
    except ServiceUnavailableError:
        # WORKER_DB_NOT_CONFIGURED и подобные — не отменяем, отдаём 503
        # без денайд-аудита (это не отказ доступа caller'у, это infra).
        raise
    if meta is None:
        # caller прошёл permission, row task'и просто отсутствует — visibility-404.
        # `failure`/`allowed=True` симметрично emit'ам на 204 и worker-dispatch'у.
        audit_service.emit(
            audit_action, target_id=task_id, target_type="task",
            status="failure", allowed=True,
            details={"reason": "task_not_found"},
        )
        raise NotFoundError(
            error_code="TASK_NOT_FOUND",
            message="Task not found",
        )

    target_server_id = meta.get("target_server_id")
    task_kind = meta.get("task_kind")
    target_resource_id = meta.get("target_resource_id")

    # 3. Системные task'и (kind ∈ _SYSTEM_TASK_KINDS — scheduler-registered
    #    autostart-задачи: heartbeat/sweep/recover/cleanup/reencrypt, см.
    #    `server_worker/src/main.py`) отменяются только платформенным
    #    account_admin'ом. Сейчас account_admin отрезан от server_service
    #    `platform_admin_guard`-middleware'ом раньше нас, поэтому в проде
    #    эта ветка стабильно отдаёт 403 любому dept-admin'у с (task, cancel)
    #    — нужная семантика, иначе он может затушить кластерный worker
    #    health для всех отделов. Защита остаётся как явный контракт на
    #    случай послабления middleware (см. развёрнутый комментарий у
    #    `_SYSTEM_TASK_KINDS`). Критерий — явный whitelist по `task_kind`,
    #    не неявный `target_server_id IS NULL AND created_by IS NULL`:
    #    новые системные kind'ы вписываются в whitelist, а пара NULL/NULL
    #    у user-task (например, тестовый dispatch без актора) не
    #    превращается в случайный 403.
    is_system_task = task_kind in _SYSTEM_TASK_KINDS
    if is_system_task and identity.platform_role != PlatformRole.ACCOUNT_ADMIN:
        audit_service.emit(
            audit_action, target_id=task_id, target_type="task",
            status="denied", allowed=False,
            details={
                "reason": "system_task_admin_required",
                "task_kind": task_kind,
                "target_server_id": target_server_id,
            },
        )
        raise AuthorizationError(
            error_code="SYSTEM_TASK_ADMIN_REQUIRED",
            message="System tasks can only be cancelled by account_admin",
        )

    # 4. Dept-isolation по target_server_id, если есть. Cross-dept маскируем
    #    под 404 — стандартный enumeration-guard.
    if target_server_id is not None:
        try:
            await server_svc.load_visible_server(db, identity, target_server_id)
        except (NotFoundError, AuthorizationError):
            # target_server невидим (cross-dept / removed) → 404, маскируется под
            # task-not-found чтобы не светить факт чужого сервера. Visibility-404 →
            # `failure`/`allowed=True`, симметрично с 132 и блоком на 204.
            audit_service.emit(
                audit_action, target_id=task_id, target_type="task",
                status="failure", allowed=True,
                details={
                    "reason": "task_not_found_or_cross_dept",
                    "target_server_id": target_server_id,
                    "task_kind": task_kind,
                },
            )
            raise NotFoundError(
                error_code="TASK_NOT_FOUND",
                message="Task not found",
            ) from None
    # Task без target_server_id и не в whitelist'е — фактически
    # глобальная row без dept-владельца. Сегодня штатно таких нет
    # (см. test_user_task_with_both_nulls_no_longer_blocked), их
    # генерируют только sandbox-dispatch'и без актора. Dept-admin с
    # (task, cancel) может отменять — это документировано как
    # сознательное послабление до появления настоящего владельца
    # row. Если в будущем добавится новый kind, который должен быть
    # системным, его пропишут в `_SYSTEM_TASK_KINDS`.

    # 5. Atomic UPDATE через cross-DB engine.
    result = await worker_client.cancel_task(
        task_id_value=task_id,
        cancelled_by=identity.user_id,
        cancel_reason=reason,
    )

    if not result["found"]:
        # Гонка: row исчез между _fetch_task_status_and_meta и cancel_task.
        # Маловероятно (cleanup task'а удаляет только terminal succeeded/
        # failed), но проще честно вернуть 404 чем притворяться.
        # `failure`, не `denied`: caller уже прошёл permission/visibility,
        # отказ — из-за исчезновения row, а не из-за прав.
        audit_service.emit(
            audit_action, target_id=task_id, target_type="task",
            status="failure", allowed=True,
            details={"reason": "task_not_found"},
        )
        raise NotFoundError(
            error_code="TASK_NOT_FOUND",
            message="Task not found",
        )

    previous_status = result.get("previous_status")
    if not result["cancelled"]:
        audit_service.emit(
            audit_action, target_id=task_id, target_type="task",
            status="failure", allowed=True,
            details={
                "reason": "not_cancellable",
                "previous_status": previous_status,
                "task_kind": task_kind,
                "target_server_id": target_server_id,
            },
        )
        raise ConflictError(
            error_code="TASK_NOT_CANCELLABLE",
            message=(
                f"Task is in terminal status '{previous_status}' and cannot be cancelled"
            ),
            details={"previous_status": previous_status},
        )

    # Отмена ещё не стартовавшего (`queued`) `vm.create` оставляла бы строку ВМ
    # осиротевшей в `busy_state=creating` навсегда. Задача воркером не
    # запускалась — на хабе ничего нет, строка ВМ — placeholder, поэтому просто
    # снимаем её (best-effort; см. `cleanup_cancelled_vm_create`). Running-cancel
    # тут сознательно не трогаем: там ВМ может быть частично собрана на хабе.
    details: dict = {
        "task_id": task_id,
        "previous_status": previous_status,
        "task_kind": task_kind,
        "target_server_id": target_server_id,
        "cancel_reason": reason,
    }
    if previous_status == "queued" and task_kind == VmTaskKind.VM_CREATE.value:
        vm_cleaned_up = await tasks_svc.cleanup_cancelled_vm_create(
            db, vm_id=target_resource_id,
        )
        details["vm_cleaned_up"] = vm_cleaned_up

    cancelled_at = datetime.now(timezone.utc)
    audit_service.emit(
        audit_action, target_id=task_id, target_type="task",
        status="success", allowed=True,
        details=details,
    )
    return TaskCancelResponse(
        task_id=task_id,
        status="cancelled",
        previous_status=previous_status,
        cancelled_at=cancelled_at,
        cancelled_by=identity.user_id,
        cancel_reason=reason,
    )

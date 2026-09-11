"""Эндпоинты матрицы entity-permissions — список / каталог / grant / revoke.

**Department scope.** PUT/DELETE принимают опциональный
`target_department_id` (body для PUT, query-param для DELETE).
Department-bound caller обязан либо опустить поле, либо передать свой
`department_id` — несовпадение → 403 `DEPARTMENT_ISOLATION`.

**account_admin — мета-админ матрицы.** Платформенный `account_admin`
управляет правами любого отдела (просмотр + grant/revoke), но бизнес-данными
testing_service (тесты/стенды/очередь) не оперирует вообще — обычные
эндпоинты сервиса отбивают его 403 `SERVICE_ACCESS_DENIED` ещё до того, как
он увидит запрос (у него нет ни отдела, ни testing_service в
allowed_services). Сюда он попадает через `PermissionMatrixIdentity`, которая
для него снимает этот гейт; `permission_service` снимает для него ролевую
проверку и dept-isolation.

**department_admin** тоже управляет матрицей, но только своего отдела —
ролевая проверка снимается, dept-isolation остаётся.
"""

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import PermissionMatrixIdentity
from src.dependencies.db import get_db
from src.schemas.common import OkResponse
from src.schemas.permission import (
    CatalogEntity,
    PermissionDescribedResponse,
    PermissionGrant,
    PermissionListResponse,
    PermissionResponse,
)
from src.services import permission_service

router = APIRouter(prefix="/permissions")


@router.get(
    "",
    response_model=PermissionListResponse,
    summary="Список записей entity_permissions",
    description=(
        "Возвращает матрицу grants в envelope'е `{items, total, described}`. "
        "Опциональный `role=<role>` сужает выдачу до грантов одной роли; "
        "`describe=true` обогащает каждую строку описаниями сущности/действия "
        "и флагом `sensitive` из каталога (в этом случае `described=true` в "
        "ответе). Доступ: `(permission, *, view)` (department_admin своего "
        "отдела или сервисная роль `admin` своего отдела). Scope: "
        "department-bound caller получает system-wide + свой dept; "
        "`account_admin` видит всю матрицу. **Pagination**: non-paginated by "
        "design — матрица закрыта каталогом (`ENTITY_ACTIONS` × ролей × "
        "департаментов), ожидаемый размер < 200 строк."
    ),
    responses={403: {"description": "Нет роли с `view` на permission."}},
)
async def list_permissions(
    identity: PermissionMatrixIdentity,
    role: str | None = Query(
        default=None,
        max_length=64,
        description="Сузить выдачу до грантов одной роли. None → вся матрица.",
    ),
    describe: bool = Query(
        default=False,
        description="Обогатить каждую строку описаниями из каталога.",
    ),
    db: AsyncSession = Depends(get_db),
) -> PermissionListResponse:
    """
    Что делает: SELECT по таблице `entity_permissions` (опц. WHERE role=:role),
    упорядоченный по `(entity_type, role, action, system_first)`. Ответ —
    envelope `{items, total, described}`.

    Связано: `services/permission_service.py::list_all`,
    `repositories/entity_permission.py::list_all` / `list_for_role`.
    """
    rows = await permission_service.list_all(db, identity, role=role)
    if describe:
        items: list[PermissionResponse | PermissionDescribedResponse] = [
            PermissionDescribedResponse.model_validate(
                {**PermissionResponse.model_validate(r).model_dump(), **permission_service.describe_row(r)}
            )
            for r in rows
        ]
    else:
        items = [PermissionResponse.model_validate(r) for r in rows]
    return PermissionListResponse(items=items, total=len(items), described=describe)


@router.get(
    "/catalog",
    response_model=list[CatalogEntity],
    summary="Каталог сущностей и действий с описаниями",
    description=(
        "Read-only справочник: все сущности матрицы и их действия с "
        "человеческими описаниями, флагами `sensitive` (аудит CRITICAL) и "
        "`worker_only` (служебный callback воркера — у testing_service сейчас "
        "пусто, callback'и закрыты `require_internal_caller`, не матрицей). "
        "Состав берётся из `ENTITY_ACTIONS`. Доступ: `(permission, *, view)` — "
        "как у GET /permissions."
    ),
    responses={403: {"description": "Нет роли с `view` на permission."}},
)
async def permissions_catalog(
    identity: PermissionMatrixIdentity,
    db: AsyncSession = Depends(get_db),
) -> list[CatalogEntity]:
    """
    Что делает: собирает каталог из `core/constants.ENTITY_ACTIONS` +
    `core/permission_catalog` (описания, sensitive/worker_only флаги).
    Матрицу в БД не читает — данные статичны, гейт нужен лишь чтобы не
    светить состав гостям.
    """
    entities = await permission_service.get_catalog(db, identity)
    return [CatalogEntity.model_validate(e) for e in entities]


@router.get(
    "/{entity_type}",
    response_model=PermissionListResponse,
    summary="Grants для одного entity_type",
    description=(
        "То же, что GET /permissions, но отфильтровано по `entity_type` "
        "(global_variable/test_stand/permission/...). Ответ — тот же envelope "
        "`{items, total, described}`; describe-обогащения здесь нет, поэтому "
        "`described` всегда `false`. Неизвестный entity_type → 422 "
        "UNKNOWN_ENTITY_TYPE."
    ),
    responses={
        403: {"description": "Нет роли с `view` на permission."},
        422: {"description": "UNKNOWN_ENTITY_TYPE — неизвестный entity_type."},
    },
)
async def list_permissions_for_entity(
    entity_type: str,
    identity: PermissionMatrixIdentity,
    db: AsyncSession = Depends(get_db),
) -> PermissionListResponse:
    """
    Что делает: SELECT с WHERE `entity_type=:type`. Ответ — envelope
    `{items, total, described=false}`.

    Возможные ошибки: 403 PERMISSION_DENIED, 422 UNKNOWN_ENTITY_TYPE.
    """
    rows = await permission_service.list_for_entity(db, identity, entity_type)
    items: list[PermissionResponse | PermissionDescribedResponse] = [
        PermissionResponse.model_validate(r) for r in rows
    ]
    return PermissionListResponse(items=items, total=len(items), described=False)


@router.put(
    "/{entity_type}/{role}/{action}",
    response_model=PermissionResponse,
    summary="Выдать `action` на `entity_type` для `role`",
    description=(
        "Идемпотентно (повторный grant с тем же scope → noop, возвращает "
        "существующую запись). Body `{target_department_id}` опциональный: "
        "caller обязан либо опустить поле, либо передать свой `department_id`, "
        "иначе 403 DEPARTMENT_ISOLATION (кроме account_admin — ему можно любой). "
        "Невалидная пара (entity_type, action) → 422; системная роль "
        "(admin/guest) → 409 SYSTEM_ROLE_IMMUTABLE."
    ),
    responses={
        403: {"description": "PERMISSION_DENIED либо DEPARTMENT_ISOLATION."},
        409: {"description": "PERMISSION_ALREADY_EXISTS (race) либо SYSTEM_ROLE_IMMUTABLE."},
        422: {"description": "INVALID_ACTION_FOR_ENTITY — action не в whitelist'е для этого entity_type."},
    },
)
async def grant_permission(
    entity_type: str,
    role: str,
    action: str,
    identity: PermissionMatrixIdentity,
    body: PermissionGrant | None = Body(default=None),
    db: AsyncSession = Depends(get_db),
) -> PermissionResponse:
    """
    Что делает: INSERT в `entity_permissions` (или возврат существующей строки
    при идемпотентном повторе).

    Доступ: `(permission, *, permission_grant)`, либо department_admin/
    account_admin bypass. Scope: caller пишет только в свой dept, кроме
    account_admin.

    Возможные ошибки: 403 PERMISSION_DENIED, 403 DEPARTMENT_ISOLATION,
    409 SYSTEM_ROLE_IMMUTABLE, 409 PERMISSION_ALREADY_EXISTS,
    422 INVALID_ACTION_FOR_ENTITY.

    Аудит: `permission.grant` (CRITICAL severity).
    """
    target_dept = body.target_department_id if body is not None else None
    obj = await permission_service.grant_action(
        db, identity,
        entity_type=entity_type, role=role, action=action,
        target_department_id=target_dept,
    )
    return PermissionResponse.model_validate(obj)


@router.delete(
    "/{entity_type}/{role}/{action}",
    response_model=OkResponse,
    summary="Снять `action` на `entity_type` с `role`",
    description=(
        "Удаляет grant. Scope такой же, как у PUT, но передаётся через "
        "query-параметр `target_department_id`. Отсутствие строки → 404."
    ),
    responses={
        403: {"description": "PERMISSION_DENIED либо DEPARTMENT_ISOLATION."},
        404: {"description": "PERMISSION_NOT_FOUND — нет такой строки в scope'е."},
        409: {"description": "SYSTEM_ROLE_IMMUTABLE — роль admin/guest."},
    },
)
async def revoke_permission(
    entity_type: str,
    role: str,
    action: str,
    identity: PermissionMatrixIdentity,
    target_department_id: str | None = Query(
        default=None,
        max_length=64,
        description=(
            "Scope строки на удаление. None → system-wide; "
            "`department_id` → per-department (только свой department, "
            "иначе 403 DEPARTMENT_ISOLATION; account_admin — любой)."
        ),
    ),
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """
    Что делает: DELETE по exact-scope.

    Доступ: `(permission, *, permission_revoke)`, либо department_admin/
    account_admin bypass. Scope: симметрично grant'у.

    Возможные ошибки: 403 PERMISSION_DENIED, 403 DEPARTMENT_ISOLATION,
    404 PERMISSION_NOT_FOUND, 409 SYSTEM_ROLE_IMMUTABLE.

    Аудит: `permission.revoke` (CRITICAL severity).
    """
    await permission_service.revoke_action(
        db, identity,
        entity_type=entity_type, role=role, action=action,
        target_department_id=target_department_id,
    )
    return OkResponse()

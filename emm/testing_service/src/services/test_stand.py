"""Use cases стендов (§2.3, §4 плана миграции).

`test_stands` — надстройка над Server/Vm из server_service, без дублирования
их данных. Создание резолвит `department_id` живым запросом к server_service
(`server_client.get_server`, pass-through bearer'а вызывающего — см. модуль
docstring `server_client.py`) и не принимает его от клиента: иначе стенд
можно было бы завести в чужом отделе, просто подделав поле в теле запроса.

Список отдаёт только то, что хранится в БД testing_service — без живого
обогащения (N+1 запросов к server_service на страницу списка того не стоит).
Карточка одного стенда, наоборот, обогащается живыми данными сервера; если
live-вызов не удаётся, карточка всё равно возвращается — без server-блока, а
не 503 на весь запрос.

Чтение (list/get) не проверяет права — открыто любому аутентифицированному
актору, как и `test_definition`. Запись (create/update/delete) — под
матрицей прав `(test_stand, *, create|update|delete)`.
"""

import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action, EntityType
from src.core.exceptions import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
)
from src.dependencies.auth import Identity
from src.models import TestStand
from src.repositories import test_stand as repo
from src.schemas.test_stand import TestStandCreate, TestStandUpdate
from src.services import audit_service, permissions, server_client
from src.utils.ids import test_stand_id as new_id

logger = logging.getLogger(__name__)


def _normalize_legacy_token(value: str | None) -> str | None:
    """Пустая строка из формы — это «имени нет», а не имя длиной ноль.

    Без этого UNIQUE не даст завести второй безымянный стенд: пустые строки
    между собой конфликтуют, в отличие от NULL.
    """
    if value is None:
        return None
    token = value.strip()
    return token or None


async def create_test_stand(
    db: AsyncSession,
    identity: Identity,
    bearer_token: str,
    payload: TestStandCreate,
) -> TestStand:
    """INSERT нового стенда. `department_id` — из живой карточки сервера.

    UNIQUE(server_id) → 409 TEST_STAND_DUPLICATE. Сервер не найден/не виден
    вызывающему/server_service недоступен → ошибка server_client пробрасывается
    как есть (её error_code уже описывает конкретную причину).
    """
    try:
        await permissions.require_action(db, identity, EntityType.TEST_STAND, Action.CREATE)
    except AuthorizationError:
        audit_service.emit(
            "test_stand.create",
            target_type="test_stand",
            status="denied", allowed=False,
            details={"reason": "permission_denied"},
        )
        raise

    try:
        server = await server_client.get_server(bearer_token, payload.server_id)
    except NotFoundError:
        audit_service.emit(
            "test_stand.create",
            target_type="test_stand",
            status="failure", allowed=True,
            details={"reason": "server_not_found", "server_id": payload.server_id},
        )
        raise
    except (AuthorizationError, ServiceUnavailableError):
        audit_service.emit(
            "test_stand.create",
            target_type="test_stand",
            status="failure", allowed=True,
            details={"reason": "server_service_unavailable", "server_id": payload.server_id},
        )
        raise

    department_id = server.get("department_id")
    if not department_id:
        audit_service.emit(
            "test_stand.create",
            target_type="test_stand",
            status="failure", allowed=True,
            details={"reason": "server_missing_department", "server_id": payload.server_id},
        )
        raise ServiceUnavailableError(
            error_code="SERVER_SERVICE_ERROR",
            message="server_service response is missing department_id",
        )

    data = payload.model_dump(mode="json")
    data["id"] = new_id()
    data["department_id"] = department_id
    data["created_by"] = identity.user_id
    data["legacy_token"] = _normalize_legacy_token(data.get("legacy_token"))
    try:
        obj = await repo.create(db, data)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        logger.warning(
            "IntegrityError на создании test_stand: %s", type(exc.orig).__name__
        )
        audit_service.emit(
            "test_stand.create",
            target_type="test_stand",
            status="failure", allowed=True,
            details={"reason": "duplicate", "server_id": payload.server_id},
        )
        raise ConflictError(
            error_code="TEST_STAND_DUPLICATE",
            message="This server is already registered as a test stand",
            details={"hint": "уникальные поля — server_id и legacy_token"},
        ) from exc
    await db.refresh(obj)
    audit_service.emit(
        "test_stand.create",
        target_id=obj.id, target_type="test_stand",
        status="success", allowed=True,
        details={"server_id": obj.server_id, "department_id": obj.department_id},
    )
    return obj


async def get_test_stand(
    db: AsyncSession,
    identity: Identity,
    bearer_token: str,
    stand_id: str,
) -> tuple[TestStand, dict | None, bool]:
    """SELECT стенда по PK + best-effort обогащение карточкой сервера.

    Возвращает `(stand, server, server_unavailable)`. Read без проверки прав
    и без аудита — как у `test_definition`.
    """
    obj = await repo.get_by_id(db, stand_id)
    if obj is None:
        raise NotFoundError(
            error_code="TEST_STAND_NOT_FOUND",
            message="Test stand not found",
        )

    server: dict | None = None
    server_unavailable = False
    try:
        server = await server_client.get_server(bearer_token, obj.server_id)
    except (NotFoundError, AuthorizationError, ServiceUnavailableError) as exc:
        logger.info(
            "test_stand %s: live server lookup failed (%s) — отдаём карточку без server-блока",
            stand_id, type(exc).__name__,
        )
        server_unavailable = True

    return obj, server, server_unavailable


async def list_test_stands(
    db: AsyncSession,
    limit: int,
    offset: int,
    *,
    department_id: str | None = None,
    is_active: bool | None = None,
    queue_enabled: bool | None = None,
    server_id: str | None = None,
) -> tuple[list[TestStand], int]:
    """List + count стендов под фильтрами. Только хранимые поля, без live-обогащения.

    `server_id` — точечный lookup «какой стенд стоит за этим Server/Vm.id»
    (UNIQUE(server_id), значит 0 либо 1 элемент); нужен консоли сервера
    (§8.6 плана миграции), чтобы по `serverId` карточки найти `stand_id`
    и дальше опросить `current-queue-item`.
    """
    items = await repo.list_all(
        db, limit=limit, offset=offset,
        department_id=department_id, is_active=is_active, queue_enabled=queue_enabled,
        server_id=server_id,
    )
    total = await repo.count_all(
        db, department_id=department_id, is_active=is_active, queue_enabled=queue_enabled,
        server_id=server_id,
    )
    return items, total


async def get_stand_or_404(db: AsyncSession, stand_id: str) -> TestStand:
    """SELECT стенда по PK без live-обогащения сервером — чистый existence-check.

    Соседние read-эндпоинты, которым нужен только факт «стенд существует»
    (например `current-queue-item`, §8.6), не обязаны платить за N+1 к
    server_service ради этого — тот вызов делает `get_test_stand`.
    """
    obj = await repo.get_by_id(db, stand_id)
    if obj is None:
        raise NotFoundError(
            error_code="TEST_STAND_NOT_FOUND",
            message="Test stand not found",
        )
    return obj


async def update_test_stand(
    db: AsyncSession,
    identity: Identity,
    stand_id: str,
    payload: TestStandUpdate,
) -> TestStand:
    """PATCH-обновление. Изменяемы только `queue_enabled`/`is_active`."""
    try:
        await permissions.require_action(db, identity, EntityType.TEST_STAND, Action.UPDATE)
    except AuthorizationError:
        audit_service.emit(
            "test_stand.update",
            target_id=stand_id, target_type="test_stand",
            status="denied", allowed=False,
            details={"reason": "permission_denied"},
        )
        raise
    obj = await repo.get_by_id(db, stand_id)
    if obj is None:
        audit_service.emit(
            "test_stand.update",
            target_id=stand_id, target_type="test_stand",
            status="failure", allowed=True,
            details={"reason": "not_found"},
        )
        raise NotFoundError(
            error_code="TEST_STAND_NOT_FOUND",
            message="Test stand not found",
        )

    changes = payload.model_dump(exclude_unset=True, mode="json")
    if "legacy_token" in changes:
        changes["legacy_token"] = _normalize_legacy_token(changes["legacy_token"])
    if not changes:
        return obj
    try:
        await repo.update(db, obj, changes)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        audit_service.emit(
            "test_stand.update",
            target_id=stand_id, target_type="test_stand",
            status="failure", allowed=True,
            details={"reason": "duplicate", "fields": list(changes.keys())},
        )
        raise ConflictError(
            error_code="TEST_STAND_DUPLICATE",
            message="Another test stand already uses this legacy_token",
            details={"legacy_token": changes.get("legacy_token")},
        ) from exc
    await db.refresh(obj)
    audit_service.emit(
        "test_stand.update",
        target_id=obj.id, target_type="test_stand",
        status="success", allowed=True,
        details={"fields": list(changes.keys())},
    )
    return obj


async def delete_test_stand(
    db: AsyncSession,
    identity: Identity,
    stand_id: str,
) -> None:
    """Hard-delete стенда. Сервер в server_service не трогается."""
    try:
        await permissions.require_action(db, identity, EntityType.TEST_STAND, Action.DELETE)
    except AuthorizationError:
        audit_service.emit(
            "test_stand.delete",
            target_id=stand_id, target_type="test_stand",
            status="denied", allowed=False,
            details={"reason": "permission_denied"},
        )
        raise
    obj = await repo.get_by_id(db, stand_id)
    if obj is None:
        audit_service.emit(
            "test_stand.delete",
            target_id=stand_id, target_type="test_stand",
            status="failure", allowed=True,
            details={"reason": "not_found"},
        )
        raise NotFoundError(
            error_code="TEST_STAND_NOT_FOUND",
            message="Test stand not found",
        )
    server_id = obj.server_id
    await repo.delete(db, obj)
    await db.commit()
    audit_service.emit(
        "test_stand.delete",
        target_id=stand_id, target_type="test_stand",
        status="success", allowed=True,
        details={"server_id": server_id},
    )


async def get_test_stand_credentials(
    db: AsyncSession,
    identity: Identity,
    bearer_token: str,
    stand_id: str,
    *,
    reveal: bool,
) -> dict:
    """Прокси на `server_service`'овский `GET /servers/{id}/test-credentials` (§5.3).

    Единый гейт на метаданные и на секрет — `view_test_credentials`, как и на
    стороне server_service: без него не отдаём даже `username`/`rotated_at`.
    Секрет остаётся у server_service, здесь только pass-through его ответа
    (bearer вызывающего, не сервисный ключ — этот эндпоинт гейтится обычной
    ролевой матрицей `server_service.admin`, не shared-secret каналом).
    """
    try:
        await permissions.require_action(
            db, identity, EntityType.TEST_STAND, Action.VIEW_TEST_CREDENTIALS,
        )
    except AuthorizationError:
        audit_service.emit(
            "test_stand.test_credentials_viewed",
            target_id=stand_id, target_type="test_stand",
            status="denied", allowed=False,
            details={"reveal": reveal},
        )
        raise

    obj = await repo.get_by_id(db, stand_id)
    if obj is None:
        raise NotFoundError(
            error_code="TEST_STAND_NOT_FOUND",
            message="Test stand not found",
        )
    data = await server_client.get_test_credentials(bearer_token, obj.server_id, reveal=reveal)
    audit_service.emit(
        "test_stand.test_credentials_viewed",
        target_id=stand_id, target_type="test_stand",
        status="success", allowed=True,
        details={"reveal": reveal, "server_id": obj.server_id},
    )
    return data

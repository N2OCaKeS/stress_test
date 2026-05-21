"""Internal-эндпоинты под фоновую ре-шифрацию секретов воркером.

Контракт: оба endpoint'а сидят в hidden `/internal/secrets/...` namespace'е
(`include_in_schema=False`) и предназначены только для server_worker. Авторизация
— через ту же матрицу `entity_permissions`, что и остальные internal-вызовы;
worker_bot роль уже несёт `(server_account, rotate_password)` и
`(ipmi_controller, rotate_credentials)`, что и проверяется.

Бизнес-логика — в `services/secrets_migration_service.py`; ротация секретов
идёт постепенно, батчами.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action, EntityType
from src.core.exceptions import AuthorizationError
from src.dependencies.auth import CurrentIdentity
from src.dependencies.db import get_db
from src.schemas.secrets_migration import (
    MigrationStatusResponse,
    ReencryptBatchResponse,
)
from src.services import audit_service, permissions
from src.services import secrets_migration_service

router = APIRouter(prefix="/internal/secrets", include_in_schema=False)


async def _require_worker_scope(db: AsyncSession, identity: CurrentIdentity) -> None:
    """Проверка полного worker-scope: чтение И ротация по обоим типам секретов.

    Endpoint реально делает decrypt-old → encrypt-active по обеим таблицам
    (`server_accounts` и `ipmi_controllers`), поэтому семантика — `view + rotate`
    для каждой. Из default-ролей все четыре грана несёт только `worker_bot`
    (миграция `43cf9cfef9e1_…`); `operator` сидит лишь на `rotate_*` без
    `view_*`, что и режет ему доступ — миграция секретов не операторская задача.
    Через `has_action` (а не `require_action`), чтобы при отсутствии любого
    из четырёх отдать единый явный 403 SECRETS_MIGRATION_DENIED, а не
    случайный action-specific код.
    """
    checks = (
        (EntityType.SERVER_ACCOUNT, Action.VIEW_PASSWORD),
        (EntityType.SERVER_ACCOUNT, Action.ROTATE_PASSWORD),
        (EntityType.IPMI_CONTROLLER, Action.VIEW_CREDENTIALS),
        (EntityType.IPMI_CONTROLLER, Action.ROTATE_CREDENTIALS),
    )
    for entity_type, action in checks:
        if not await permissions.has_action(db, identity, entity_type, action):
            raise AuthorizationError(
                error_code="SECRETS_MIGRATION_DENIED",
                message=(
                    "Caller lacks full secrets-migration scope "
                    "(needs view+rotate on server_account и ipmi_controller)"
                ),
            )


@router.get("/migration_status", response_model=MigrationStatusResponse)
async def get_migration_status(
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> MigrationStatusResponse:
    """Сколько осталось перешифровать. Полностью read-only.

    Оператор и worker зовут это, чтобы понять, можно ли уже дропнуть
    `SERVER_ENCRYPTION_KEY__v<old>`. `remaining == 0` = безопасно.
    """
    await _require_worker_scope(db, identity)
    data = await secrets_migration_service.status(db)
    return MigrationStatusResponse(**data)


@router.post("/reencrypt_batch", response_model=ReencryptBatchResponse)
async def reencrypt_batch(
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=1000),
) -> ReencryptBatchResponse:
    """Перешифровать до `limit` записей активной версией ключа.

    Идемпотентна: повторный вызов не трогает строки, у которых префикс
    уже совпадает с активной версией. Сетевая ошибка / падение worker'а
    между call'ами безопасны — следующая попытка возьмёт оставшиеся.

    Audit: `secrets.reencrypt_batch` (INFO на success, чтобы операторы
    могли проследить ход миграции через loging_service).
    """
    await _require_worker_scope(db, identity)
    data = await secrets_migration_service.reencrypt_batch(db, limit=limit)
    # Если ВСЕ строки в батче упали с decrypt/encrypt ошибками — это
    # серьёзный сигнал (битый ciphertext или неправильная версия мастер-ключа).
    # Пишем `status=failure`, чтобы loging_service поднял severity и SOC
    # получил event, а не INFO-шум.
    processed = data["processed"]
    errors = data["errors"]
    is_total_failure = errors > 0 and processed == 0
    audit_service.emit(
        "secrets.reencrypt_batch",
        target_id=None,
        target_type="secret",
        status="failure" if is_total_failure else "success",
        allowed=True,
        details={
            "limit": limit,
            "processed": processed,
            "errors": errors,
        },
    )
    return ReencryptBatchResponse(**data)

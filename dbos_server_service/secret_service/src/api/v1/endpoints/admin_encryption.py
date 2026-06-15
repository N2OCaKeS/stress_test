"""Admin-эндпоинты ротации мастер-ключа secret_service для `account_admin`.

Зеркало `server_service/src/api/v1/endpoints/admin_encryption.py`, но крипто
secret_service раздельно (собственный master-key `SECRETS_MASTER_KEY_B64`).

Ключи шифрования — инфраструктура, а не бизнес-данные. Ротацию из UI
инициирует платформенный владелец `account_admin`; у него нет department и
department-service-access, поэтому штатный `require_user_context` его отбил бы
403. Здесь гейт — `require_account_admin`: пропускается только платформенный
админ, и только на эти три инфраструктурные ручки. Обычный CRUD/reveal
credentials остаётся за `require_user_context` и для account_admin закрыт.

Бизнес-логика переиспользуется как есть:

* `key_rotation_service.rotate` / `.retire` — keystore-bump + reencrypt-outbox;
* `migration_status_service.compute` — прогресс ре-шифрации для UI-поллинга.

S2S-канал (`/internal/encryption/*`, `/internal/migration_status`) под
shared-secret'ом остаётся как есть — это другой actor.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.keystore import get_keystore
from src.dependencies.auth import CurrentIdentity, require_account_admin
from src.dependencies.db import get_db
from src.schemas.internal import (
    MigrationStatus,
    RetireKeyResponse,
    RotateKeyResponse,
)
from src.services import (
    audit_service,
    key_rotation_service,
    migration_status_service,
)

router = APIRouter(prefix="/admin/encryption", tags=["admin-encryption"])


@router.get(
    "/migration_status",
    response_model=MigrationStatus,
    responses={
        200: {"description": "Прогресс ре-шифрации для UI-поллинга."},
        401: {"description": "ACCESS_TOKEN_MISSING / UNAUTHORIZED."},
        403: {"description": "ACCOUNT_ADMIN_REQUIRED — нужна платформенная роль account_admin."},
    },
)
async def admin_migration_status(
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> MigrationStatus:
    """Read-only прогресс ре-шифрации credentials под активную версию ключа.

    UI поллит этот endpoint после `rotate`, чтобы показать
    `remaining_legacy`/`total_rows`/`by_version`/`migrated_pct` и понять, когда
    можно `retire` старую версию (`remaining_legacy == 0` и
    `outbox_pending_count == 0`).
    """
    require_account_admin(identity)
    active_version = get_keystore().get_active_version()
    return await migration_status_service.compute(db, active_version=active_version)


@router.post(
    "/rotate",
    response_model=RotateKeyResponse,
    responses={
        200: {"description": "Новая версия активна, reencrypt-outbox засеян."},
        401: {"description": "ACCESS_TOKEN_MISSING / UNAUTHORIZED."},
        403: {"description": "ACCOUNT_ADMIN_REQUIRED."},
        400: {"description": "ROTATE_KEY_INVALID — new_key_b64 не base64/не 32 байта."},
    },
)
async def admin_rotate_encryption_key(
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
    new_key_b64: str = Body(
        embed=True,
        min_length=1,
        description=(
            "Новый master-материал в base64 (32 байта). Сгенерить через"
            " auth_service `POST /admin/service-keys/generate`."
        ),
    ),
) -> RotateKeyResponse:
    """Рантайм-ротация мастер-ключа secret_service без простоя, из UI.

    Новая версия становится активной (новые токены сразу под ней), старые
    токены остаются читаемыми (материал в keystore), фоновая ре-шифрация
    публикуется через reencrypt-outbox. Идемпотентно: повтор с тем же
    материалом не плодит версию.

    Audit: `secrets.admin_encryption_rotate` (CRITICAL, actor = account_admin).
    """
    require_account_admin(identity)
    data = await key_rotation_service.rotate(db, new_key_b64=new_key_b64)
    audit_service.emit(
        "secrets.admin_encryption_rotate",
        target_id=None,
        target_type="secret",
        status="success",
        allowed=True,
        details={
            "new_version": data["new_version"],
            "previous_version": data["previous_version"],
            "seeded_inserted": data["seeded"]["inserted"],
            "idempotent": data["idempotent"],
        },
    )
    return RotateKeyResponse(**data)


@router.post(
    "/retire/{version}",
    response_model=RetireKeyResponse,
    responses={
        200: {"description": "Версия убрана из keystore (или уже отсутствовала)."},
        401: {"description": "ACCESS_TOKEN_MISSING / UNAUTHORIZED."},
        403: {"description": "ACCOUNT_ADMIN_REQUIRED."},
        409: {"description": "KEYSTORE_CANNOT_RETIRE_ACTIVE / KEYSTORE_VERSION_IN_USE."},
    },
)
async def admin_retire_encryption_key(
    identity: CurrentIdentity,
    version: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
) -> RetireKeyResponse:
    """Убрать старую версию мастер-ключа из keystore, инициировано из UI.

    Разрешено только когда на версии 0 строк (полная ре-шифрация завершена)
    и она не активна — иначе 409.

    Audit: `secrets.admin_encryption_retire` (CRITICAL, actor = account_admin).
    """
    require_account_admin(identity)
    data = await key_rotation_service.retire(db, version=version)
    audit_service.emit(
        "secrets.admin_encryption_retire",
        target_id=None,
        target_type="secret",
        status="success",
        allowed=True,
        details={
            "version": data["version"],
            "retired": data["retired"],
        },
    )
    return RetireKeyResponse(**data)

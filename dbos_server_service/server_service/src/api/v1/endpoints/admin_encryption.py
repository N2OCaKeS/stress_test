"""Admin-эндпоинты ротации мастер-ключа шифрования для `account_admin`.

Ключи шифрования — инфраструктура, а не бизнес-данные серверов/аккаунтов,
поэтому ротацию из UI инициирует платформенный владелец (`account_admin`).
`platform_admin_guard` блокирует account_admin'а на всех остальных
server_service-эндпоинтах (бизнес-данные); эти три ручки — явное исключение
из этого блока (см. allowlist в `middleware/platform_admin_guard`). Они
возвращают только статус ротации и версии ключа — никаких паролей/секретов.

Бизнес-логика переиспользуется как есть:

* `key_rotation_service.rotate` / `.retire` — keystore-bump + reencrypt-outbox;
* `secrets_migration_service.status` — прогресс ре-шифрации для UI-поллинга.

S2S-канал (`/internal/encryption/*`, `/internal/migration_status`) под
`rotation_runner` shared-secret'ом остаётся как есть — это другой actor.
"""

from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import AccountAdminIdentity
from src.dependencies.db import get_db
from src.schemas.secrets_migration import (
    MigrationStatusResponse,
    RetireKeyResponse,
    RotateKeyRequest,
    RotateKeyResponse,
)
from src.services import audit_service
from src.services import key_rotation_service
from src.services import secrets_migration_service

router = APIRouter(prefix="/admin/encryption", tags=["admin-encryption"])


@router.get(
    "/migration_status",
    response_model=MigrationStatusResponse,
    responses={
        200: {"description": "Прогресс ре-шифрации для UI-поллинга."},
        401: {"description": "ACCESS_TOKEN_MISSING / ACCESS_TOKEN_INVALID / USER_BANNED."},
        403: {"description": "ACCOUNT_ADMIN_REQUIRED — нужна платформенная роль account_admin."},
    },
)
async def admin_migration_status(
    identity: AccountAdminIdentity,
    db: AsyncSession = Depends(get_db),
) -> MigrationStatusResponse:
    """Read-only прогресс ре-шифрации секретов под активную версию ключа.

    UI поллит этот endpoint после `rotate`, чтобы показать
    `remaining`/`total`/`by_version`/`migrated_pct` и понять, когда можно
    `retire` старую версию (`remaining == 0` и `outbox.pending == 0`).
    """
    data = await secrets_migration_service.status(db)
    return MigrationStatusResponse(**data)


@router.post(
    "/rotate",
    response_model=RotateKeyResponse,
    responses={
        200: {"description": "Новая версия активна, reencrypt-outbox засеян."},
        401: {"description": "ACCESS_TOKEN_MISSING / ACCESS_TOKEN_INVALID / USER_BANNED."},
        403: {"description": "ACCOUNT_ADMIN_REQUIRED."},
        422: {"description": "ROTATE_KEY_INVALID — new_key_b64 не base64/не 32 байта."},
    },
)
async def admin_rotate_encryption_key(
    payload: RotateKeyRequest,
    identity: AccountAdminIdentity,
    db: AsyncSession = Depends(get_db),
) -> RotateKeyResponse:
    """Рантайм-ротация мастер-ключа сервера без простоя, инициированная из UI.

    Новый ключ генерит auth_service (`POST /admin/service-keys/generate`),
    UI присылает его сюда. Новая версия становится активной (новые токены
    сразу под ней), старые остаются читаемыми (материал в keystore), фоновая
    ре-шифрация публикуется через reencrypt-outbox. Идемпотентно: повтор с
    тем же материалом не плодит версию.

    Audit: `encryption.admin_rotate` (CRITICAL, actor = account_admin).
    """
    data = await key_rotation_service.rotate(db, new_key_b64=payload.new_key_b64)
    audit_service.emit(
        "encryption.admin_rotate",
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
        401: {"description": "ACCESS_TOKEN_MISSING / ACCESS_TOKEN_INVALID / USER_BANNED."},
        403: {"description": "ACCOUNT_ADMIN_REQUIRED."},
        409: {"description": "KEYSTORE_CANNOT_RETIRE_ACTIVE / KEYSTORE_VERSION_IN_USE."},
    },
)
async def admin_retire_encryption_key(
    identity: AccountAdminIdentity,
    version: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
) -> RetireKeyResponse:
    """Убрать старую версию мастер-ключа из keystore, инициировано из UI.

    Разрешено только когда на версии 0 строк (полная ре-шифрация завершена)
    и она не активна — иначе 409. После retire старый материал недоступен,
    расшифровать токены этой версии станет нельзя.

    Audit: `encryption.admin_retire` (CRITICAL, actor = account_admin).
    """
    data = await key_rotation_service.retire(db, version=version)
    audit_service.emit(
        "encryption.admin_retire",
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

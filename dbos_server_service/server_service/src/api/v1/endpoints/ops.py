"""Ops-эндпоинты под s2s-runner'ы (rotation_runner и др.).

Отдельный канал от worker_bot'а:

* worker_bot ходит через auth_service introspect, PAT/bot-токен, проверяется
  матрицей `entity_permissions` (`view_password`, `rotate_password`, …);
* ops-runner ходит с shared-secret'ом из `SERVER_INBOUND_SERVICE_API_KEYS`
  под header'ом `X-Service-Identity: <identity>` — без user, без department,
  без матрицы прав. Доступ ограничивается whitelist'ом identity на каждый
  route (см. `require_internal_caller`).

Сейчас здесь — `GET /internal/migration_status` для `scripts/k8s/rotate_master_key.sh
--auto-finalize`. Раньше скрипт дёргал `/internal/secrets/migration_status`
под bearer'ом rotation_runner, но тот защищён `_require_worker_scope` (worker_bot
матрица) и отбивал shared-secret 403. Здесь endpoint правильный: ровно тот же
service-layer `migration_status()`, но под s2s-аутентификацией.
"""

from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import require_internal_caller
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

# Здесь два router'а:
#  - `ops_router` собирает все ops-роуты; include_in_schema=False, потому что
#    публичный OpenAPI не должен ничего знать про runner'ов;
#  - `router` — public alias под унифицированный импорт из `v1/router.py`.
ops_router = APIRouter(prefix="/internal", include_in_schema=False)
router = ops_router


@ops_router.get(
    "/migration_status",
    response_model=MigrationStatusResponse,
    responses={
        200: {"description": "Сводка состояния миграции секретов."},
        401: {"description": "SERVICE_IDENTITY_REQUIRED / INVALID_SERVICE_TOKEN."},
        403: {"description": "SERVICE_IDENTITY_NOT_ALLOWED."},
    },
)
async def get_migration_status(
    db: AsyncSession = Depends(get_db),
    caller: str = Depends(require_internal_caller("rotation_runner")),
) -> MigrationStatusResponse:
    """Read-only сводка по `secrets_reencrypt_outbox` + per-column legacy-residue.

    Возвращает ровно тот же payload, что и `/internal/secrets/migration_status`
    (тот ходит под worker_bot scope'ом). Здесь — под `rotation_runner` shared
    secret'ом: скрипт `scripts/k8s/rotate_master_key.sh --auto-finalize`
    дожидается `outbox.pending == 0` и `remaining_legacy_total == 0`, чтобы
    дропнуть старый `SERVER_ENCRYPTION_KEY__v<old>`.

    Audit: `ops.migration_status_read` (INFO) — фиксируем сам факт чтения
    rotation-runner'ом, чтобы SIEM видел кто и когда смотрел на residue.
    """
    data = await secrets_migration_service.status(db)
    audit_service.emit(
        "ops.migration_status_read",
        target_id=None,
        target_type="secret",
        status="success",
        allowed=True,
        details={
            "identity": caller,
            "remaining_legacy_total": data["remaining_legacy_total"],
            "outbox_pending": data["outbox_pending"],
            "active_version": data["active_version"],
        },
    )
    return MigrationStatusResponse(**data)


@ops_router.post(
    "/encryption/rotate",
    response_model=RotateKeyResponse,
    responses={
        200: {"description": "Новая версия активна, reencrypt-outbox засеян."},
        401: {"description": "SERVICE_IDENTITY_REQUIRED / INVALID_SERVICE_TOKEN."},
        403: {"description": "SERVICE_IDENTITY_NOT_ALLOWED."},
        422: {"description": "ROTATE_KEY_INVALID — new_key_b64 не base64/не 32 байта."},
    },
)
async def rotate_encryption_key(
    payload: RotateKeyRequest,
    db: AsyncSession = Depends(get_db),
    caller: str = Depends(require_internal_caller("rotation_runner")),
) -> RotateKeyResponse:
    """Рантайм-ротация мастер-ключа без простоя.

    Новый ключ генерит auth_service (`POST /admin/service-keys/generate`),
    rotation-runner присылает его сюда. Эффект: новая версия становится
    активной (новые токены сразу под ней), старые токены остаются читаемыми
    (материал старых версий ещё в keystore), и фоновая ре-шифрация
    публикуется через reencrypt-outbox. Идемпотентно: повтор с тем же
    материалом не плодит версию.

    Audit: `ops.encryption_rotate` (CRITICAL).
    """
    data = await key_rotation_service.rotate(
        db, new_key_b64=payload.new_key_b64, mode=payload.mode
    )
    audit_service.emit(
        "ops.encryption_rotate",
        target_id=None,
        target_type="secret",
        status="success",
        allowed=True,
        details={
            "identity": caller,
            "new_version": data["new_version"],
            "previous_version": data["previous_version"],
            "seeded_inserted": data["seeded"]["inserted"],
            "idempotent": data["idempotent"],
            "mode": data["mode"],
            "force_active": data["force_active"],
        },
    )
    return RotateKeyResponse(**data)


@ops_router.post(
    "/encryption/retire/{version}",
    response_model=RetireKeyResponse,
    responses={
        200: {"description": "Версия убрана из keystore (или уже отсутствовала)."},
        401: {"description": "SERVICE_IDENTITY_REQUIRED / INVALID_SERVICE_TOKEN."},
        403: {"description": "SERVICE_IDENTITY_NOT_ALLOWED."},
        409: {"description": "KEYSTORE_CANNOT_RETIRE_ACTIVE / KEYSTORE_VERSION_IN_USE."},
    },
)
async def retire_encryption_key(
    version: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
    caller: str = Depends(require_internal_caller("rotation_runner")),
) -> RetireKeyResponse:
    """Убрать старую версию мастер-ключа из keystore.

    Разрешено только когда на версии 0 строк (полная ре-шифрация завершена)
    и она не активна — иначе 409. После retire старый материал больше
    недоступен, расшифровать токены этой версии станет нельзя.

    Audit: `ops.encryption_retire` (CRITICAL).
    """
    data = await key_rotation_service.retire(db, version=version)
    audit_service.emit(
        "ops.encryption_retire",
        target_id=None,
        target_type="secret",
        status="success",
        allowed=True,
        details={
            "identity": caller,
            "version": data["version"],
            "retired": data["retired"],
        },
    )
    return RetireKeyResponse(**data)

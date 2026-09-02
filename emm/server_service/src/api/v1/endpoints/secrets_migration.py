"""Internal-эндпоинты под фоновую ре-шифрацию секретов воркером.

Контракт: все endpoint'ы сидят в hidden `/internal/secrets/...` namespace'е
(`include_in_schema=False`) и предназначены только для server_worker. Авторизация
— через ту же матрицу `entity_permissions`, что и остальные internal-вызовы;
worker_bot роль уже несёт `(server_account, rotate_password)` и
`(ipmi_controller, rotate_credentials)`, что и проверяется.

Outbox-pattern. Старые `migration_status` / `reencrypt_batch` оставлены для
обратной совместимости и одноразовых ручных прогонов. Основной поток теперь:

* `POST /reencrypt_outbox/seed` — server-service сканит owner-таблицы и
  публикует pending-row'ы.
* `GET  /reencrypt_outbox/pending?limit=N` — воркер атомарно claim'ит батч.
* `POST /reencrypt_outbox/{id}/done` — после успешной перешифровки.
* `POST /reencrypt_outbox/{id}/failed` — на decrypt/encrypt ошибке.
* `POST /reencrypt_outbox/cleanup?older_than_hours=N` — оператор/периодик
  чистит закрытые row'ы.

Бизнес-логика — в `services/secrets_migration_service.py`.
"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.constants import Action, EntityType
from src.core.exceptions import AppException, AuthorizationError
from src.core.limiter import endpoint_limiter
from src.dependencies.auth import CurrentIdentity
from src.dependencies.db import get_db
from src.schemas.secrets_migration import (
    MigrationStatusResponse,
    OutboxClaimResponse,
    OutboxCleanupResponse,
    OutboxFinalizeDoneResponse,
    OutboxFinalizeFailedRequest,
    OutboxFinalizeFailedResponse,
    OutboxItem,
    ReencryptBatchResponse,
    SeedOutboxResponse,
)
from src.services import audit_service, permissions
from src.services import secrets_migration_service

router = APIRouter(prefix="/internal/secrets", include_in_schema=False)


async def _require_worker_scope(db: AsyncSession, identity: CurrentIdentity) -> None:
    """Проверка полного worker-scope: чтение И ротация по обоим типам секретов.

    Endpoint реально делает decrypt-old → encrypt-active по обеим таблицам
    (`server_accounts` и `ipmi_controllers`), поэтому семантика — `view + rotate`
    для каждой. Из системных ролей полный набор из четырёх грантов несёт только
    `worker_bot` (миграция `43cf9cfef9e1_…`); роль без всех четырёх (нет любого
    из `view_*`/`rotate_*`) сюда не проходит — миграция секретов не штатная задача.
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
    `SERVER_ENCRYPTION_KEY__v<old>`. `remaining == 0` и `outbox.pending +
    outbox.processing == 0` = безопасно.
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
    """Legacy sync-путь: перешифровать до `limit` записей активной версией.

    Сохранён для совместимости со старым воркером и ad-hoc-прогонов из CLI.
    Идемпотентна; повторный вызов не трогает уже мигрированные. Новый поток
    идёт через outbox-эндпоинты ниже.

    Audit: `secrets.reencrypt_batch` (INFO на success, failure при total-failure).
    """
    await _require_worker_scope(db, identity)
    data = await secrets_migration_service.reencrypt_batch(db, limit=limit)
    processed = data["processed"]
    errors = data["errors"]
    failed_rows = data.get("failed_rows", [])
    is_total_failure = errors > 0 and processed == 0
    details: dict[str, object] = {
        "limit": limit,
        "processed": processed,
        "errors": errors,
    }
    if failed_rows:
        # Cap на случай гигантских батчей: SIEM detail-field не любит длинные
        # массивы. Полный счётчик уже в `errors`; здесь — sample для триажа.
        details["failed_rows"] = failed_rows[:50]
    audit_service.emit(
        "secrets.reencrypt_batch",
        target_id=None,
        target_type="secret",
        status="failure" if is_total_failure else "success",
        allowed=True,
        details=details,
    )
    return ReencryptBatchResponse(processed=processed, errors=errors)


# ── Outbox-pattern endpoints ────────────────────────────────────────────────


@router.post(
    "/reencrypt_outbox/seed",
    response_model=SeedOutboxResponse,
    responses={
        200: {"description": "Pending outbox-row'ы опубликованы."},
        403: {"description": "SECRETS_MIGRATION_DENIED — нет full worker-scope (`view+rotate` на server_account и ipmi_controller)."},
        429: {"description": "RATE_LIMIT_EXCEEDED — per-IP worker-pool лимит пробит (WORKER_POOL_RATE_LIMIT)."},
        500: {"description": "DECRYPT_FAILED / ENCRYPTION_KEY_MISSING / SECRETS_REENCRYPT_FINALIZE_FAILED."},
    },
)
@endpoint_limiter.limit(get_settings().worker_pool_rate_limit)
async def seed_reencrypt_outbox(
    request: Request,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(
        default=500,
        ge=1,
        le=5000,
        description=(
            "Максимум INSERT'ов за один проход. На крупном bump'е версии"
            " ключа caller вызывает endpoint несколько раз до `inserted=0`."
        ),
    ),
) -> SeedOutboxResponse:
    """Просканировать owner-таблицы и опубликовать pending outbox-row'ы.

    Идемпотентно: partial-unique индекс `(entity_type, entity_id) WHERE
    status IN ('pending','processing')` не даст вставить дубль для
    owner-row, у которого уже висит активная задача.

    Audit: `secrets.reencrypt_seed` (INFO).
    """
    await _require_worker_scope(db, identity)
    data = await secrets_migration_service.seed_outbox(db, limit=limit)
    audit_service.emit(
        "secrets.reencrypt_seed",
        target_id=None,
        target_type="secret",
        status="success",
        allowed=True,
        details={
            "inserted": data["inserted"],
            "scanned": data["scanned"],
            "active_version": data["active_version"],
            "limit": limit,
        },
    )
    return SeedOutboxResponse(**data)


@router.get(
    "/reencrypt_outbox/pending",
    response_model=OutboxClaimResponse,
    responses={
        200: {"description": "Batch claimed под worker'а."},
        403: {"description": "SECRETS_MIGRATION_DENIED."},
        429: {"description": "RATE_LIMIT_EXCEEDED — per-IP worker-pool лимит пробит."},
    },
)
@endpoint_limiter.limit(get_settings().worker_pool_rate_limit)
async def claim_reencrypt_outbox(
    request: Request,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=500),
) -> OutboxClaimResponse:
    """Атомарно claim'нуть batch pending row'ов под worker.

    `FOR UPDATE SKIP LOCKED` — параллельные replica'и не дублируют работу.
    Транзакция короткая: SELECT + UPDATE status → 'processing'.
    """
    await _require_worker_scope(db, identity)
    rows = await secrets_migration_service.claim_pending(db, limit=limit)
    return OutboxClaimResponse(items=[OutboxItem(**r) for r in rows])


@router.post(
    "/reencrypt_outbox/{outbox_id}/done",
    response_model=OutboxFinalizeDoneResponse,
    responses={
        200: {"description": "Row закрыт (status=done) либо помечен skipped/warning."},
        403: {"description": "SECRETS_MIGRATION_DENIED."},
        404: {"description": "SECRETS_OUTBOX_ROW_NOT_FOUND."},
        500: {"description": "DECRYPT_FAILED / ENCRYPTION_KEY_MISSING / SECRETS_REENCRYPT_FINALIZE_FAILED."},
    },
)
async def finalize_reencrypt_outbox_done(
    outbox_id: str,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> OutboxFinalizeDoneResponse:
    """Закрыть outbox-row: decrypt legacy → encrypt active → UPDATE owner-row.

    Crypto-операции остаются на server-side (ключи никогда не покидают
    сервис). Транзакция короткая, владелец-row блокируется только пока
    идёт UPDATE — это и устраняет старую блокировку pool'а.

    Audit:

    * success → `secrets.reencrypt_done` (status=success, severity INFO);
    * skipped (owner уже перешифровался параллельно / пропал / outbox-row
      закрыт другой ветвью) → `secrets.reencrypt_done` со
      `status="warning"` и `details.reason in {owner_vanished,
      owner_ciphertext_changed, status_not_processing}`; service-layer
      эмитит парный `secrets.migration.skipped` с тем же reason;
    * 404 если row не существует.
    """
    await _require_worker_scope(db, identity)
    try:
        data = await secrets_migration_service.finalize_done(db, outbox_id)
    except AppException:
        # Crypto-ошибки (`DECRYPT_FAILED`, `ENCRYPTION_KEY_MISSING`) и любые
        # другие structured-исключения пробрасываем как есть, чтобы worker
        # увидел оригинальный error_code и записал его в `/failed`.
        raise
    except Exception as exc:  # noqa: BLE001 — нестандартные runtime-ошибки
        # Только для несвязанных runtime-ошибок оборачиваем в generic
        # `SECRETS_REENCRYPT_FINALIZE_FAILED` — иначе worker полностью
        # потеряет первопричину crash'а.
        raise AppException(
            http_status=500,
            error_code="SECRETS_REENCRYPT_FINALIZE_FAILED",
            message=f"finalize_done crashed: {type(exc).__name__}",
        ) from exc

    if data["status"] == "missing":
        raise AppException(
            http_status=404,
            error_code="SECRETS_OUTBOX_ROW_NOT_FOUND",
            message=f"outbox row {outbox_id!r} not found",
        )

    # На skipped service-layer уже эмитит `secrets.migration.skipped` с
    # деталями (owner_vanished / owner_ciphertext_changed / status_not_processing).
    # Endpoint-уровень при этом всё равно успешно закрыл row, но семантически
    # это не plain success — отдаём `warning`, чтобы SIEM правила не считали
    # такие row'ы как чистые перешифровки.
    is_skipped = bool(data.get("skipped"))
    skip_reason = data.get("skip_reason")
    details: dict[str, object] = {
        "outbox_id": outbox_id,
        "status": data["status"],
        "skipped": is_skipped,
    }
    if is_skipped and skip_reason:
        details["reason"] = skip_reason
    audit_service.emit(
        "secrets.reencrypt_done",
        target_id=outbox_id,
        target_type="secret",
        status="warning" if is_skipped else "success",
        allowed=True,
        details=details,
    )
    # `skip_reason` — внутреннее поле для аудита, не часть wire-контракта.
    response_data = {k: v for k, v in data.items() if k != "skip_reason"}
    return OutboxFinalizeDoneResponse(**response_data)


@router.post(
    "/reencrypt_outbox/{outbox_id}/failed",
    response_model=OutboxFinalizeFailedResponse,
)
async def finalize_reencrypt_outbox_failed(
    outbox_id: str,
    payload: OutboxFinalizeFailedRequest,
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> OutboxFinalizeFailedResponse:
    """Пометить outbox-row `failed` с описанием ошибки.

    Worker зовёт после неудачной `done`-попытки на своей стороне (transport
    или хочется записать осмысленную причину). Идемпотентно — повторный
    POST не меняет уже-failed строку.

    Audit: `secrets.reencrypt_failed` (WARNING).
    """
    await _require_worker_scope(db, identity)
    data = await secrets_migration_service.finalize_failed(
        db, outbox_id, error=payload.error
    )
    if data["status"] == "missing":
        raise AppException(
            http_status=404,
            error_code="SECRETS_OUTBOX_ROW_NOT_FOUND",
            message=f"outbox row {outbox_id!r} not found",
        )
    audit_service.emit(
        "secrets.reencrypt_failed",
        target_id=outbox_id,
        target_type="secret",
        status="failure",
        allowed=True,
        details={
            "outbox_id": outbox_id,
            "status": data["status"],
        },
    )
    return OutboxFinalizeFailedResponse(**data)


@router.post(
    "/reencrypt_outbox/cleanup",
    response_model=OutboxCleanupResponse,
)
async def cleanup_reencrypt_outbox(
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
    older_than_hours: int = Query(
        default=24,
        ge=1,
        le=24 * 90,
        description=(
            "Удалить `done`-row'ы старше N часов. 24 — sane дефолт для"
            " operator-ручки; периодик можно гонять с 72-168."
        ),
    ),
) -> OutboxCleanupResponse:
    """Удалить done-row'ы старше `older_than_hours`. Bounded growth таблицы."""
    await _require_worker_scope(db, identity)
    deleted = await secrets_migration_service.cleanup_done(
        db, older_than_hours=older_than_hours
    )
    audit_service.emit(
        "secrets.reencrypt_outbox_cleanup",
        target_id=None,
        target_type="secret",
        status="success",
        allowed=True,
        details={
            "deleted": deleted,
            "older_than_hours": older_than_hours,
        },
    )
    return OutboxCleanupResponse(deleted=deleted)

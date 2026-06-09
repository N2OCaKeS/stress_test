"""Internal HTTP-эндпоинты под proactive re-encrypt очередь.

Дополнение к lazy-пути в `credential_service`: lazy перешифровывает только
кред'ы, которые читают, а «холодные» credential'ы (никто не reveal'ит)
остаются под старым ключом → `migration_status.remaining_legacy > 0`
бесконечно. Outbox-pattern закрывает дыру: оператор / CronJob после ротации
зовут `seed` → `process` → `status`.

Все три endpoint'а скрыты из публичного OpenAPI (`include_in_schema=False`)
и закрыты `require_internal_caller` — bearer-секрет + опциональная
`X-Service-Identity`. Принимаем нескольких caller'ов:

* `rotation_runner` — k8s Job из `rotate_secret_master_key.sh`.
* `worker` / `secret_worker` — фоновый процессор, если когда-нибудь
  материализуется в отдельный pod.
* `account_admin` — оператор может позвать вручную через kubectl-exec.

Жёсткой привязки к одному identity нет — guard валидирует, что caller
принёс валидный `SERVICE_API_KEY` (или ключ из `SERVICE_API_KEYS`).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import AppException
from src.dependencies.db import get_db
from src.dependencies.internal_auth import require_internal_caller
from src.schemas.internal import (
    ReencryptOutboxProcessResponse,
    ReencryptOutboxSeedResponse,
    ReencryptOutboxStatus,
)
from src.services import audit_service, reencrypt_outbox_service

logger = logging.getLogger(__name__)


# Префикс `/internal/reencrypt_outbox` — симметрично server_service'у.
# `include_in_schema=False` — не светим в OpenAPI; caller'ы дёргают
# напрямую (rotate-script знает URL).
router = APIRouter(
    prefix="/internal/reencrypt_outbox",
    include_in_schema=False,
    dependencies=[Depends(require_internal_caller)],
)


_INTERNAL_RESPONSES: dict[int | str, dict] = {
    401: {"description": "INTERNAL_AUTH_REQUIRED — bearer отсутствует / не совпал."},
    500: {"description": "Внутренняя ошибка процессора."},
}


@router.post(
    "/seed",
    response_model=ReencryptOutboxSeedResponse,
    responses=_INTERNAL_RESPONSES,
)
async def seed(
    request: Request,
    db: AsyncSession = Depends(get_db),
    target_version: int | None = Body(
        default=None,
        embed=True,
        description=(
            "Версия мастер-ключа, под которую публикуются задачи. None — "
            "берётся `SECRET_ENCRYPTION_KEY_VERSION` из env'а."
        ),
    ),
) -> ReencryptOutboxSeedResponse:
    """Просканировать credentials и опубликовать pending outbox-row'ы.

    Идемпотентно: partial UNIQUE по `credential_id WHERE status='pending'`
    не пустит дубль для credential'ы с активной задачей. После `done` /
    `error` следующий seed добавит новую row нормально.

    Audit: `secrets.reencrypt_seed` (INFO).
    """
    try:
        data = await reencrypt_outbox_service.seed_outbox(
            db, target_version=target_version
        )
    except AppException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("reencrypt_outbox_seed_crashed")
        raise AppException(
            http_status=500,
            error_code="REENCRYPT_OUTBOX_SEED_FAILED",
            message=f"seed_outbox crashed: {type(exc).__name__}",
        ) from exc

    caller = getattr(request.state, "caller", None)
    audit_service.emit(
        "secrets.reencrypt_seed",
        target_id=None,
        target_type="reencrypt_outbox",
        status="success",
        allowed=True,
        details={
            "inserted": data["inserted"],
            "scanned": data["scanned"],
            "active_version": data["active_version"],
            "caller": caller,
        },
    )
    return ReencryptOutboxSeedResponse(**data)


@router.post(
    "/process",
    response_model=ReencryptOutboxProcessResponse,
    responses=_INTERNAL_RESPONSES,
)
async def process(
    request: Request,
    db: AsyncSession = Depends(get_db),
    batch_size: int = Query(default=100, ge=1, le=1000),
) -> ReencryptOutboxProcessResponse:
    """Обработать до `batch_size` pending row'ов.

    Каждая row перешифровывается атомарно: decrypt legacy → encrypt active
    → CAS на credentials.secret_encrypted. Race с lazy-путём — норма, row
    закрывается done без ошибки.

    Audit: `secrets.reencrypt_process` (status=success / warning, если
    были errors).
    """
    try:
        data = await reencrypt_outbox_service.process_batch(
            db, batch_size=batch_size
        )
    except AppException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("reencrypt_outbox_process_crashed")
        raise AppException(
            http_status=500,
            error_code="REENCRYPT_OUTBOX_PROCESS_FAILED",
            message=f"process_batch crashed: {type(exc).__name__}",
        ) from exc

    caller = getattr(request.state, "caller", None)
    is_total_failure = data["errors"] > 0 and data["processed"] == 0
    audit_status = "failure" if is_total_failure else "success"
    audit_service.emit(
        "secrets.reencrypt_process",
        target_id=None,
        target_type="reencrypt_outbox",
        status=audit_status,
        allowed=True,
        details={
            "processed": data["processed"],
            "errors": data["errors"],
            "batch_size": batch_size,
            "caller": caller,
            # Sample упавших row'ов — для триажа в SIEM. Полный счётчик в
            # `errors`, здесь — первые 50 для bound'ности audit-payload'а.
            "failed_sample": data["failed"][:50],
        },
    )
    return ReencryptOutboxProcessResponse(**data)


@router.get(
    "/status",
    response_model=ReencryptOutboxStatus,
    responses=_INTERNAL_RESPONSES,
)
async def status(
    db: AsyncSession = Depends(get_db),
) -> ReencryptOutboxStatus:
    """Сводка `{pending, done, error, total}`.

    Используется monitoring'ом и шагом `--finalize` rotate-скрипта. Когда
    `pending == 0` и `error == 0` (и `migration_status.remaining_legacy ==
    0`) — старый мастер-ключ можно дропать.
    """
    data = await reencrypt_outbox_service.migration_status(db)
    return ReencryptOutboxStatus(**data)

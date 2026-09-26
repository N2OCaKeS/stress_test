"""Приёмник callback'а «настройка стенда без restore» от server_service.

Путь — `/internal/stand-setup/{stand_setup_request_id}/completed`, без
`/api/testing/v1` (как callback prepare-for-test). Операцию запускает
очередь между шагами многоступенчатого теста (,
`services/queue_steps.py`): успех возвращает item в `ready` (воркер
заберёт следующий шаг), провал — провал item'а. Callback без ждущего
item'а (повтор, item сняли) — 200 и только аудит.
"""

from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.db import get_db
from src.dependencies.internal_auth import require_caller_identity
from src.schemas.common import OkResponse
from src.schemas.queue import StandSetupCompletedCallback
from src.services import audit_service, queue_steps

router = APIRouter(prefix="/internal/stand-setup", include_in_schema=False)


@router.post("/{stand_setup_request_id}/completed", response_model=OkResponse)
async def stand_setup_completed(
    body: StandSetupCompletedCallback,
    stand_setup_request_id: str = Path(description="`ssr_<hex>` из 202-ответа server_service."),
    _caller: None = Depends(require_caller_identity("server_service")),
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """server_service сообщает исход настройки стенда без restore."""
    audit_service.emit(
        "stand_setup.completed",
        target_id=body.correlation_id, target_type="queue_item",
        status="success" if body.succeeded else "failure", allowed=True,
        details={
            "stand_setup_request_id": stand_setup_request_id,
            "succeeded": body.succeeded, "failed_step": body.failed_step, "error": body.error,
        },
    )
    await queue_steps.handle_stand_setup_completed(db, stand_setup_request_id, body)
    return OkResponse()

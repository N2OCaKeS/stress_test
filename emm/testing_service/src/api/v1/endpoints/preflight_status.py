"""Статус ожидания внешних сервисов для левой панели UI."""

from fastapi import APIRouter

from src.dependencies.auth import CurrentUserIdentity
from src.schemas.queue import PreflightStatusResponse
from src.services import preflight_status as preflight_status_svc

router = APIRouter(prefix="/preflight")


@router.get(
    "/status",
    response_model=PreflightStatusResponse,
    summary="Ждут ли тесты отдела внешние сервисы",
    description=(
        "`waiting` — хоть один тест отдела пользователя стоит перед запуском и ждёт "
        "недоступные внешние сервисы (preflight): UI показывает «тестирование "
        "приостановлено». Пользователь без отдела всегда получает `ok`."
    ),
)
async def get_preflight_status(identity: CurrentUserIdentity) -> PreflightStatusResponse:
    return PreflightStatusResponse(**await preflight_status_svc.get_status(identity))

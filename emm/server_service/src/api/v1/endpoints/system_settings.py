"""Платформенные настройки проб статуса под `account_admin` + internal-read.

Пробы статуса (reachability = ping+ssh, power = ipmi/domstate) снимает
server_worker. Их частота и вкл/выкл живут в БД (а не в env воркера), чтобы
одинаково работать в docker и k8s: воркер читает настройки через
internal-эндпоинт, а редактирует их account_admin через `/settings/probes`.

Как и `/management-user-config` и `/admin/encryption/*`, это сервисная
настройка уровня платформы, а не бизнес-данные отдела. `platform_admin_guard`
пропускает `/settings/*` по allowlist'у (`_is_settings_path`), позитивную
проверку роли делает `require_account_admin` (`AccountAdminIdentity`).

Internal-эндпоинт (`/internal/settings/probes`) скрыт из публичного OpenAPI и
авторизуется той же матрицей `entity_permissions`, что и sweep-callback'и
воркера: `(server, *, prepare_callback)` (роль worker_bot).
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import AccountAdminIdentity, CurrentIdentity
from src.dependencies.db import get_db
from src.schemas.probe_settings import ProbeSettingsResponse, ProbeSettingsUpdate
from src.services import probe_settings as svc

router = APIRouter(prefix="/settings", tags=["system-settings"])


@router.get(
    "/probes",
    response_model=ProbeSettingsResponse,
    summary="Текущие настройки проб статуса",
    responses={
        200: {"description": "Настройки (дефолт, если строки ещё нет)."},
        401: {"description": "ACCESS_TOKEN_MISSING / ACCESS_TOKEN_INVALID / USER_BANNED."},
        403: {"description": "ACCOUNT_ADMIN_REQUIRED — нужна платформенная роль account_admin."},
    },
)
async def get_probe_settings(
    identity: AccountAdminIdentity,
    db: AsyncSession = Depends(get_db),
) -> ProbeSettingsResponse:
    """Read-only текущие настройки проб. Нет строки → дефолты."""
    return await svc.get_probe_settings(db)


@router.put(
    "/probes",
    response_model=ProbeSettingsResponse,
    summary="Обновить настройки проб статуса",
    description=(
        "Частичное обновление: любое поле можно опустить — тогда текущее "
        "значение сохраняется. Интервалы имеют нижние границы; интервал "
        "питания не может быть короче интервала доступности."
    ),
    responses={
        200: {"description": "Настройки обновлены."},
        400: {"description": "POWER_INTERVAL_BELOW_REACHABILITY — power < reachability."},
        401: {"description": "ACCESS_TOKEN_MISSING / ACCESS_TOKEN_INVALID / USER_BANNED."},
        403: {"description": "ACCOUNT_ADMIN_REQUIRED."},
        422: {"description": "Интервал ниже минимальной границы."},
    },
)
async def put_probe_settings(
    payload: ProbeSettingsUpdate,
    identity: AccountAdminIdentity,
    db: AsyncSession = Depends(get_db),
) -> ProbeSettingsResponse:
    """Upsert настроек проб. Audit: `settings.probes_updated`."""
    return await svc.update_probe_settings(db, payload)


# ── Internal: worker читает настройки проб ──────────────────────────────────

internal_router = APIRouter(prefix="/internal/settings", include_in_schema=False)


@internal_router.get(
    "/probes",
    response_model=ProbeSettingsResponse,
    responses={
        403: {"description": "PERMISSION_DENIED — нет `(server, prepare_callback)` в матрице."},
    },
)
async def get_probe_settings_internal(
    identity: CurrentIdentity,
    db: AsyncSession = Depends(get_db),
) -> ProbeSettingsResponse:
    """Отдать настройки проб для server_worker (частота ping/ssh/ipmi-опроса).

    Доступ: `(server, *, prepare_callback)` — тот же callback-грант worker_bot'а,
    что у sweep-эндпоинтов. Прогон платформенный, X-Target-Department-Id не
    требуется.
    """
    return await svc.get_probe_settings_for_worker(db, identity)

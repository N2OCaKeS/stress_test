"""Use cases для настроек проб статуса — платформенный singleton.

Одна строка (`SINGLETON_ID`) на всю платформу. Чтение отдаёт текущий конфиг,
а при отсутствии строки — дефолты. PUT делает upsert с частичным слиянием и
проверяет порядок интервалов (power >= reachability). Read для воркера —
отдельная функция под тем же callback-грантом, что sweep-эндпоинты.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import Action, EntityType
from src.core.exceptions import AuthorizationError, BadRequestError
from src.models.probe_settings import (
    DEFAULT_POWER_INTERVAL_SECONDS,
    DEFAULT_REACHABILITY_INTERVAL_SECONDS,
    SINGLETON_ID,
    ProbeSettings,
)
from src.schemas.identity import IdentityContext
from src.schemas.probe_settings import ProbeSettingsResponse, ProbeSettingsUpdate
from src.services import audit_service, permissions


def _to_response(row: ProbeSettings) -> ProbeSettingsResponse:
    return ProbeSettingsResponse(
        reachability_probe_interval_seconds=row.reachability_probe_interval_seconds,
        power_probe_interval_seconds=row.power_probe_interval_seconds,
        reachability_probe_enabled=row.reachability_probe_enabled,
        power_probe_enabled=row.power_probe_enabled,
    )


def _defaults() -> ProbeSettingsResponse:
    return ProbeSettingsResponse(
        reachability_probe_interval_seconds=DEFAULT_REACHABILITY_INTERVAL_SECONDS,
        power_probe_interval_seconds=DEFAULT_POWER_INTERVAL_SECONDS,
        reachability_probe_enabled=True,
        power_probe_enabled=True,
    )


async def _get_row(db: AsyncSession) -> ProbeSettings | None:
    return await db.get(ProbeSettings, SINGLETON_ID)


async def get_probe_settings(db: AsyncSession) -> ProbeSettingsResponse:
    """Текущие настройки проб. Нет строки → дефолты."""
    row = await _get_row(db)
    if row is None:
        return _defaults()
    return _to_response(row)


async def update_probe_settings(
    db: AsyncSession,
    payload: ProbeSettingsUpdate,
) -> ProbeSettingsResponse:
    """Upsert настроек проб (частичное слияние) + проверка порядка интервалов.

    Неприсланные поля сохраняют текущее значение. После слияния проверяем, что
    интервал питания не короче интервала доступности (power >= reachability) —
    иначе 400. Аудит `settings.probes_updated`.
    """
    row = await _get_row(db)
    if row is None:
        defaults = _defaults()
        row = ProbeSettings(
            id=SINGLETON_ID,
            reachability_probe_interval_seconds=defaults.reachability_probe_interval_seconds,
            power_probe_interval_seconds=defaults.power_probe_interval_seconds,
            reachability_probe_enabled=defaults.reachability_probe_enabled,
            power_probe_enabled=defaults.power_probe_enabled,
        )
        db.add(row)

    if payload.reachability_probe_interval_seconds is not None:
        row.reachability_probe_interval_seconds = payload.reachability_probe_interval_seconds
    if payload.power_probe_interval_seconds is not None:
        row.power_probe_interval_seconds = payload.power_probe_interval_seconds
    if payload.reachability_probe_enabled is not None:
        row.reachability_probe_enabled = payload.reachability_probe_enabled
    if payload.power_probe_enabled is not None:
        row.power_probe_enabled = payload.power_probe_enabled

    if row.power_probe_interval_seconds < row.reachability_probe_interval_seconds:
        raise BadRequestError(
            error_code="POWER_INTERVAL_BELOW_REACHABILITY",
            message=(
                "power_probe_interval_seconds must be greater than or equal to "
                "reachability_probe_interval_seconds"
            ),
            details={
                "reachability_probe_interval_seconds": row.reachability_probe_interval_seconds,
                "power_probe_interval_seconds": row.power_probe_interval_seconds,
            },
        )

    await db.commit()
    await db.refresh(row)

    audit_service.emit(
        "settings.probes_updated",
        target_id=SINGLETON_ID,
        target_type="probe_settings",
        status="success",
        allowed=True,
        details={
            "reachability_probe_interval_seconds": row.reachability_probe_interval_seconds,
            "power_probe_interval_seconds": row.power_probe_interval_seconds,
            "reachability_probe_enabled": row.reachability_probe_enabled,
            "power_probe_enabled": row.power_probe_enabled,
        },
    )

    return _to_response(row)


async def get_probe_settings_for_worker(
    db: AsyncSession,
    identity: IdentityContext,
) -> ProbeSettingsResponse:
    """Read настроек проб для server_worker (internal-эндпоинт).

    Право: `(server, *, prepare_callback)` — тот же глобальный callback-грант
    worker_bot'а, что у sweep-эндпоинтов. Прогон платформенный, не привязан к
    отделу, поэтому X-Target-Department-Id здесь не требуется.
    """
    try:
        await permissions.require_action(
            db, identity, EntityType.SERVER, Action.PREPARE_CALLBACK,
        )
    except AuthorizationError:
        audit_service.emit(
            "settings.probes_updated",
            target_id=SINGLETON_ID,
            target_type="probe_settings",
            status="denied", allowed=False,
            details={"reason": "permission_denied", "op": "worker_read"},
        )
        raise
    return await get_probe_settings(db)

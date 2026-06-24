"""Use cases для конфига управляющей учётки — платформенный singleton.

Одна строка (`SINGLETON_ID`) на всю платформу. Чтение всегда отдаёт полный
конфиг по всем четырём режимам: отсутствующие в БД режимы дополняются
дефолтами (пустые группы/команды). PUT заменяет/обновляет строку, детектит
смену `login` и возвращает её наверх флагом — фан-аут rename на сервера здесь
НЕ выполняется (это отдельная фаза).
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import ManagementMode
from src.models.management_user_config import (
    DEFAULT_LOGIN,
    SINGLETON_ID,
    ManagementUserConfig,
)
from src.schemas.management_user_config import (
    ManagementModeConfig,
    ManagementUserConfigResponse,
    ManagementUserConfigUpdate,
)
from src.services import audit_service

logger = logging.getLogger(__name__)


def _default_modes() -> dict[ManagementMode, ManagementModeConfig]:
    """Полный набор режимов с пустыми настройками."""
    return {mode: ManagementModeConfig() for mode in ManagementMode}


def _modes_from_row(stored: dict) -> dict[ManagementMode, ManagementModeConfig]:
    """Слить хранимый JSONB поверх дефолтов: каждый режим всегда присутствует."""
    result = _default_modes()
    for mode in ManagementMode:
        raw = stored.get(mode.value)
        if isinstance(raw, dict):
            result[mode] = ManagementModeConfig.model_validate(raw)
    return result


async def _get_row(db: AsyncSession) -> ManagementUserConfig | None:
    return await db.get(ManagementUserConfig, SINGLETON_ID)


async def get_config(db: AsyncSession) -> ManagementUserConfigResponse:
    """Текущий конфиг. Нет строки → разумный дефолт (login=dbos, пустые режимы)."""
    row = await _get_row(db)
    if row is None:
        return ManagementUserConfigResponse(
            login=DEFAULT_LOGIN,
            modes=_default_modes(),
        )
    return ManagementUserConfigResponse(
        login=row.login,
        modes=_modes_from_row(row.modes or {}),
    )


async def update_config(
    db: AsyncSession,
    payload: ManagementUserConfigUpdate,
) -> ManagementUserConfigResponse:
    """Заменить/обновить конфиг. Возвращает результат с флагом login_changed.

    Семантика: `login` без значения — оставляем текущий; присланные режимы в
    `modes` заменяются целиком, неприсланные остаются. Смена `login` только
    фиксируется (значение + флаг наверх) — rename на серверах здесь не делается.
    """
    row = await _get_row(db)
    if row is None:
        row = ManagementUserConfig(id=SINGLETON_ID, login=DEFAULT_LOGIN, modes={})
        db.add(row)

    previous_login = row.login
    login_changed = False
    if payload.login is not None and payload.login != row.login:
        row.login = payload.login
        login_changed = True

    if payload.modes is not None:
        merged = dict(row.modes or {})
        for mode, cfg in payload.modes.items():
            merged[mode.value] = cfg.model_dump(mode="json")
        row.modes = merged
        # JSONB-словарь переприсвоен целиком — SQLAlchemy отследит изменение.

    await db.commit()
    await db.refresh(row)

    audit_service.emit(
        "management_user_config.update",
        target_id=SINGLETON_ID,
        target_type="management_user_config",
        status="success",
        allowed=True,
        details={
            "login_changed": login_changed,
            "previous_login": previous_login if login_changed else None,
            "new_login": row.login,
            "modes_updated": (
                sorted(m.value for m in payload.modes) if payload.modes else []
            ),
        },
    )

    return ManagementUserConfigResponse(
        login=row.login,
        modes=_modes_from_row(row.modes or {}),
        login_changed=login_changed,
        previous_login=previous_login if login_changed else None,
    )

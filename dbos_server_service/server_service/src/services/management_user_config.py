"""Use cases для конфига управляющей учётки — платформенный singleton.

Одна строка (`SINGLETON_ID`) на всю платформу. Чтение всегда отдаёт полный
конфиг по всем четырём режимам: отсутствующие в БД режимы дополняются
дефолтами (пустые группы/команды). PUT заменяет/обновляет строку, детектит
смену `login`/`modes` и возвращает её наверх флагами.

При изменении `modes`/`login` PUT-эндпоинт фан-аутит `management_user_sync`
high-priority задачей на все подготовленные (`is_managed`) серверы — это
re-bootstrap управляющей учётки (группы + extra_create_commands + ключ),
идемпотентный и НЕдеструктивный. Смена `login` сам rename/cutover НЕ
выполняет — только помечается `rename_pending` (фаза C, отдельный хендлер).
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.constants import ManagementMode
from src.core.exceptions import ConflictError, ServiceUnavailableError
from src.models.management_user_config import (
    DEFAULT_LOGIN,
    SINGLETON_ID,
    ManagementUserConfig,
)
from src.repositories import server as server_repo
from src.schemas.management_user_config import (
    ManagementModeConfig,
    ManagementUserConfigResponse,
    ManagementUserConfigUpdate,
)
from src.services import audit_service, worker_client

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

    # Сверка по-режимно: считаем `modes` изменёнными, только если присланный
    # режим реально отличается от хранимого. PUT с теми же значениями не должен
    # запускать фан-аут на все серверы (идемпотентный no-op запроса).
    modes_changed = False
    if payload.modes is not None:
        merged = dict(row.modes or {})
        for mode, cfg in payload.modes.items():
            new_value = cfg.model_dump(mode="json")
            if merged.get(mode.value) != new_value:
                modes_changed = True
            merged[mode.value] = new_value
        if modes_changed:
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
            "modes_changed": modes_changed,
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
        modes_changed=modes_changed,
    )


def _sync_payload(server, config: ManagementUserConfigResponse) -> dict:
    """Payload одной `management_user_sync` задачи на конкретный сервер.

    Несёт текущий конфиг (login + пер-режимные группы/команды) и SSH-адресацию
    сервера. Воркер на боксе детектит редакцию и выбирает группы/команды нужного
    режима — ровно как `server.prepare` (re-bootstrap). Управляющий публичный
    ключ — per-server (#3): берётся из `servers.mgmt_ssh_public_key` и едет в
    payload, воркер кладёт его в authorized_keys при re-bootstrap'е.
    """
    management_modes = {
        mode.value: cfg.model_dump(mode="json")
        for mode, cfg in config.modes.items()
    }
    return {
        "server_id": server.id,
        "target_department_id": server.department_id,
        "host": server.hostname,
        "ssh_port": server.ssh_port,
        "is_managed": server.is_managed,
        "management_user": server.management_user,
        "management_login": config.login,
        "management_modes": management_modes,
        "management_public_key": server.mgmt_ssh_public_key,
    }


async def fanout_management_user_sync(
    db: AsyncSession,
    *,
    config: ManagementUserConfigResponse,
    actor_id: str | None,
    request_id: str | None = None,
    rename_pending: bool = False,
) -> dict:
    """Разослать `management_user_sync` high-priority на все managed-серверы.

    Зовётся PUT-эндпоинтом после успешного `update_config`, если изменились
    `modes` и/или `login`. Конфиг управляющей учётки — платформенный singleton,
    поэтому фан-аут бьёт по всем подготовленным (`is_managed`) серверам
    платформы независимо от отдела. Каждая задача — НЕдеструктивный re-bootstrap
    (группы + extra_create_commands + ключ, идемпотентно), с
    `priority=TASK_PRIORITY_HIGH`, чтобы реально обогнать normal-очередь.

    Cap аналогичен `fanout_update_on_host`: при превышении
    `management_user_sync_fanout_max` режем хвост и эмитим truncated-audit —
    отрезанные серверы выровняются следующим PUT/prepare.

    Best-effort: недоступность worker'а / idempotent-conflict на отдельном
    сервере уходит в `skipped` и не валит остальные диспатчи. `rename_pending`
    кладём в payload каждой задачи и в сводку — сам rename здесь НЕ делается
    (фаза C); хендлер синхронизирует только группы/команды/ключ.

    Возвращает `{dispatched, skipped, truncated, rename_pending}`.
    """
    cap = get_settings().management_user_sync_fanout_max
    servers = await server_repo.list_managed(db, limit=cap)
    truncated = 0
    total_managed = await server_repo.count_managed(db)
    if total_managed > cap:
        truncated = total_managed - cap
        audit_service.emit(
            "management_user_sync_fanout.truncated",
            target_id=SINGLETON_ID,
            target_type="management_user_config",
            status="warning",
            allowed=True,
            details={
                "total_managed": total_managed,
                "cap": cap,
                "truncated_count": truncated,
            },
        )

    audit_action = "management_user_config.sync"
    dispatched: list[dict] = []
    skipped: list[dict] = []
    for server in servers:
        # Per-server управляющий ключ (#3) обязателен для re-bootstrap'а: воркер
        # кладёт его в authorized_keys и без него падает SSH_INVALID_ARG.
        # Managed-сервер без mgmt-кред — аномалия (prepare всегда их генерит);
        # не диспатчим заведомо обречённую задачу, уводим в skipped.
        if server.mgmt_ssh_public_key is None:
            audit_service.emit(
                audit_action, target_id=server.id, target_type="server",
                status="failure", allowed=True,
                details={
                    "reason": "no_mgmt_creds",
                    "task_kind": "management_user_sync",
                    "server_id": server.id,
                    "source": "config_fanout",
                    "department_id": server.department_id,
                },
            )
            skipped.append({"server_id": server.id, "reason": "no_mgmt_creds"})
            continue
        payload = _sync_payload(server, config)
        payload["rename_pending"] = rename_pending
        try:
            task_id, idempotent_hit = await worker_client.dispatch_task_with_hit(
                db=db,
                task_kind="management_user_sync",
                target_server_id=server.id,
                payload=payload,
                created_by=actor_id,
                request_id=request_id,
                priority=worker_client.TASK_PRIORITY_HIGH,
            )
            await db.commit()
        except (ConflictError, ServiceUnavailableError) as exc:
            reason = (
                "idempotent_conflict"
                if isinstance(exc, ConflictError)
                else "worker_unreachable"
            )
            audit_service.emit(
                audit_action, target_id=server.id, target_type="server",
                status="failure", allowed=True,
                details={
                    "reason": reason,
                    "task_kind": "management_user_sync",
                    "server_id": server.id,
                    "source": "config_fanout",
                    "department_id": server.department_id,
                },
            )
            skipped.append({"server_id": server.id, "reason": reason})
            continue
        audit_service.emit(
            audit_action, target_id=server.id, target_type="server",
            status="success", allowed=True,
            details={
                "task_id": task_id,
                "task_kind": "management_user_sync",
                "server_id": server.id,
                "source": "config_fanout",
                "management_login": config.login,
                "rename_pending": rename_pending,
                "department_id": server.department_id,
                "idempotent_hit": idempotent_hit,
            },
        )
        dispatched.append({"server_id": server.id, "task_id": task_id})

    return {
        "dispatched": dispatched,
        "skipped": skipped,
        "truncated": truncated,
        "rename_pending": rename_pending,
    }

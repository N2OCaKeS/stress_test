"""Use cases для настроек SSH-доступа к хосту (host-service control) — per-department.

`HostServicesSettings` — одна строка на отдел (`department_id` — PK) с
host/port/user и зашифрованным приватным SSH-ключом. `HostServiceUnit` —
список systemd-юнитов, которые отдел решил выставить (много строк на отдел,
уникальность по `(department_id, unit_name)`).

`department_id` нигде в этом модуле не берётся откуда-либо, кроме явного
параметра функции — вызывающий endpoint обязан резолвить его из
`identity.department_id` и никогда не принимать его от caller'а
(cross-department peek — как раз то, чего быть не должно, см.
`obsidian/reports/` этой волны).
"""

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ConflictError, DomainValidationError, NotFoundError, ServiceUnavailableError
from src.models.host_service_unit import HostServiceUnit
from src.models.host_services_settings import HostServicesSettings
from src.schemas.host_services_settings import (
    HostServicesSettingsResponse,
    HostServicesSettingsUpdate,
    HostServiceUnitCreate,
    HostServiceUnitListResponse,
    HostServiceUnitResponse,
    HostServiceUnitUpdate,
)
from src.services import audit_service, secrets_service
from src.utils.ids import host_service_unit_id

# Юнит-имя едет в shell-команду по SSH (`systemctl {action} {unit_name}.service`)
# — тот же charset, что и в `emm-host-service-guard.sh` на хосте. Двойная
# защита: даже если это приложение допустит опечатку, хостовый guard всё
# равно её отобьёт, но проверка здесь — первая линия и понятная 422 вместо
# невнятного отказа от guard'а на другом конце SSH.
_UNIT_NAME_RE = re.compile(r"^[a-zA-Z0-9_.@-]+$")


def _normalize_unit_name(raw: str) -> str:
    name = raw.strip()
    if name.endswith(".service"):
        name = name[: -len(".service")]
    if not name or not _UNIT_NAME_RE.match(name):
        raise DomainValidationError(
            error_code="HOST_SERVICE_UNIT_NAME_INVALID",
            message="Unit name must match ^[a-zA-Z0-9_.@-]+$ (without .service)",
            details={"unit_name": raw},
        )
    return name


# ── HostServicesSettings (per-department SSH config) ────────────────────────


def _to_response(row: HostServicesSettings) -> HostServicesSettingsResponse:
    return HostServicesSettingsResponse(
        configured=bool(row.ssh_host and row.ssh_user and row.ssh_private_key_encrypted),
        ssh_host=row.ssh_host,
        ssh_port=row.ssh_port,
        ssh_user=row.ssh_user,
        private_key_is_set=bool(row.ssh_private_key_encrypted),
    )


def _defaults() -> HostServicesSettingsResponse:
    return HostServicesSettingsResponse(
        configured=False, ssh_host=None, ssh_port=22, ssh_user=None, private_key_is_set=False,
    )


async def _get_row(db: AsyncSession, department_id: str) -> HostServicesSettings | None:
    return await db.get(HostServicesSettings, department_id)


async def get_settings(db: AsyncSession, department_id: str) -> HostServicesSettingsResponse:
    """Текущие настройки отдела. Нет строки → дефолты (ничего не настроено)."""
    row = await _get_row(db, department_id)
    if row is None:
        return _defaults()
    return _to_response(row)


async def update_settings(
    db: AsyncSession,
    department_id: str,
    payload: HostServicesSettingsUpdate,
) -> HostServicesSettingsResponse:
    """Upsert настроек отдела (частичное слияние) + шифрование ключа на входе.

    Неприсланные поля сохраняют текущее значение. Пустой `ssh_host`/`ssh_user`
    трактуется как явная очистка. Ключ: `ssh_private_key` задан → шифруется и
    заменяет текущий; `clear_private_key=True` (и `ssh_private_key` не задан) →
    стирает сохранённый ключ; иначе — не трогаем. Аудит `settings.host_services_updated`
    (сам ключ никогда не логируется, только факт `private_key_set`).
    """
    row = await _get_row(db, department_id)
    if row is None:
        row = HostServicesSettings(
            department_id=department_id, ssh_host=None, ssh_port=22, ssh_user=None, ssh_private_key_encrypted=None,
        )
        db.add(row)

    if payload.ssh_host is not None:
        stripped = payload.ssh_host.strip()
        row.ssh_host = stripped or None
    if payload.ssh_port is not None:
        row.ssh_port = payload.ssh_port
    if payload.ssh_user is not None:
        stripped = payload.ssh_user.strip()
        row.ssh_user = stripped or None

    if payload.ssh_private_key:
        row.ssh_private_key_encrypted = secrets_service.encrypt(
            payload.ssh_private_key, aad=secrets_service.aad_for_host_control_ssh_key(department_id)
        )
    elif payload.clear_private_key:
        row.ssh_private_key_encrypted = None

    await db.commit()
    await db.refresh(row)

    audit_service.emit(
        "settings.host_services_updated",
        target_id=department_id,
        target_type="host_services_settings",
        status="success",
        allowed=True,
        details={
            "department_id": department_id,
            "ssh_host": row.ssh_host,
            "ssh_port": row.ssh_port,
            "ssh_user": row.ssh_user,
            "private_key_set": bool(row.ssh_private_key_encrypted),
        },
    )

    return _to_response(row)


async def get_decrypted_private_key(db: AsyncSession, department_id: str) -> str:
    """Расшифрованный приватный SSH-ключ хоста отдела для `host_control`.

    503 `HOST_SERVICES_NOT_CONFIGURED`, если строки нет или не заполнены все
    три поля (host/user/ключ) — тот же контракт недоступности, что у
    `acs_settings.get_acs_credentials` (`ACS_DISABLED`).
    """
    row = await _get_row(db, department_id)
    if row is None or not row.ssh_host or not row.ssh_user or not row.ssh_private_key_encrypted:
        raise ServiceUnavailableError(
            error_code="HOST_SERVICES_NOT_CONFIGURED",
            message="Host services SSH access is not configured",
        )

    result = secrets_service.decrypt_with_meta(
        row.ssh_private_key_encrypted, aad=secrets_service.aad_for_host_control_ssh_key(department_id)
    )
    if result.needs_reencrypt:
        await secrets_service.lazy_reencrypt_owner_column(
            db,
            table="host_services_settings",
            column="ssh_private_key_encrypted",
            row_id=department_id,
            old_blob=row.ssh_private_key_encrypted,
            plaintext=result.plaintext,
            aad=secrets_service.aad_for_host_control_ssh_key(department_id),
        )
    return result.plaintext


# ── HostServiceUnit (per-department unit list) ───────────────────────────────


def _unit_to_response(row: HostServiceUnit) -> HostServiceUnitResponse:
    return HostServiceUnitResponse(
        id=row.id, unit_name=row.unit_name, label=row.label, created_at=row.created_at, created_by=row.created_by,
    )


async def list_units(db: AsyncSession, department_id: str) -> HostServiceUnitListResponse:
    """Список юнитов отдела, по имени для стабильного порядка в UI."""
    stmt = (
        select(HostServiceUnit)
        .where(HostServiceUnit.department_id == department_id)
        .order_by(HostServiceUnit.unit_name)
    )
    rows = list((await db.execute(stmt)).scalars())
    return HostServiceUnitListResponse(items=[_unit_to_response(r) for r in rows])


async def create_unit(
    db: AsyncSession,
    department_id: str,
    payload: HostServiceUnitCreate,
    created_by: str | None,
) -> HostServiceUnitResponse:
    """Добавить юнит в список отдела. 409 `HOST_SERVICE_UNIT_ALREADY_EXISTS` на дубликат."""
    unit_name = _normalize_unit_name(payload.unit_name)
    label = (payload.label or "").strip() or unit_name

    existing = (
        await db.execute(
            select(HostServiceUnit).where(
                HostServiceUnit.department_id == department_id,
                HostServiceUnit.unit_name == unit_name,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError(
            error_code="HOST_SERVICE_UNIT_ALREADY_EXISTS",
            message=f"Unit '{unit_name}' is already in this department's list",
            details={"unit_name": unit_name},
        )

    row = HostServiceUnit(
        id=host_service_unit_id(),
        department_id=department_id,
        unit_name=unit_name,
        label=label,
        created_by=created_by,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    audit_service.emit(
        "settings.host_service_unit_added",
        target_id=row.id,
        target_type="host_service_unit",
        status="success",
        allowed=True,
        details={"department_id": department_id, "unit_name": unit_name, "label": label},
    )

    return _unit_to_response(row)


async def _get_own_unit(db: AsyncSession, department_id: str, unit_id: str) -> HostServiceUnit:
    """Строка юнита, если она принадлежит `department_id`.

    404 `HOST_SERVICE_UNIT_NOT_FOUND` одинаково для "не существует" и
    "существует, но принадлежит другому отделу" — не палим существование
    чужого юнита другим кодом ошибки (тот же приём, что и cross-department
    404 у серверов).
    """
    row = await db.get(HostServiceUnit, unit_id)
    if row is None or row.department_id != department_id:
        raise NotFoundError(
            error_code="HOST_SERVICE_UNIT_NOT_FOUND",
            message="Host service unit not found",
            details={"unit_id": unit_id},
        )
    return row


async def rename_unit(
    db: AsyncSession,
    department_id: str,
    unit_id: str,
    payload: HostServiceUnitUpdate,
) -> HostServiceUnitResponse:
    """Переименовать label юнита. `unit_name` не редактируется (см. схему)."""
    row = await _get_own_unit(db, department_id, unit_id)
    row.label = payload.label.strip() or row.unit_name
    await db.commit()
    await db.refresh(row)
    return _unit_to_response(row)


async def delete_unit(db: AsyncSession, department_id: str, unit_id: str) -> None:
    """Убрать юнит из списка отдела. 404, если не свой (см. `_get_own_unit`)."""
    row = await _get_own_unit(db, department_id, unit_id)
    unit_name = row.unit_name
    await db.delete(row)
    await db.commit()

    audit_service.emit(
        "settings.host_service_unit_removed",
        target_id=unit_id,
        target_type="host_service_unit",
        status="success",
        allowed=True,
        details={"department_id": department_id, "unit_name": unit_name},
    )

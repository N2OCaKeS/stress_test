"""Use cases для NOPASSWD sudo настройки тестовых учёток — per-department opt-in.

`AccountNopasswdSudoSettings` — одна строка на отдел, дефолт (нет строки) —
`is_enabled=False`, то есть текущее поведение (has_sudo даёт группу `sudo`,
пароль на sudo по-прежнему спрашивается) сохраняется, пока отдел явно не
включит флаг.

Два входа:

* self-service (`get_own`/`update_own`) — department_admin/admin своего
  отдела читает и правит ровно свою строку;
* оверсайт (`list_all`/`update_many`) — account_admin видит и правит флаг
  любого отдела, тот же паттерн, что `acs_settings.list_acs_department_access`.

`is_enabled_for_department` — узкая точечная проверка для payload-билдеров
worker-dispatch'а (`worker_dispatch._build_account_task_payload` и др.):
не тянет полный список кандидатов, только факт по одному `department_id`.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.account_nopasswd_sudo_settings import AccountNopasswdSudoSettings
from src.repositories import server as server_repo
from src.schemas.account_nopasswd_sudo_settings import (
    AccountNopasswdSudoSettingsBatchUpdate,
    AccountNopasswdSudoSettingsItem,
    AccountNopasswdSudoSettingsListResponse,
    AccountNopasswdSudoSettingsResponse,
    AccountNopasswdSudoSettingsUpdate,
)
from src.schemas.identity import IdentityContext
from src.services import audit_service


async def _get_row(db: AsyncSession, department_id: str) -> AccountNopasswdSudoSettings | None:
    return await db.get(AccountNopasswdSudoSettings, department_id)


async def is_enabled_for_department(db: AsyncSession, department_id: str | None) -> bool:
    """True iff отделу явно включён NOPASSWD sudo для sudo-аккаунтов.

    `department_id=None` (не должно происходить для managed-сервера/ВМ, но
    caller'ы иногда передают Optional) — трактуется как «нет отдела, доступа
    нет», без похода в БД. Отсутствие строки — как и выключенный флаг —
    «доступа нет» (дефолт closed, симметрично `acs_settings.is_department_acs_enabled`).
    """
    if not department_id:
        return False
    row = await _get_row(db, department_id)
    return bool(row is not None and row.is_enabled)


async def get_own(db: AsyncSession, department_id: str) -> AccountNopasswdSudoSettingsResponse:
    """Текущий флаг своего отдела. Нет строки → дефолт `is_enabled=False`."""
    row = await _get_row(db, department_id)
    if row is None:
        return AccountNopasswdSudoSettingsResponse(
            department_id=department_id, is_enabled=False, updated_at=None,
        )
    return AccountNopasswdSudoSettingsResponse(
        department_id=department_id, is_enabled=row.is_enabled, updated_at=row.updated_at,
    )


async def update_own(
    db: AsyncSession,
    department_id: str,
    payload: AccountNopasswdSudoSettingsUpdate,
    identity: IdentityContext,
) -> AccountNopasswdSudoSettingsResponse:
    """Upsert флага своего отдела. Аудит: `settings.account_nopasswd_sudo_updated`."""
    row = await _get_row(db, department_id)
    if row is None:
        row = AccountNopasswdSudoSettings(
            department_id=department_id,
            is_enabled=payload.is_enabled,
            created_by=identity.user_id,
        )
        db.add(row)
    else:
        row.is_enabled = payload.is_enabled

    await db.commit()
    await db.refresh(row)

    audit_service.emit(
        "settings.account_nopasswd_sudo_updated",
        target_id=department_id,
        target_type="account_nopasswd_sudo_settings",
        status="success",
        allowed=True,
        details={"department_id": department_id, "is_enabled": row.is_enabled, "via": "self_service"},
    )

    return AccountNopasswdSudoSettingsResponse(
        department_id=department_id, is_enabled=row.is_enabled, updated_at=row.updated_at,
    )


async def list_all(db: AsyncSession) -> AccountNopasswdSudoSettingsListResponse:
    """Список отделов-кандидатов + их текущий флаг (оверсайт account_admin).

    Кандидаты = union(отделы с хотя бы одним сервером, отделы, у которых флаг
    уже когда-либо выставлялся) — тот же приём, что у
    `acs_settings.list_acs_department_access`.
    """
    rows = list((await db.execute(select(AccountNopasswdSudoSettings))).scalars())
    by_department = {row.department_id: row for row in rows}

    department_ids = set(by_department.keys())
    department_ids.update(await server_repo.list_distinct_department_ids(db))

    items = []
    for department_id in sorted(department_ids):
        row = by_department.get(department_id)
        if row is None:
            items.append(
                AccountNopasswdSudoSettingsItem(
                    department_id=department_id, is_enabled=False, updated_at=None, created_by=None,
                )
            )
        else:
            items.append(
                AccountNopasswdSudoSettingsItem(
                    department_id=row.department_id,
                    is_enabled=row.is_enabled,
                    updated_at=row.updated_at,
                    created_by=row.created_by,
                )
            )
    return AccountNopasswdSudoSettingsListResponse(items=items)


async def update_many(
    db: AsyncSession,
    payload: AccountNopasswdSudoSettingsBatchUpdate,
    identity: IdentityContext,
) -> AccountNopasswdSudoSettingsListResponse:
    """Upsert одного или нескольких флагов is_enabled по department_id (account_admin).

    Один SELECT+INSERT/UPDATE на элемент батча, один commit в конце. Аудит —
    одно агрегированное событие со списком затронутых отделов, не по одному на
    строку, чтобы не заспамить лог при массовом тогле.
    """
    touched: list[str] = []
    for item in payload.items:
        row = await _get_row(db, item.department_id)
        if row is None:
            row = AccountNopasswdSudoSettings(
                department_id=item.department_id,
                is_enabled=item.is_enabled,
                created_by=identity.user_id,
            )
            db.add(row)
        else:
            row.is_enabled = item.is_enabled
        touched.append(item.department_id)

    await db.commit()

    audit_service.emit(
        "settings.account_nopasswd_sudo_updated",
        target_id=",".join(touched),
        target_type="account_nopasswd_sudo_settings",
        status="success",
        allowed=True,
        details={"department_ids": touched, "count": len(touched), "via": "account_admin"},
    )

    return await list_all(db)

"""Use cases для bootstrap-пароля версии ОС (авто-prepare после restore ACS).

1:1 с `os_versions`. `get_bootstrap_password_for_os_version` — не HTTP-эндпоинт,
а внутренняя функция: её зовёт dispatch restore и callback restore-done, чтобы
достать креды первого захода на только что восстановленный из снимка диск.
HTTP-слой (`/os-versions/{id}/bootstrap-password`) — тонкая CRUD-обёртка поверх
той же таблицы, permission-check остаётся на стороне endpoint'а/сервиса
os_version (то же право `(os_version, *, update)`).
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.os_version_bootstrap_password import OsVersionBootstrapPassword
from src.services import secrets_service
from src.utils.ids import os_version_bootstrap_password_id as new_id


async def _get_row(
    db: AsyncSession, os_version_id: str
) -> OsVersionBootstrapPassword | None:
    stmt = select(OsVersionBootstrapPassword).where(
        OsVersionBootstrapPassword.os_version_id == os_version_id
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_bootstrap_password_for_os_version(
    db: AsyncSession, os_version_id: str
) -> dict | None:
    """Расшифрованные bootstrap-креды версии ОС, либо `None`, если не заданы.

    Возвращает `{"ssh_username": ..., "password": ...}`. Плейнтекст пароля
    транзиентный — caller кладёт его в тот же Redis-stash, что и обычный
    prepare, и не хранит его нигде дольше окна dispatch'а.
    """
    row = await _get_row(db, os_version_id)
    if row is None:
        return None
    aad = secrets_service.aad_for_os_version_bootstrap_password(os_version_id)
    result = secrets_service.decrypt_with_meta(row.password_encrypted, aad=aad)
    if result.needs_reencrypt:
        await secrets_service.lazy_reencrypt_owner_column(
            db,
            table="os_version_bootstrap_passwords",
            column="password_encrypted",
            row_id=row.id,
            old_blob=row.password_encrypted,
            plaintext=result.plaintext,
            aad=aad,
        )
    return {"ssh_username": row.ssh_username, "password": result.plaintext}


async def get_bootstrap_password_status(
    db: AsyncSession, os_version_id: str
) -> dict | None:
    """Статус для GET-эндпоинта: логин + факт «пароль задан», без plaintext."""
    row = await _get_row(db, os_version_id)
    if row is None:
        return None
    return {
        "ssh_username": row.ssh_username,
        "has_password": bool(row.password_encrypted),
    }


async def upsert_bootstrap_password(
    db: AsyncSession,
    os_version_id: str,
    ssh_username: str,
    password: str,
    updated_by: str | None,
) -> None:
    """Upsert 1:1 строки bootstrap-пароля версии. Коммитит сам."""
    row = await _get_row(db, os_version_id)
    encrypted = secrets_service.encrypt(
        password,
        aad=secrets_service.aad_for_os_version_bootstrap_password(os_version_id),
    )
    if row is None:
        row = OsVersionBootstrapPassword(
            id=new_id(),
            os_version_id=os_version_id,
            ssh_username=ssh_username,
            password_encrypted=encrypted,
            updated_by=updated_by,
        )
        db.add(row)
    else:
        row.ssh_username = ssh_username
        row.password_encrypted = encrypted
        row.updated_by = updated_by
    await db.commit()

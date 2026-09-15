"""Повторяемый перенос старого ACS-пароля через публичный API секретов."""
import argparse
import asyncio
import base64
import hmac
import os
from urllib.parse import urlencode
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import AppException, BadRequestError, ConflictError
from src.models.acs_settings import AcsSettings, SINGLETON_ID
from src.services import audit_service, secret_client, secrets_service


async def locked_settings(db: AsyncSession):
    return (await db.execute(select(AcsSettings).where(AcsSettings.id == SINGLETON_ID)
        .with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()


async def migrate(db: AsyncSession, owner_dept_id: str, creator_token: str) -> str:
    row = await locked_settings(db)
    if row is None or (not row.credential_id and not row.acs_password_encrypted):
        raise BadRequestError(error_code="ACS_LEGACY_PASSWORD_MISSING", message="Старый пароль ACS не задан")
    if row.credential_id:
        await secret_client.reveal_acs_password(row.credential_id)
        return row.credential_id
    if row.migration_owner_dept_id and row.migration_owner_dept_id != owner_dept_id:
        raise ConflictError(error_code="ACS_MIGRATION_OWNER_CHANGED", message="Перенос уже начат для другого отдела")
    row.migration_id = row.migration_id or uuid4().hex
    row.migration_owner_dept_id = owner_dept_id
    migration_id, old_blob = row.migration_id, row.acs_password_encrypted
    password = secrets_service.decrypt_with_meta(old_blob, aad=secrets_service.aad_for_acs_password(SINGLETON_ID)).plaintext
    await db.commit()

    name = f"ACS migration {migration_id}"
    credential = await find_imported(name, owner_dept_id, creator_token)
    if credential is None:
        response = await secret_client.request("POST", "/credentials", token=creator_token, body={
            "name": name, "service": "acs", "scope": "service", "owner_dept_id": owner_dept_id,
            "secret_b64": base64.b64encode(password.encode()).decode(),
        })
        credential = await find_imported(name, owner_dept_id, creator_token) if response.status_code == 409 else secret_client.response_body(response)
    if not credential or credential.get("owner_dept_id") != owner_dept_id:
        raise ConflictError(error_code="ACS_MIGRATION_CONFLICT", message="Не удалось сопоставить запись переноса в сервисе секретов")
    credential_id = credential["id"]
    actual = await secret_client.reveal_acs_password(credential_id)
    if not hmac.compare_digest(actual.encode(), password.encode()):
        raise ConflictError(error_code="ACS_MIGRATION_SECRET_CHANGED", message="Значение сервисной записи изменилось; старый пароль ACS сохранён")

    row = await locked_settings(db)
    if row and row.credential_id == credential_id and not row.acs_password_encrypted:
        return credential_id
    if row is None or row.credential_id or row.acs_password_encrypted != old_blob or row.migration_id != migration_id:
        raise ConflictError(error_code="ACS_MIGRATION_CONFIG_CHANGED", message="Настройки ACS изменились во время переноса; повторите после проверки конфигурации")
    row.credential_id = credential_id
    row.acs_password_encrypted = None
    await db.commit()
    audit_service.emit("settings.acs_updated", target_id=SINGLETON_ID, target_type="acs_settings",
        status="success", allowed=True, details={"operation": "migrate_credential", "credential_id": credential_id, "owner_dept_id": owner_dept_id})
    return credential_id


async def find_imported(name: str, owner_dept_id: str, creator_token: str) -> dict | None:
    cursor = None
    seen = set()
    while True:
        query = {"service": "acs", "scope": "service", "limit": 100}
        if cursor:
            query["cursor"] = cursor
        page = secret_client.response_body(await secret_client.request("GET", "/credentials?" + urlencode(query), token=creator_token))
        credential = next((item for item in page.get("items", []) if item.get("name") == name and item.get("owner_dept_id") == owner_dept_id), None)
        cursor = page.get("next_cursor")
        if credential or not cursor:
            return credential
        if cursor in seen:
            raise ConflictError(error_code="ACS_MIGRATION_CONFLICT", message="Некорректная пагинация списка сервисных записей")
        seen.add(cursor)


async def main():
    parser = argparse.ArgumentParser(description="Перенести ACS-пароль в сервис секретов")
    parser.add_argument("--owner-department", required=True)
    args = parser.parse_args()
    token = os.environ.get("ACS_MIGRATION_OWNER_TOKEN", "")
    if not token:
        raise SystemExit("Задайте ACS_MIGRATION_OWNER_TOKEN — токен администратора отдела-владельца")
    from src.db.session import AsyncSessionLocal
    try:
        async with AsyncSessionLocal() as db:
            credential_id = await migrate(db, args.owner_department, token)
        print(f"ACS использует сервисную запись {credential_id}")
    except AppException as exc:
        raise SystemExit(f"{exc.error_code}: {exc.message}") from None


if __name__ == "__main__":
    asyncio.run(main())

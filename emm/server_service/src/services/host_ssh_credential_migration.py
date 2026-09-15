"""Повторяемый перенос старого SSH-ключа host-control через публичный API секретов."""
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
from src.models.host_services_settings import HostServicesSettings
from src.services import audit_service, secret_client, secrets_service


async def locked_settings(db: AsyncSession, department_id: str):
    return (await db.execute(select(HostServicesSettings).where(HostServicesSettings.department_id == department_id)
        .with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()


async def migrate(db: AsyncSession, department_id: str, creator_token: str) -> str:
    row = await locked_settings(db, department_id)
    if row is None or (not row.credential_id and not row.ssh_private_key_encrypted):
        raise BadRequestError(error_code="HOST_SSH_LEGACY_KEY_MISSING", message="Старый SSH-ключ отдела не задан")
    if row.credential_id:
        await secret_client.reveal_host_ssh_key(row.credential_id, department_id)
        return row.credential_id
    row.migration_id = row.migration_id or uuid4().hex
    migration_id, old_blob = row.migration_id, row.ssh_private_key_encrypted
    key = secrets_service.decrypt_with_meta(old_blob, aad=secrets_service.aad_for_host_control_ssh_key(department_id)).plaintext
    await db.commit()

    name = f"Host SSH migration {migration_id}"
    credential = await find_imported(name, department_id, creator_token)
    if credential is None:
        response = await secret_client.request("POST", "/credentials", token=creator_token, body={
            "name": name, "service": "host_ssh", "scope": "service", "owner_dept_id": department_id,
            "secret_b64": base64.b64encode(key.encode()).decode(),
        })
        credential = await find_imported(name, department_id, creator_token) if response.status_code == 409 else secret_client.response_body(response, unavailable_error_code="HOST_SSH_CREDENTIAL_UNAVAILABLE")
    if not credential or credential.get("owner_dept_id") != department_id:
        raise ConflictError(error_code="HOST_SSH_MIGRATION_CONFLICT", message="Не удалось сопоставить запись переноса в сервисе секретов")
    credential_id = credential["id"]
    actual = await secret_client.reveal_host_ssh_key(credential_id, department_id)
    if not hmac.compare_digest(actual.encode(), key.encode()):
        raise ConflictError(error_code="HOST_SSH_MIGRATION_SECRET_CHANGED", message="Значение сервисной записи изменилось; старый SSH-ключ сохранён")

    row = await locked_settings(db, department_id)
    if row and row.credential_id == credential_id and not row.ssh_private_key_encrypted:
        return credential_id
    if row is None or row.credential_id or row.ssh_private_key_encrypted != old_blob or row.migration_id != migration_id:
        raise ConflictError(error_code="HOST_SSH_MIGRATION_CONFIG_CHANGED", message="Настройки SSH-доступа изменились во время переноса; повторите после проверки конфигурации")
    row.credential_id = credential_id
    row.ssh_private_key_encrypted = None
    await db.commit()
    audit_service.emit("settings.host_services_updated", target_id=department_id, target_type="host_services_settings",
        status="success", allowed=True, details={"operation": "migrate_credential", "credential_id": credential_id, "department_id": department_id})
    return credential_id


async def find_imported(name: str, department_id: str, creator_token: str) -> dict | None:
    cursor = None
    seen = set()
    while True:
        query = {"service": "host_ssh", "scope": "service", "limit": 100}
        if cursor:
            query["cursor"] = cursor
        page = secret_client.response_body(await secret_client.request("GET", "/credentials?" + urlencode(query), token=creator_token), unavailable_error_code="HOST_SSH_CREDENTIAL_UNAVAILABLE")
        credential = next((item for item in page.get("items", []) if item.get("name") == name and item.get("owner_dept_id") == department_id), None)
        cursor = page.get("next_cursor")
        if credential or not cursor:
            return credential
        if cursor in seen:
            raise ConflictError(error_code="HOST_SSH_MIGRATION_CONFLICT", message="Некорректная пагинация списка сервисных записей")
        seen.add(cursor)


async def main():
    parser = argparse.ArgumentParser(description="Перенести SSH-ключ host-control отдела в сервис секретов")
    parser.add_argument("--department", required=True)
    args = parser.parse_args()
    token = os.environ.get("HOST_SSH_MIGRATION_TOKEN", "")
    if not token:
        raise SystemExit("Задайте HOST_SSH_MIGRATION_TOKEN — токен администратора отдела")
    from src.db.session import AsyncSessionLocal
    try:
        async with AsyncSessionLocal() as db:
            credential_id = await migrate(db, args.department, token)
        print(f"Host-control SSH использует сервисную запись {credential_id}")
    except AppException as exc:
        raise SystemExit(f"{exc.error_code}: {exc.message}") from None


if __name__ == "__main__":
    asyncio.run(main())

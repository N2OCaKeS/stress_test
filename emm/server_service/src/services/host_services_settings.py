"""Use cases для настроек SSH-доступа к хосту (host-service control) — платформенный singleton.

`HostServicesSettings` — одна строка (`SINGLETON_ID`) с host/port/user и
зашифрованным приватным SSH-ключом. Зеркало `services/acs_settings.py`.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ServiceUnavailableError
from src.models.host_services_settings import SINGLETON_ID, HostServicesSettings
from src.schemas.host_services_settings import (
    HostServicesSettingsResponse,
    HostServicesSettingsUpdate,
)
from src.services import audit_service, secrets_service


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


async def _get_row(db: AsyncSession) -> HostServicesSettings | None:
    return await db.get(HostServicesSettings, SINGLETON_ID)


async def get_settings(db: AsyncSession) -> HostServicesSettingsResponse:
    """Текущие настройки. Нет строки → дефолты (ничего не настроено)."""
    row = await _get_row(db)
    if row is None:
        return _defaults()
    return _to_response(row)


async def update_settings(
    db: AsyncSession,
    payload: HostServicesSettingsUpdate,
) -> HostServicesSettingsResponse:
    """Upsert настроек (частичное слияние) + шифрование ключа на входе.

    Неприсланные поля сохраняют текущее значение. Пустой `ssh_host`/`ssh_user`
    трактуется как явная очистка. Ключ: `ssh_private_key` задан → шифруется и
    заменяет текущий; `clear_private_key=True` (и `ssh_private_key` не задан) →
    стирает сохранённый ключ; иначе — не трогаем. Аудит `settings.host_services_updated`
    (сам ключ никогда не логируется, только факт `private_key_set`).
    """
    row = await _get_row(db)
    if row is None:
        row = HostServicesSettings(id=SINGLETON_ID, ssh_host=None, ssh_port=22, ssh_user=None, ssh_private_key_encrypted=None)
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
            payload.ssh_private_key, aad=secrets_service.aad_for_host_control_ssh_key(SINGLETON_ID)
        )
    elif payload.clear_private_key:
        row.ssh_private_key_encrypted = None

    await db.commit()
    await db.refresh(row)

    audit_service.emit(
        "settings.host_services_updated",
        target_id=SINGLETON_ID,
        target_type="host_services_settings",
        status="success",
        allowed=True,
        details={
            "ssh_host": row.ssh_host,
            "ssh_port": row.ssh_port,
            "ssh_user": row.ssh_user,
            "private_key_set": bool(row.ssh_private_key_encrypted),
        },
    )

    return _to_response(row)


async def get_decrypted_private_key(db: AsyncSession) -> str:
    """Расшифрованный приватный SSH-ключ хоста для `host_control`.

    503 `HOST_SERVICES_NOT_CONFIGURED`, если строки нет или не заполнены все
    три поля (host/user/ключ) — тот же контракт недоступности, что у
    `acs_settings.get_acs_credentials` (`ACS_DISABLED`).
    """
    row = await _get_row(db)
    if row is None or not row.ssh_host or not row.ssh_user or not row.ssh_private_key_encrypted:
        raise ServiceUnavailableError(
            error_code="HOST_SERVICES_NOT_CONFIGURED",
            message="Host services SSH access is not configured",
        )

    result = secrets_service.decrypt_with_meta(
        row.ssh_private_key_encrypted, aad=secrets_service.aad_for_host_control_ssh_key(SINGLETON_ID)
    )
    if result.needs_reencrypt:
        await secrets_service.lazy_reencrypt_owner_column(
            db,
            table="host_services_settings",
            column="ssh_private_key_encrypted",
            row_id=SINGLETON_ID,
            old_blob=row.ssh_private_key_encrypted,
            plaintext=result.plaintext,
            aad=secrets_service.aad_for_host_control_ssh_key(SINGLETON_ID),
        )
    return result.plaintext

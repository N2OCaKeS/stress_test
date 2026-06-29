"""Per-server управляющие креды (#3): генерация, шифрование, ротация.

Каждый сервер несёт свою SSH-пару + пароль управляющего пользователя `dbos`.
Раньше управление шло по единому глобальному ключу из env воркера — теперь
материал генерит server_service, шифрует тем же envelope-форматом
(`secrets_service`, AES-256-GCM), что и пароли аккаунтов/IPMI, и хранит на
строке `Server`.

Здесь живёт только server-side часть: сгенерировать/перевыпустить пару и
пароль и положить ciphertext в модель. Раскрытие воркеру (internal fetch) и
подтверждение применения (callback) — в `internal_service`, рядом с
`fetch_account_password`/`record_server_prepared`.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import DomainValidationError
from src.models import Server
from src.services import secrets_service
from src.services.server_account import (
    _generate_ssh_keypair,
    _generate_strong_password,
)

logger = logging.getLogger(__name__)


def generate_management_material() -> tuple[str, str, str]:
    """Сгенерировать управляющий материал: `(private_pem, public_openssh, password)`.

    Ed25519-пара + 24-символьный strong-password — те же генераторы, что у
    server_account provision'а (одна точка генерации криптоматериала на сервис).
    Пароль нужен для `sudo -S` / прямого console-логина как fallback, даже когда
    sudoers стоит NOPASSWD.
    """
    private_pem, public_openssh = _generate_ssh_keypair()
    password = _generate_strong_password()
    return private_pem, public_openssh, password


async def ensure_management_credentials(
    db: AsyncSession,
    server: Server,
) -> tuple[Server, dict, bool]:
    """Гарантировать наличие управляющих кред у сервера (sticky по существующим).

    Если креды уже есть (повторный prepare) — расшифровываем существующий
    материал и переиспользуем его, чтобы на боксе не оказалось рассинхрона с
    БД. Иначе генерим новую пару+пароль, шифруем и сохраняем; flush в этой же
    транзакции, commit делает caller (savepoint-паттерн как у
    `ensure_provision_credentials`).

    Помечает `mgmt_creds_pending_apply=True` — материал записан в БД, но на
    боксе подтверждается только callback'ом `prepared`. Возвращает
    `(server, creds, generated)`, где `creds = {public_key, private_key,
    password}` (plaintext для stash воркеру), `generated=True` — если материал
    создан в этом вызове (для аудита `management_creds_generated`).
    """
    was_already_pending = bool(server.mgmt_creds_pending_apply)
    aad_ssh = secrets_service.aad_for_server_mgmt_ssh_key(server.id)
    aad_pwd = secrets_service.aad_for_server_mgmt_password(server.id)

    if server.mgmt_ssh_public_key is not None:
        if not server.mgmt_ssh_private_key_encrypted or not server.mgmt_password_encrypted:
            # public есть, а privkey/пароль нет — повреждённая строка. Отдать
            # воркеру pubkey без матчащего privkey/пароля нельзя: вход по новому
            # ключу не проверить, анти-локаут сломан. Оператор разбирается руками.
            raise DomainValidationError(
                error_code="MANAGEMENT_CREDS_INCONSISTENT",
                message=(
                    "Server has mgmt_ssh_public_key but is missing the encrypted "
                    "private key or password — row needs to be reset before prepare"
                ),
                details={"server_id": server.id},
            )
        private_pem = secrets_service.decrypt(
            server.mgmt_ssh_private_key_encrypted, aad=aad_ssh,
        )
        password = secrets_service.decrypt(
            server.mgmt_password_encrypted, aad=aad_pwd,
        )
        public_openssh = server.mgmt_ssh_public_key
        generated = False
    else:
        private_pem, public_openssh, password = generate_management_material()
        server.mgmt_ssh_public_key = public_openssh
        server.mgmt_ssh_private_key_encrypted = secrets_service.encrypt(
            private_pem, aad=aad_ssh,
        )
        server.mgmt_password_encrypted = secrets_service.encrypt(
            password, aad=aad_pwd,
        )
        generated = True

    server.mgmt_creds_pending_apply = True
    if generated or not was_already_pending:
        await db.flush()

    creds = {
        "public_key": public_openssh,
        "private_key": private_pem,
        "password": password,
    }
    return server, creds, generated


async def rotate_management_credentials(
    db: AsyncSession,
    server: Server,
) -> dict:
    """Перевыпустить управляющую пару+пароль сервера.

    Текущий ciphertext переезжает в `previous_mgmt_*` (анти-локаут: пока новый
    не подтверждён на боксе, fetch отдаёт previous — рабочий ключ), новый
    пишется в `mgmt_*`, ставится `mgmt_creds_pending_apply=True`. Flush в этой
    же транзакции, commit делает caller. Возвращает `{public_key, private_key,
    password}` нового материала для dispatch-stash'а воркер-таски.
    """
    aad_ssh = secrets_service.aad_for_server_mgmt_ssh_key(server.id)
    aad_pwd = secrets_service.aad_for_server_mgmt_password(server.id)

    private_pem, public_openssh, password = generate_management_material()

    # Переносим прежний материал в previous-зеркала тем же AAD (он привязан к
    # server_id + kind, не к колонке) — оператор/воркер заходят старым ключом,
    # пока новый не раскатан и не подтверждён applied-callback'ом.
    if server.mgmt_ssh_private_key_encrypted is not None:
        server.previous_mgmt_ssh_private_key_encrypted = (
            server.mgmt_ssh_private_key_encrypted
        )
    if server.mgmt_password_encrypted is not None:
        server.previous_mgmt_password_encrypted = server.mgmt_password_encrypted

    server.mgmt_ssh_public_key = public_openssh
    server.mgmt_ssh_private_key_encrypted = secrets_service.encrypt(
        private_pem, aad=aad_ssh,
    )
    server.mgmt_password_encrypted = secrets_service.encrypt(
        password, aad=aad_pwd,
    )
    server.mgmt_creds_pending_apply = True
    await db.flush()

    return {
        "public_key": public_openssh,
        "private_key": private_pem,
        "password": password,
    }

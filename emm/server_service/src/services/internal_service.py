"""Internal-use cases для server_worker.

Исторически эти endpoint'ы обходили department-фильтр (worker — это
service-to-service caller, работает platform-wide) и опирались только на
матрицу `entity_permissions` — т.е. worker-PAT держал роль `admin` с
глобальными `view_credentials` / `view_password` / `rotate_password`,
что означало: любой holder PAT'а мог читать секреты ЛЮБОГО сервера в
ЛЮБОМ department'е.

Один глобальный worker-бот обслуживает серверы РАЗНЫХ отделов: он живёт в
системном отделе, и его `identity.department_id` заведомо не совпадает с
`server.department_id`. Поэтому отдел самого бота в авторизации НЕ участвует.
Единственный cross-dept гард — заголовок `X-Target-Department-Id`, который
проверяет `_check_target_department`:

  * заголовок форвардит воркер из `target_department_id` задачи (server_service
    сам положил туда `server.department_id` при dispatch'е, уже проверив права
    запросившего юзера);
  * заголовок **обязан присутствовать** и совпасть с `server.department_id`;
  * отсутствует → 403 `TARGET_DEPARTMENT_HEADER_REQUIRED`;
  * не совпал → 404 (маска not-found, чтобы 403/404 не работали
    enumeration-oracle'ом), `reason=target_department_mismatch`;
  * совпал → пропускаем.

Проверка заголовка безусловна — она не зависит от `internal_require_dept_header`,
т.к. это единственный барьер между воркером и сервером чужого отдела. Header
НЕ заменяет `require_action` permission-проверку — это ортогональный cross-dept
scoping поверх матрицы прав.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.constants import AccountSource, Action, BusyState, EntityType
from src.core.exceptions import (
    AppException,
    AuthorizationError,
    BadRequestError,
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
)
from src.core.known_os import is_known_os, normalize_os_version_name
from src.repositories import ipmi_controller as ipmi_repo
from src.repositories import os_version as osv_repo
from src.repositories import server as server_repo
from src.repositories import server_account as account_repo
from src.repositories import server_account_ignored_login as ignored_login_repo
from src.repositories import server_disk as disk_repo
from src.repositories import vm as vm_repo
from src.repositories import vm_disk as vm_disk_repo
from src.repositories import vm_package_inventory as vm_package_repo
from src.repositories import vm_snapshot as vm_snapshot_repo
from src.schemas.identity import IdentityContext
from src.schemas.internal import (
    InventoryCallbackRequest,
    IpmiCredentialsRotatedRequest,
    ProvisionStatusRequest,
    UsersInventoryCallbackRequest,
)
from src.schemas.internal import PowerStateCallbackRequest
from src.schemas.server import (
    AcsSnapshotCreatedCallbackRequest,
    AcsSnapshotRestoreDoneCallbackRequest,
    ServerAstraUpdateCallbackRequest,
    ServerPrepareCallbackRequest,
)
from src.schemas.vm import (
    VmDisksCallbackRequest,
    VmPackagesCallbackRequest,
    VmPreparedCallbackRequest,
    VmsHubStateCallbackRequest,
    VmSnapshotsCallbackRequest,
    VmStateCallbackRequest,
)
from src.services import (
    audit_service,
    auto_inventory,
    management_creds as management_creds_svc,
    management_user_config as management_user_config_svc,
    metrics,
    os_version_bootstrap_password as bootstrap_password_svc,
    permissions,
    reservation,
    secrets_service,
    worker_client,
)
from src.services import server as server_svc
from src.services import vm as vm_svc
from src.utils.ids import (
    os_version_id,
    prepare_creds_id,
    server_disk_id,
    vm_snapshot_id,
)

logger = logging.getLogger(__name__)

# Допустимый NTP-skew между worker'ом и server_service'ом для `rotated_at` в
# `record_ipmi_credentials_rotated` живёт в Settings (env `ROTATED_AT_SKEW_SECONDS`,
# дефолт 600). Читаем через `get_settings()` в самом обработчике — fixture'ы и
# `monkeypatch.setenv` в тестах подхватываются без перезагрузки модуля.


def _check_target_department(
    *,
    audit_action: str,
    target_id: str,
    target_type: str,
    server_department_id: str | None,
    header_department_id: str | None,
    actor_department_id: str | None,
    not_found_error_code: str,
    not_found_message: str,
    extra_details: dict | None = None,
) -> None:
    """Cross-check `X-Target-Department-Id` header против server.department_id.

    Отдел самого воркер-бота (`actor_department_id`) в авторизации НЕ участвует:
    один глобальный бот обслуживает серверы всех отделов и живёт в системном
    отделе. Единственный cross-dept гард — заголовок, поэтому он enforce'ится
    безусловно (не зависит от `internal_require_dept_header`):

    * `header_department_id is None` → 403 ``TARGET_DEPARTMENT_HEADER_REQUIRED``,
      `reason=missing_target_department_header`.
    * `header_department_id != server_department_id` → 404 (`not_found_error_code`,
      маска not-found — разница 403/404 работала бы enumeration-oracle'ом для
      воркера, щупающего чужой отдел), `reason=target_department_mismatch`.
    * совпал → пропускаем.

    `actor_department_id` пишется в `details` для наблюдаемости (SIEM видит, из
    какого отдела пришёл бот), но на блокировку не влияет.

    `audit_action` — тот же action-key, что caller использует для
    success/denied/failure emit'ов, чтобы оператор мог корреллировать.

    Идентификатор уезжает в `details` под единым ключом `target_id` —
    SIEM/правила различают тип через `target_type` рядом, а не по имени
    поля. Раньше у нас был `target_field` с дефолтом `server_id`, и
    account/controller caller'ы получали `acc_*`/`ipm_*` либо под чужой
    меткой, либо требовали явного override'а на каждом call-site.
    """
    extra = dict(extra_details or {})
    extra.update({
        "server_department_id": server_department_id,
        "header_department_id": header_department_id,
        "actor_department_id": actor_department_id,
    })

    def _emit_denied(reason: str) -> None:
        """Локальный shortcut для denied-audit с общим target/action."""
        audit_service.emit(
            audit_action,
            target_id=target_id, target_type=target_type,
            status="denied", allowed=False,
            details={**extra, "reason": reason},
        )

    if header_department_id is None:
        _emit_denied("missing_target_department_header")
        raise AuthorizationError(
            error_code="TARGET_DEPARTMENT_HEADER_REQUIRED",
            message=(
                "X-Target-Department-Id header is required for internal "
                "endpoints"
            ),
            details={"target_id": target_id},
        )

    if header_department_id != server_department_id:
        # Заголовок указывает на чужой отдел — либо stale payload воркера, либо
        # PAT, который щупает не свои серверы. Маскируем под not-found, чтобы по
        # разнице кодов нельзя было перечислить существующие чужие ресурсы.
        _emit_denied("target_department_mismatch")
        raise NotFoundError(
            error_code=not_found_error_code,
            message=not_found_message,
            details={"target_id": target_id},
        )


# Тонкие обёртки над `_check_target_department` для случаев, где маска 404
# подаётся одним и тем же набором аргументов. Сжимают 5-строчный kwargs-блок
# на каждом call-site до одного вызова, error_code/message не разъезжаются.

def _check_target_department_for_server(
    *,
    audit_action: str,
    target_id: str,
    server_department_id: str | None,
    header_department_id: str | None,
    actor_department_id: str | None,
    extra_details: dict | None = None,
) -> None:
    _check_target_department(
        audit_action=audit_action,
        target_id=target_id,
        target_type="server",
        server_department_id=server_department_id,
        header_department_id=header_department_id,
        actor_department_id=actor_department_id,
        extra_details=extra_details,
        not_found_error_code="SERVER_NOT_FOUND",
        not_found_message="Server not found",
    )


def _check_target_department_for_account(
    *,
    audit_action: str,
    target_id: str,
    server_department_id: str | None,
    header_department_id: str | None,
    actor_department_id: str | None,
    extra_details: dict | None = None,
) -> None:
    _check_target_department(
        audit_action=audit_action,
        target_id=target_id,
        target_type="server_account",
        server_department_id=server_department_id,
        header_department_id=header_department_id,
        actor_department_id=actor_department_id,
        extra_details=extra_details,
        not_found_error_code="ACCOUNT_NOT_FOUND",
        not_found_message="Server account not found on this server",
    )


def _check_target_department_for_controller(
    *,
    audit_action: str,
    target_id: str,
    server_department_id: str | None,
    header_department_id: str | None,
    actor_department_id: str | None,
    extra_details: dict | None = None,
) -> None:
    _check_target_department(
        audit_action=audit_action,
        target_id=target_id,
        target_type="ipmi_controller",
        server_department_id=server_department_id,
        header_department_id=header_department_id,
        actor_department_id=actor_department_id,
        extra_details=extra_details,
        not_found_error_code="NO_IPMI_CONTROLLER",
        not_found_message="IPMI controller not found",
    )


async def fetch_os_version_bootstrap_password(
    db: AsyncSession,
    identity: IdentityContext,
    os_version_id: str,
) -> dict:
    """Расшифровать и вернуть bootstrap-креды версии ОС для worker'а.

    Нужно `acs.snapshot_restore`, чтобы САМОМУ (не только server_service на
    callback'е) проверить SSH бутстрап-кредой перед тем, как репортить
    restore успешным — иначе `server.prepare` стартует раньше, чем сервер
    реально поднимется после переустановки диска. Не department-scoped —
    версия ОС общая на всю платформу, dept-check тут не нужен.

    Право `(server, *, prepare_callback)` — тот же узкий грант worker_bot'а,
    что у остальных internal-эндпоинтов подготовки.
    """
    try:
        await permissions.require_action(
            db, identity, EntityType.SERVER, Action.PREPARE_CALLBACK
        )
    except AuthorizationError:
        audit_service.emit(
            "os_version.bootstrap_password_fetched",
            target_id=os_version_id, target_type="os_version",
            status="denied", allowed=False,
            details={"reason": "permission_denied", "caller_type": identity.subject_type},
        )
        raise
    creds = await bootstrap_password_svc.get_bootstrap_password_for_os_version(
        db, os_version_id,
    )
    if creds is None:
        audit_service.emit(
            "os_version.bootstrap_password_fetched",
            target_id=os_version_id, target_type="os_version",
            status="failure", allowed=True,
            details={"reason": "not_found"},
        )
        raise NotFoundError(
            error_code="OS_VERSION_BOOTSTRAP_PASSWORD_NOT_FOUND",
            message="Bootstrap password not set for this OS version",
        )
    audit_service.emit(
        "os_version.bootstrap_password_fetched",
        target_id=os_version_id, target_type="os_version",
        status="success", allowed=True,
        details={"caller_type": identity.subject_type},
    )
    return creds


async def fetch_ipmi_credentials(
    db: AsyncSession,
    identity: IdentityContext,
    server_id: str,
    target_department_id: str | None = None,
) -> dict:
    """Расшифровать и вернуть IPMI-credentials для worker'а."""
    try:
        await permissions.require_action(
            db, identity, EntityType.IPMI_CONTROLLER, Action.VIEW_CREDENTIALS
        )
    except AuthorizationError:
        audit_service.emit(
            "ipmi_controller.view_credentials",
            target_id=server_id, target_type="ipmi_controller",
            status="denied", allowed=False,
            details={
                "reason": "permission_denied",
                "caller_type": identity.subject_type,
            },
        )
        raise
    server = await server_repo.get_by_id(db, server_id)
    # Dept-check фактически объединяем с existence-check: если сервера нет или
    # dept у caller'а другой — выдаём один и тот же SERVER_NOT_FOUND, не
    # выделяя 403 в отдельный сигнал. Иначе по разнице 403 vs 404 caller
    # угадывал бы, что сервер есть в чужом dept'е. Несуществующий server_id
    # обрабатываем ДО dept-check'а: SIEM иначе ловит фейковый
    # target_department_mismatch, которого фактически нет.
    if server is None:
        audit_service.emit(
            "ipmi_controller.view_credentials",
            target_id=server_id, target_type="ipmi_controller",
            status="failure", allowed=True,
            details={"reason": "server_not_found"},
        )
        raise NotFoundError(error_code="SERVER_NOT_FOUND", message="Server not found")
    server_department_id = server.department_id
    _check_target_department(
        audit_action="ipmi_controller.view_credentials",
        target_id=server_id,
        target_type="ipmi_controller",
        server_department_id=server_department_id,
        header_department_id=target_department_id,
        actor_department_id=identity.department_id,
        not_found_error_code="SERVER_NOT_FOUND",
        not_found_message="Server not found",
    )
    ctrl = await ipmi_repo.get_by_server_id(db, server_id)
    if ctrl is None:
        audit_service.emit(
            "ipmi_controller.view_credentials",
            target_id=server_id, target_type="ipmi_controller",
            status="failure", allowed=True,
            details={"reason": "controller_not_registered"},
        )
        raise NotFoundError(
            error_code="NO_IPMI_CONTROLLER",
            message="No IPMI controller is registered for this server",
        )
    aad = secrets_service.aad_for_ipmi_credential(ctrl.id)
    old_blob = ctrl.password_encrypted
    try:
        result = secrets_service.decrypt_with_meta(old_blob, aad=aad)
    except AppException:
        # Симметрично с `ipmi_controller._reveal_controller_password`:
        # сломанный ciphertext должен оставить SIEM-след именно как
        # failure-audit, а не уезжать наверх голым 422.
        metrics.increment_secrets_decrypt_failures()
        audit_service.emit(
            "ipmi_controller.view_credentials",
            target_id=ctrl.id, target_type="ipmi_controller",
            status="failure", allowed=True,
            details={
                "reason": "decrypt_failed",
                "server_id": server_id,
                "caller_type": identity.subject_type,
            },
        )
        raise
    plain = result.plaintext
    if result.needs_reencrypt:
        # Lazy миграция legacy-ciphertext'а под активный ключ. Read worker'а
        # не блокируется ошибками UPDATE'а — outbox-flow подчистит остальное.
        await secrets_service.lazy_reencrypt_owner_column(
            db,
            table="ipmi_controllers",
            column="password_encrypted",
            row_id=ctrl.id,
            old_blob=old_blob,
            plaintext=plain,
            aad=aad,
        )
    audit_service.emit(
        "ipmi_controller.view_credentials",
        target_id=ctrl.id, target_type="ipmi_controller",
        status="success", allowed=True,
        details={
            "server_id": server_id,
            "kind": ctrl.kind,
            "department_id": server.department_id,
            "caller_type": identity.subject_type,
        },
    )
    return {
        "controller_id": ctrl.id,
        "kind": ctrl.kind,
        "endpoint_url": ctrl.endpoint_url,
        "username": ctrl.username,
        "password": plain,
    }


async def fetch_account_password(
    db: AsyncSession,
    identity: IdentityContext,
    server_id: str,
    account_id: str,
    target_department_id: str | None = None,
) -> dict:
    """Расшифровать и вернуть пароль OS-аккаунта для worker'а."""
    try:
        await permissions.require_action(
            db, identity, EntityType.SERVER_ACCOUNT, Action.VIEW_PASSWORD
        )
    except AuthorizationError:
        audit_service.emit(
            "server_account.view_password",
            target_id=account_id, target_type="server_account",
            status="denied", allowed=False,
            details={
                "reason": "permission_denied",
                "server_id": server_id,
                "caller_type": identity.subject_type,
            },
        )
        raise
    # Dept-check РАНЬШЕ existence-проверок: header-mismatch маскируется под то же
    # 404 ACCOUNT_NOT_FOUND, что и валидный caller на несуществующем account_id, —
    # по коду ответа нельзя перечислить чужие ресурсы. 403 остаётся только для
    # permission_denied / отсутствующего заголовка.
    # Исключение: если server_id фактически не существует — эмитим честный
    # `server_not_found` ДО dept-check'а. Иначе SIEM ловит фейковый
    # `target_department_mismatch` на каждом тычке несуществующим server_id.
    # Клиенту всё равно уходит 404 ACCOUNT_NOT_FOUND/SERVER_NOT_FOUND, oracle
    # не открывается.
    server = await server_repo.get_by_id(db, server_id)
    if server is None:
        # Сервер отсутствует — реальная missing-entity это server, не account.
        # Эмитим под `target_type="server"`, account_id уезжает в details как
        # secondary. Иначе SIEM ловит server-level miss под account-меткой.
        audit_service.emit(
            "server_account.view_password",
            target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={
                "reason": "server_not_found",
                "server_id": server_id,
                "account_id": account_id,
            },
        )
        raise NotFoundError(
            error_code="ACCOUNT_NOT_FOUND",
            message="Server account not found on this server",
        )
    server_department_id = server.department_id
    _check_target_department_for_account(
        audit_action="server_account.view_password",
        target_id=account_id,
        server_department_id=server_department_id,
        header_department_id=target_department_id,
        actor_department_id=identity.department_id,
        extra_details={"server_id": server_id},
    )
    account = await account_repo.get_by_id(db, account_id)
    if account is None or not await account_repo.is_linked(db, account_id, server_id):
        audit_service.emit(
            "server_account.view_password",
            target_id=account_id, target_type="server_account",
            status="failure", allowed=True,
            details={"reason": "account_not_found", "server_id": server_id},
        )
        raise NotFoundError(
            error_code="ACCOUNT_NOT_FOUND",
            message="Server account not found on this server",
        )
    if account.password_encrypted is None:
        # Discovered-аккаунт (`source=discovered`) приходит в БД без пароля —
        # инвентаризация ловит существующего OS-юзера и заводит карточку.
        # `account.provision` на такой записи отдаёт worker'у пустой пароль:
        # tasks/users.py трактует это как «useradd без chpasswd», что
        # соответствует ТЗ (SUCCEEDED + INFO, не failure). Для managed-аккаунтов
        # без пароля поведение прежнее — 404 ACCOUNT_HAS_NO_PASSWORD.
        if account.source == AccountSource.DISCOVERED.value:
            audit_service.emit(
                "server_account.view_password",
                target_id=account_id, target_type="server_account",
                status="success", allowed=True,
                details={
                    "server_id": server_id,
                    "login": account.login,
                    "discovered_no_password": True,
                    "caller_type": identity.subject_type,
                },
            )
            return {"login": account.login, "password": ""}
        audit_service.emit(
            "server_account.view_password",
            target_id=account_id, target_type="server_account",
            status="failure", allowed=True,
            details={"reason": "no_password_stored", "server_id": server_id},
        )
        raise NotFoundError(
            error_code="ACCOUNT_HAS_NO_PASSWORD",
            message="Account has no stored password",
        )
    aad = secrets_service.aad_for_server_account_password(account.id)
    old_blob = account.password_encrypted
    try:
        result = secrets_service.decrypt_with_meta(old_blob, aad=aad)
    except AppException:
        # Симметрично с `server_account._reveal_account_password`: на битом
        # ciphertext'е worker'у нужнее audit-trail, чем чистый 422-trace.
        metrics.increment_secrets_decrypt_failures()
        audit_service.emit(
            "server_account.view_password",
            target_id=account_id, target_type="server_account",
            status="failure", allowed=True,
            details={
                "reason": "decrypt_failed",
                "server_id": server_id,
                "caller_type": identity.subject_type,
            },
        )
        raise
    plain = result.plaintext
    if result.needs_reencrypt:
        # Lazy миграция legacy-ciphertext'а под активный ключ — параллельно
        # с outbox-flow. Read не блокируется ошибками UPDATE'а.
        await secrets_service.lazy_reencrypt_owner_column(
            db,
            table="server_accounts",
            column="password_encrypted",
            row_id=account.id,
            old_blob=old_blob,
            plaintext=plain,
            aad=aad,
        )
    audit_service.emit(
        "server_account.view_password",
        target_id=account_id, target_type="server_account",
        status="success", allowed=True,
        details={
            "server_id": server_id,
            "login": account.login,
            "caller_type": identity.subject_type,
        },
    )
    return {"login": account.login, "password": plain}


async def fetch_account_password_by_id(
    db: AsyncSession,
    identity: IdentityContext,
    account_id: str,
    target_department_id: str | None = None,
) -> dict:
    """Расшифровать пароль аккаунта по одному `account_id`, без server_id.

    Нужно провижну привязанных к ВМ учёток: dispatch `vm.create` знает только
    `account_id`/`login`, исходный сервер аккаунта воркеру не передаётся. Аккаунт
    резолвится по глобально-уникальному id, отдел берём из самой карточки, а не
    из сервера. Заголовок `X-Target-Department-Id` cross-check'ится против
    `account.department_id`.
    """
    try:
        await permissions.require_action(
            db, identity, EntityType.SERVER_ACCOUNT, Action.VIEW_PASSWORD
        )
    except AuthorizationError:
        audit_service.emit(
            "server_account.view_password",
            target_id=account_id, target_type="server_account",
            status="denied", allowed=False,
            details={
                "reason": "permission_denied",
                "caller_type": identity.subject_type,
                "by_account_id": True,
            },
        )
        raise
    # Здесь аккаунт — самостоятельная сущность (server_id нет), поэтому грузим
    # его до dept-check'а: отдел берётся из карточки. Несуществующий account и
    # чужой отдел отдают один и тот же 404 ACCOUNT_NOT_FOUND — по коду ответа
    # нельзя перечислить чужие учётки.
    account = await account_repo.get_by_id(db, account_id)
    if account is None:
        audit_service.emit(
            "server_account.view_password",
            target_id=account_id, target_type="server_account",
            status="failure", allowed=True,
            details={"reason": "account_not_found", "by_account_id": True},
        )
        raise NotFoundError(
            error_code="ACCOUNT_NOT_FOUND",
            message="Server account not found",
        )
    _check_target_department_for_account(
        audit_action="server_account.view_password",
        target_id=account_id,
        server_department_id=account.department_id,
        header_department_id=target_department_id,
        actor_department_id=identity.department_id,
        extra_details={"by_account_id": True},
    )
    if account.password_encrypted is None:
        # Discovered-аккаунт без пароля — отдаём пустую строку (useradd без
        # chpasswd), managed без пароля — 404, симметрично `fetch_account_password`.
        if account.source == AccountSource.DISCOVERED.value:
            audit_service.emit(
                "server_account.view_password",
                target_id=account_id, target_type="server_account",
                status="success", allowed=True,
                details={
                    "login": account.login,
                    "discovered_no_password": True,
                    "caller_type": identity.subject_type,
                    "by_account_id": True,
                },
            )
            return {"login": account.login, "password": ""}
        audit_service.emit(
            "server_account.view_password",
            target_id=account_id, target_type="server_account",
            status="failure", allowed=True,
            details={"reason": "no_password_stored", "by_account_id": True},
        )
        raise NotFoundError(
            error_code="ACCOUNT_HAS_NO_PASSWORD",
            message="Account has no stored password",
        )
    aad = secrets_service.aad_for_server_account_password(account.id)
    old_blob = account.password_encrypted
    try:
        result = secrets_service.decrypt_with_meta(old_blob, aad=aad)
    except AppException:
        metrics.increment_secrets_decrypt_failures()
        audit_service.emit(
            "server_account.view_password",
            target_id=account_id, target_type="server_account",
            status="failure", allowed=True,
            details={
                "reason": "decrypt_failed",
                "caller_type": identity.subject_type,
                "by_account_id": True,
            },
        )
        raise
    plain = result.plaintext
    if result.needs_reencrypt:
        await secrets_service.lazy_reencrypt_owner_column(
            db,
            table="server_accounts",
            column="password_encrypted",
            row_id=account.id,
            old_blob=old_blob,
            plaintext=plain,
            aad=aad,
        )
    audit_service.emit(
        "server_account.view_password",
        target_id=account_id, target_type="server_account",
        status="success", allowed=True,
        details={
            "login": account.login,
            "caller_type": identity.subject_type,
            "by_account_id": True,
        },
    )
    return {"login": account.login, "password": plain}


async def rotate_account_password(
    db: AsyncSession,
    identity: IdentityContext,
    server_id: str,
    account_id: str,
    new_password: str,
    target_department_id: str | None = None,
) -> dict:
    """Принять новый пароль OS-аккаунта (callback worker'а после SSH-apply)."""
    try:
        await permissions.require_action(
            db, identity, EntityType.SERVER_ACCOUNT, Action.ROTATE_PASSWORD
        )
    except AuthorizationError:
        audit_service.emit(
            "server_account.rotate_password",
            target_id=account_id, target_type="server_account",
            status="denied", allowed=False,
            details={
                "reason": "permission_denied",
                "server_id": server_id,
                "caller_type": identity.subject_type,
            },
        )
        raise
    # Dept-check РАНЬШЕ existence-проверок: header-mismatch маскируется под то же
    # 404 ACCOUNT_NOT_FOUND, что и валидный caller на несуществующем account_id.
    # Симметрично с `fetch_account_password`.
    # Исключение: если server отсутствует — эмитим честный `server_not_found`
    # ДО dept-check'а, чтобы SIEM не ловил фейковый target_department_mismatch.
    server = await server_repo.get_by_id(db, server_id)
    if server is None:
        # Сервер отсутствует — missing-entity это server, не account.
        # Эмитим под `target_type="server"`, account_id уезжает в details.
        # Симметрично с `fetch_account_password`.
        audit_service.emit(
            "server_account.rotate_password",
            target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={
                "reason": "server_not_found",
                "server_id": server_id,
                "account_id": account_id,
            },
        )
        raise NotFoundError(
            error_code="ACCOUNT_NOT_FOUND",
            message="Server account not found on this server",
        )
    server_department_id = server.department_id
    _check_target_department_for_account(
        audit_action="server_account.rotate_password",
        target_id=account_id,
        server_department_id=server_department_id,
        header_department_id=target_department_id,
        actor_department_id=identity.department_id,
        extra_details={"server_id": server_id},
    )
    account = await account_repo.get_by_id(db, account_id)
    if account is None or not await account_repo.is_linked(db, account_id, server_id):
        audit_service.emit(
            "server_account.rotate_password",
            target_id=account_id, target_type="server_account",
            status="failure", allowed=True,
            details={"reason": "account_not_found", "server_id": server_id},
        )
        raise NotFoundError(
            error_code="ACCOUNT_NOT_FOUND",
            message="Server account not found on this server",
        )
    encrypted = secrets_service.encrypt(
        new_password,
        aad=secrets_service.aad_for_server_account_password(account.id),
    )
    updated = await account_repo.update_password(db, account, encrypted)
    await db.commit()
    audit_service.emit(
        "server_account.rotate_password",
        target_id=account_id, target_type="server_account",
        status="success", allowed=True,
        details={
            "server_id": server_id,
            "login": account.login,
            "rotated_at": updated.password_rotated_at.isoformat(),
            "caller_type": identity.subject_type,
        },
    )
    return {"ok": True, "rotated_at": updated.password_rotated_at.isoformat()}


async def fetch_management_credentials(
    db: AsyncSession,
    identity: IdentityContext,
    server_id: str,
    target_department_id: str | None = None,
) -> dict:
    """Расшифровать и вернуть per-server управляющие креды воркеру (#3).

    Воркер just-in-time тянет приватный ключ + пароль пользователя `dbos`
    перед каждой managed-операцией (паттерн `fetch_account_password`).

    Инвариант анти-локаута: пока `mgmt_creds_pending_apply=True` и есть
    previous-материал — отдаём previous (рабочий на боксе), а не свежий
    ciphertext. После applied/prepared-callback'а (pending снят) — текущий.

    Доступ: `(server, *, view_management_credentials)` — узкий грант worker_bot'а.
    Аудит: `server.management_credentials_revealed` (WARNING на success —
    штатный internal pull, не утечка человеку).
    """
    try:
        await permissions.require_action(
            db, identity, EntityType.SERVER, Action.VIEW_MANAGEMENT_CREDENTIALS,
        )
    except AuthorizationError:
        audit_service.emit(
            "server.management_credentials_revealed",
            target_id=server_id, target_type="server",
            status="denied", allowed=False,
            details={
                "reason": "permission_denied",
                "caller_type": identity.subject_type,
            },
        )
        raise
    server = await server_repo.get_by_id(db, server_id)
    if server is None:
        audit_service.emit(
            "server.management_credentials_revealed",
            target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "server_not_found"},
        )
        raise NotFoundError(error_code="SERVER_NOT_FOUND", message="Server not found")
    _check_target_department_for_server(
        audit_action="server.management_credentials_revealed",
        target_id=server_id,
        server_department_id=server.department_id,
        header_department_id=target_department_id,
        actor_department_id=identity.department_id,
    )
    if server.mgmt_ssh_private_key_encrypted is None or server.mgmt_password_encrypted is None:
        audit_service.emit(
            "server.management_credentials_revealed",
            target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "no_creds_stored", "department_id": server.department_id},
        )
        raise NotFoundError(
            error_code="MANAGEMENT_CREDS_NOT_FOUND",
            message="Server has no stored management credentials (not prepared yet)",
        )

    # Пока новый материал не подтверждён на боксе — отдаём previous (рабочий).
    use_previous = (
        server.mgmt_creds_pending_apply
        and server.previous_mgmt_ssh_private_key_encrypted is not None
        and server.previous_mgmt_password_encrypted is not None
    )
    if use_previous:
        ssh_blob = server.previous_mgmt_ssh_private_key_encrypted
        pwd_blob = server.previous_mgmt_password_encrypted
        source = "previous"
    else:
        ssh_blob = server.mgmt_ssh_private_key_encrypted
        pwd_blob = server.mgmt_password_encrypted
        source = "current"

    aad_ssh = secrets_service.aad_for_server_mgmt_ssh_key(server.id)
    aad_pwd = secrets_service.aad_for_server_mgmt_password(server.id)
    try:
        private_pem = secrets_service.decrypt(ssh_blob, aad=aad_ssh)
        password = secrets_service.decrypt(pwd_blob, aad=aad_pwd)
    except AppException:
        metrics.increment_secrets_decrypt_failures()
        audit_service.emit(
            "server.management_credentials_revealed",
            target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={
                "reason": "decrypt_failed",
                "department_id": server.department_id,
                "caller_type": identity.subject_type,
            },
        )
        raise
    audit_service.emit(
        "server.management_credentials_revealed",
        target_id=server_id, target_type="server",
        status="success", allowed=True,
        details={
            "department_id": server.department_id,
            "management_user": server.management_user,
            "source": source,
            "caller_type": identity.subject_type,
        },
    )
    return {
        "management_user": server.management_user,
        "ssh_private_key": private_pem,
        "password": password,
    }


async def confirm_management_creds_applied(
    db: AsyncSession,
    identity: IdentityContext,
    server_id: str,
    target_department_id: str | None = None,
) -> dict:
    """Callback воркера: новые управляющие креды применены на боксе (rotate, #3).

    Снимает `mgmt_creds_pending_apply`, зануляет previous-зеркала (переходное
    окно закрыто — старый ключ на боксе больше не нужен) и проставляет
    `mgmt_creds_rotated_at`. С этого момента fetch отдаёт текущий материал.

    Доступ: `(server, *, prepare_callback)` — тот же callback-грант worker_bot'а,
    что и у `prepared`. Аудит: `server.management_creds_rotated` (CRITICAL).
    """
    try:
        await permissions.require_action(
            db, identity, EntityType.SERVER, Action.PREPARE_CALLBACK,
        )
    except AuthorizationError:
        audit_service.emit(
            "server.management_creds_rotated",
            target_id=server_id, target_type="server",
            status="denied", allowed=False,
            details={"reason": "permission_denied"},
        )
        raise
    server = await server_repo.get_by_id(db, server_id)
    if server is None:
        audit_service.emit(
            "server.management_creds_rotated",
            target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "server_not_found"},
        )
        raise NotFoundError(error_code="SERVER_NOT_FOUND", message="Server not found")
    _check_target_department_for_server(
        audit_action="server.management_creds_rotated",
        target_id=server_id,
        server_department_id=server.department_id,
        header_department_id=target_department_id,
        actor_department_id=identity.department_id,
    )
    rotated_at = datetime.now(timezone.utc)
    await server_repo.update(db, server, {
        "mgmt_creds_pending_apply": False,
        "previous_mgmt_ssh_private_key_encrypted": None,
        "previous_mgmt_password_encrypted": None,
        "mgmt_creds_rotated_at": rotated_at,
    })
    await db.commit()
    audit_service.emit(
        "server.management_creds_rotated",
        target_id=server_id, target_type="server",
        status="success", allowed=True,
        details={
            "rotated_at": rotated_at.isoformat(),
            "department_id": server.department_id,
            "caller_type": identity.subject_type,
        },
    )
    return {"ok": True, "rotated_at": rotated_at.isoformat()}


async def record_power_state(
    db: AsyncSession,
    identity: IdentityContext,
    server_id: str,
    payload: PowerStateCallbackRequest,
    target_department_id: str | None = None,
) -> dict:
    """Callback воркера: результат живой пробы питания (`power.status`).

    Одним callback'ом приходят три независимых сигнала доступности — ping, ssh
    и питание по BMC (ipmi). Каждый присланный (не None) сигнал пишется в свою
    тройку колонок с моментом приёма (UTC): `ping_*`, `ssh_*`, `ipmi_*`.
    Доступность в списке серверов ведётся по `ping_reachable`.

    Legacy-тройка `power_state`/`power_state_source`/`power_state_checked_at`
    остаётся рабочей, но anti-clobber: воркер шлёт callback всегда, в т.ч. с
    `power_state="unknown"`, и unknown НЕ должен затирать ранее закэшированное
    on/off. Поэтому legacy-тройку обновляем только когда `power_state != unknown`;
    ping/ssh/ipmi при этом записываются в любом случае.

    Доступ: `(server, *, prepare_callback)` — тот же callback-грант worker_bot'а,
    что и у `prepared`/`management-credentials/applied`. Аудит:
    `server.power_state_updated` (INFO — рутинный кэш-апдейт, reveal'а нет).
    """
    try:
        await permissions.require_action(
            db, identity, EntityType.SERVER, Action.PREPARE_CALLBACK,
        )
    except AuthorizationError:
        audit_service.emit(
            "server.power_state_updated",
            target_id=server_id, target_type="server",
            status="denied", allowed=False,
            details={"reason": "permission_denied"},
        )
        raise
    server = await server_repo.get_by_id(db, server_id)
    if server is None:
        audit_service.emit(
            "server.power_state_updated",
            target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "server_not_found"},
        )
        raise NotFoundError(error_code="SERVER_NOT_FOUND", message="Server not found")
    _check_target_department_for_server(
        audit_action="server.power_state_updated",
        target_id=server_id,
        server_department_id=server.department_id,
        header_department_id=target_department_id,
        actor_department_id=identity.department_id,
    )
    checked_at = datetime.now(timezone.utc)
    update: dict = {}

    # Legacy-тройка: обновляем только на определённом состоянии. unknown не
    # затирает ранее закэшированное on/off — воркер шлёт callback всегда, а
    # неизвестность не должна стирать последний достоверный факт.
    if payload.power_state != "unknown":
        update["power_state"] = payload.power_state
        update["power_state_source"] = payload.source
        update["power_state_checked_at"] = checked_at

    # Каждый присланный сигнал пишется независимо со своим временем приёма.
    if payload.ping_reachable is not None:
        update["ping_reachable"] = payload.ping_reachable
        update["ping_latency_ms"] = payload.ping_latency_ms
        update["ping_checked_at"] = checked_at
    if payload.ssh_reachable is not None:
        update["ssh_reachable"] = payload.ssh_reachable
        update["ssh_latency_ms"] = payload.ssh_latency_ms
        update["ssh_checked_at"] = checked_at
    if payload.ipmi_power_state is not None:
        update["ipmi_power_state"] = payload.ipmi_power_state
        update["ipmi_checked_at"] = checked_at

    if update:
        await server_repo.update(db, server, update)
        await db.commit()

    # Итоговое сводное состояние в кэше: свежее, если legacy-тройку обновили,
    # иначе то, что уже лежало (на unknown-callback'е кэш не трогали).
    cached_power_state = update.get("power_state", server.power_state)
    audit_service.emit(
        "server.power_state_updated",
        target_id=server_id, target_type="server",
        status="success", allowed=True,
        details={
            "power_state": payload.power_state,
            "source": payload.source,
            "ping_reachable": payload.ping_reachable,
            "ssh_reachable": payload.ssh_reachable,
            "ipmi_power_state": payload.ipmi_power_state,
            "checked_at": checked_at.isoformat(),
            "department_id": server.department_id,
            "caller_type": identity.subject_type,
        },
    )
    return {
        "ok": True,
        "power_state": cached_power_state,
        "checked_at": checked_at.isoformat(),
    }


# ── Worker callbacks (write-direction internal API) ─────────────────────────


async def _resolve_or_create_os(
    db: AsyncSession,
    name: str,
    *,
    repositories: list[str] | None = None,
    server_id: str | None = None,
    server_department_id: str | None = None,
    actor_subject_type: str | None = None,
) -> str | None:
    """Lookup OS-версии по name; INSERT при first-seen.

    Worker отдаёт `os_version` строкой из `/etc/os-release`. Имя обязано
    начинаться с одного из whitelist-префиксов (`KNOWN_OS_PREFIXES`) —
    иначе запись в каталоге НЕ создаётся, эмитим WARNING
    `os.unknown_observed` и возвращаем None. Inventory.sync продолжается
    без апдейта `server.os_version_id` (поле остаётся прежним).

    Известное имя → существующий flow: если в каталоге есть строка с тем
    же name — возвращаем её id; иначе INSERT с WARNING-аудитом
    `os_version.create` (есть авто-создание, оператор видит источник).

    `repositories` — снапшот активных репозиториев версии ОС с бокса.
    Семантика записи в каталог:

      * first-seen (INSERT) — пишем присланный список как есть (пустой у
        старого воркера — норма, колонка default '{}');
      * версия уже в каталоге и список непустой — перезаписываем хранимый
        снапшот (актуальная картина репозиториев версии), updated_at бампится
        onupdate'ом;
      * версия уже в каталоге, но список пустой — НЕ затираем существующие:
        старый воркер без поля либо сбой чтения sources.list не должны
        обнулять каталог.
    """
    name = normalize_os_version_name(name)
    repositories = list(repositories or [])
    if not is_known_os(name):
        logger.warning(
            "unknown OS observed: %r from server %s — skip create",
            name, server_id,
        )
        audit_service.emit(
            "os.unknown_observed",
            target_id=server_id,
            target_type="server",
            status="warning",
            allowed=True,
            details={
                "reason": "os_not_in_whitelist",
                "os_name": name,
                "server_id": server_id,
                "server_department_id": server_department_id,
                "actor_subject_type": actor_subject_type,
            },
        )
        return None
    # Параллельный inventory с двух серверов на одну новую OS-версию: чистый
    # get-then-create ловил бы IntegrityError на UNIQUE(name). ON CONFLICT
    # DO NOTHING + re-SELECT возвращает уже вставленную row без race-window.
    obj, created = await osv_repo.create_if_absent(db, {
        "id": os_version_id(),
        "name": name,
        "repositories": repositories,
    })
    if created:
        audit_service.emit(
            "os_version.create",
            target_id=obj.id,
            target_type="os_version",
            status="warning",
            allowed=True,
            details={
                "reason": "auto_from_inventory",
                "name": name,
                "server_id": server_id,
                "server_department_id": server_department_id,
            },
        )
    elif repositories:
        # Версия уже в каталоге — обновляем снапшот репозиториев только на
        # непустом списке. Пустой не затираем (см. docstring).
        await osv_repo.update(db, obj, {"repositories": repositories})
    return obj.id


async def _upsert_disks(
    db: AsyncSession, server_id: str, items: list,
) -> int:
    """Bulk-upsert по (server_id, device_name) через PostgreSQL ON CONFLICT.

    Возвращает счётчик затронутых строк (INSERT + UPDATE). is_system-инвариант
    (ровно один system disk на server) гарантирует partial unique index в
    миграции, дубли отбиваются IntegrityError'ом на уровне БД.

    Каждый item летит как `INSERT ... ON CONFLICT DO UPDATE` — один SQL,
    атомарно, без savepoint'ов. Повторный callback от worker'а (HTTP-таймаут
    → retry) и параллельные callback'и обрабатываются БД, не приложением.
    """
    touched = 0
    for item in items:
        await disk_repo.upsert_by_device(db, {
            "id": server_disk_id(),
            "server_id": server_id,
            "device_name": item.name,
            "size_gb": item.size_gb,
            "used_gb": item.used_gb,
            "used_percent": item.used_percent,
            "model": item.model,
            "is_system": item.is_system,
        })
        touched += 1
    return touched


# Скалярные hardware-факты сервера, которые приходят из inventory-callback'а и
# подчиняются модели «БД-истина, warn-on-drift»: first-write сохраняем, drift
# по уже заполненному полю эмитим WARNING и НЕ перетираем.
#
# Чего тут НЕТ намеренно:
#   * `os_version` — версию ОС бокс обновляет через каталог `os_versions`
#     (`_resolve_or_create_os`); это осознанное box→DB исключение.
#   * `hostname` — адресная identity сервера (`nullable=False`, задаётся при
#     регистрации, глобально уникален). Inventory остаётся authoritative для
#     hostname'а как раньше; warn-on-drift к нему не применяем (он никогда не
#     NULL, под first-write не попадает — иначе любая первая инвентаризация
#     считалась бы drift'ом). Вынесено как вопрос владельцу — см. reports/.
#   * disks — своя upsert-таблица `server_disks`.
_INVENTORY_DRIFT_FIELDS = (
    "cpu_brand",
    "cpu_model",
    "cpu_cores",
    "cpu_threads",
    "cpu_frequency_ghz",
    "ram_total_mb",
)


def _inventory_field_changes(server, payload) -> tuple[dict, dict]:
    """Разложить hardware-факты бокса на first-write и drift относительно БД.

    Возвращает `(first_write, drift)`:

    * `first_write` — `{field: new}` для полей, где в БД пусто (None) — их
      сохраняем (это и есть первичное наполнение инвентаря).
    * `drift` — `{field: {"old": <БД>, "new": <бокс>}}` для полей, где в БД
      уже есть значение и оно расходится с фактом бокса. Эти поля НЕ
      перетираем; вызывающий эмитит по ним WARNING.

    Совпадающие значения не попадают ни в один словарь — менять нечего.
    `cpu_frequency_ghz` сравнивается как float; остальные — по равенству.
    """
    first_write: dict = {}
    drift: dict = {}
    for field in _INVENTORY_DRIFT_FIELDS:
        # default=None: на ORM-строке колонка всегда есть, но в тестовых
        # стабах сервера её может не быть — трактуем отсутствие как «значения
        # нет» (first-write по факту бокса), не падаем на AttributeError.
        old = getattr(server, field, None)
        new = getattr(payload, field, None)
        if old is None:
            # First-write: значения ещё не было. None в payload'е (cpu_brand и
            # т.п. опциональны) тоже не пишем — записывать None поверх None
            # незачем, и это не «факт», а отсутствие данных.
            if new is not None:
                first_write[field] = new
            continue
        if old != new:
            drift[field] = {"old": old, "new": new}
    return first_write, drift


async def receive_inventory(
    db: AsyncSession,
    identity: IdentityContext,
    server_id: str,
    payload: InventoryCallbackRequest,
    target_department_id: str | None = None,
) -> dict:
    """Принять hardware-facts от worker'а после `inventory.sync`.

    Поток: permission check (`server:inventory_submit`) → server lookup → dept
    cross-check → upsert os_versions по имени → апдейт CPU-полей и
    hostname/cpu_*/os_version_id на server → bulk-upsert дисков → commit →
    audit `server.inventory_received`. CPU-данные пишутся плоско в строку
    `servers` (cpu_brand/cpu_model/cpu_cores/cpu_threads/cpu_frequency_ghz),
    отдельной таблицы-каталога нет.
    """
    try:
        await permissions.require_action(
            db, identity, EntityType.SERVER, Action.INVENTORY_SUBMIT,
        )
    except AuthorizationError:
        audit_service.emit(
            "server.inventory_received",
            target_id=server_id, target_type="server",
            status="denied", allowed=False,
            details={"reason": "permission_denied"},
        )
        raise

    # Dept-check ВЫШЕ existence-проверки: `_check_target_department_for_server`
    # на header-mismatch отвечает 404 SERVER_NOT_FOUND — как и валидный caller
    # на несуществующем server_id, оба исхода неотличимы, факт существования
    # сервера в чужом dept не утекает. Несуществующий server_id обрабатываем ДО
    # dept-check'а — иначе SIEM ловит фейковый target_department_mismatch
    # (server_dept=None).
    server = await server_repo.get_by_id(db, server_id)
    if server is None:
        audit_service.emit(
            "server.inventory_received",
            target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "server_not_found"},
        )
        raise NotFoundError(
            error_code="SERVER_NOT_FOUND", message="Server not found",
        )
    server_department_id = server.department_id
    _check_target_department_for_server(
        audit_action="server.inventory_received",
        target_id=server_id,
        server_department_id=server_department_id,
        header_department_id=target_department_id,
        actor_department_id=identity.department_id,
    )

    os_id_resolved = await _resolve_or_create_os(
        db,
        payload.os_version,
        repositories=payload.repositories,
        server_id=server_id,
        server_department_id=server.department_id,
        actor_subject_type=identity.subject_type,
    )

    # Warn-on-drift по hardware-полям: БД — источник истины. First-write
    # (хранимое значение пустое) сохраняем; расхождение хранимого с фактом
    # бокса НЕ перетираем, а эмитим WARNING `inventory.drift_detected`
    # (что/old/new) — оператор разбирается вручную. `os_version` — осознанное
    # исключение: каталожную привязку `os_version_id` бокс обновляет
    # (`_resolve_or_create_os`), drift по ней не считаем.
    # hostname остаётся authoritative с бокса (адресная identity, не warn-on-
    # drift факт) — апдейтим как раньше. os_version_id — box→DB исключение.
    server_update: dict = {
        "hostname": payload.hostname,
        "os_last_synced_at": datetime.now(timezone.utc),
    }
    if os_id_resolved is not None:
        server_update["os_version_id"] = os_id_resolved
    # Режим безопасности — per-server факт с бокса. Обновляем на каждом
    # непустом значении; None (старый воркер без поля) существующее не затирает,
    # симметрично семантике repositories.
    if payload.os_security_mode is not None:
        server_update["os_security_mode"] = payload.os_security_mode

    # Аппаратная виртуализация (KVM) — булев факт детекта с бокса. Здесь
    # inventory авторитетен (не warn-on-drift): непустое значение пишем как
    # есть, оно гейтит подготовку VMS-hub. None (старый воркер без поля)
    # существующее не затирает.
    if payload.virtualization is not None:
        server_update["virtualization"] = payload.virtualization

    # Сетевые интерфейсы — box-authoritative факт (как os_version_id): непустой
    # список с бокса перезаписывает хранимый, пустой/отсутствующий (старый
    # воркер) существующее не трогает. Основной интерфейс
    # (`network_interface_name`) остаётся admin-editable: заполняем его первым
    # именем только first-write (когда в БД пусто), уже заданное вручную не
    # перетираем.
    if payload.network_interfaces:
        server_update["network_interfaces"] = payload.network_interfaces
        if getattr(server, "network_interface_name", None) is None:
            server_update["network_interface_name"] = payload.network_interfaces[0]

    first_write, drift = _inventory_field_changes(server, payload)
    server_update.update(first_write)
    await server_repo.update(db, server, server_update)
    disks_count = await _upsert_disks(db, server_id, payload.disks)
    await db.commit()

    if drift:
        audit_service.emit(
            "inventory.drift_detected",
            target_id=server_id, target_type="server",
            status="warning", allowed=True,
            details={
                "fields": sorted(drift.keys()),
                "drift": drift,
                "department_id": server.department_id,
                "caller_type": identity.subject_type,
            },
        )

    audit_service.emit(
        "server.inventory_received",
        target_id=server_id, target_type="server",
        status="success", allowed=True,
        details={
            "hostname": payload.hostname,
            "cpu_brand": payload.cpu_brand,
            "cpu_model": payload.cpu_model,
            "os_version": payload.os_version,
            "disks": disks_count,
            "first_write_fields": sorted(first_write.keys()),
            "drift_fields": sorted(drift.keys()),
            "department_id": server.department_id,
            "caller_type": identity.subject_type,
        },
    )
    return {
        "ok": True,
        "os_version_id": os_id_resolved,
        "disks_upserted": disks_count,
        "first_write_fields": sorted(first_write.keys()),
        "drift_fields": sorted(drift.keys()),
    }


# Атрибуты OS-пользователя, чьё расхождение бокса с БД считается дрейфом.
#
# `home_dir` намеренно НЕ в списке: PATCH home_dir не запускает fan-out
# (worker не двигает $HOME), а inventory эмитил бы drift на каждом скане —
# оператор получал бы шум, который ничем не закрыть. Симметрично `home_dir`
# исключён из `_OS_MANAGED_FIELDS` для fanout-payload в server_accounts.py.
_DRIFT_ATTRS = ("has_sudo", "unix_groups", "shell")


def _account_attr_drift(account, item) -> dict:
    """Сравнить атрибуты бокса (`item`) с записью в БД (`account`).

    БД — источник истины: возвращаем поля, по которым бокс разошёлся, в виде
    `{field: {"expected": <БД>, "found": <бокс>}}`. Группы сравниваем как
    множества — порядок и дубли в выводе getent не значимы. Пустой dict —
    расхождений нет.

    `home_dir` сюда не входит — см. комментарий к `_DRIFT_ATTRS`.
    """
    diff: dict = {}
    if bool(account.has_sudo) != bool(item.has_sudo):
        diff["has_sudo"] = {"expected": account.has_sudo, "found": item.has_sudo}
    if set(account.unix_groups or []) != set(item.unix_groups or []):
        diff["unix_groups"] = {
            "expected": list(account.unix_groups or []),
            "found": list(item.unix_groups or []),
        }
    if account.shell != item.shell:
        diff["shell"] = {"expected": account.shell, "found": item.shell}
    return diff


async def receive_users_inventory(
    db: AsyncSession,
    identity: IdentityContext,
    server_id: str,
    payload: UsersInventoryCallbackRequest,
    target_department_id: str | None = None,
) -> dict:
    """Принять список реальных OS-пользователей от worker'а и reconcile'ить
    его против привязанных к серверу `server_accounts`.

    Право: `(server_account, *, inventory_submit)` — узкий грант worker_bot'а.

    Модель shared/M2M: один аккаунт держит одинаковые атрибуты на всех
    привязанных серверах, истина — БД. Инвентаризация фиксирует факт-состояние
    бокса, но НЕ перетирает поля аккаунта — расхождение поднимает WARNING-аудит
    `server_account.drift_detected`, чтобы оператор разобрался вручную.

    Reconcile (для инвентаризуемого сервера X):

      * найден на X, нет привязанного аккаунта, НЕ в ignore-list'е отдела, но
        под этот login в ОТДЕЛЕ уже есть аккаунт (просто не привязан к X) →
        логин уходит в `unlinked_existing` со списком кандидатов; НЕ линкуем и
        НЕ создаём, drift не поднимаем (оператор свяжет вручную через UI);
      * найден на X, нет привязанного аккаунта, НЕ в ignore-list'е отдела, и под
        login аккаунта в отделе вообще нет → discovered-аккаунт НЕ создаётся;
        логин уходит в `unknown_users` ответа (оператор решает: импортировать
        или заигнорить). По-прежнему поднимаем drift-сигнал `unknown_login`
        (на боксе живёт неуправляемый юзер);
      * найден на X, но логин в ignore-list'е отдела → пропускаем целиком (не
        в unknown_users, не дрейфим);
      * есть и там, и в API → пометить связку present + свежий
        `last_inventory_at`; если атрибуты бокса разошлись с БД — drift, поля
        аккаунта НЕ трогаем;
      * привязан в API, но не найден на сервере → drift + пометить связку
        `present_on_server=False`, запись НЕ удаляем.
    """
    # Permission ВЫШЕ existence/dept: caller без grant'а получает 403 ровно
    # такой же, как same-dept caller без grant'а, и факт существования
    # сервера в чужом dept не утекает. Same-dept caller с grant'ом проходит
    # дальше к existence+dept-check'у — там 404 SERVER_NOT_FOUND одинаково
    # покрывает «не твой dept» и «не существует».
    try:
        await permissions.require_action(
            db, identity, EntityType.SERVER_ACCOUNT, Action.INVENTORY_SUBMIT,
        )
    except AuthorizationError:
        audit_service.emit(
            "server_account.users_inventory_received",
            target_id=server_id, target_type="server",
            status="denied", allowed=False,
            details={"reason": "permission_denied"},
        )
        raise
    server = await server_repo.get_by_id(db, server_id)
    if server is None:
        audit_service.emit(
            "server_account.users_inventory_received",
            target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "server_not_found"},
        )
        raise NotFoundError(
            error_code="SERVER_NOT_FOUND", message="Server not found",
        )
    server_department_id = server.department_id
    _check_target_department_for_server(
        audit_action="server_account.users_inventory_received",
        target_id=server_id,
        server_department_id=server_department_id,
        header_department_id=target_department_id,
        actor_department_id=identity.department_id,
    )

    # Снимок текущих связок сервера + login'ов, найденных на боксе.
    links = await account_repo.list_links_for_server(db, server_id)
    seen_logins = {item.login for item in payload.users}
    # Ignore-list отдела сервера: эти логины reconcile полностью пропускает —
    # ни в unknown_users, ни в drift.
    ignored_logins = await ignored_login_repo.ignored_logins_for_department(
        db, server.department_id,
    )
    # Управляющую учётку сервера (та, под которой ходим по SSH) reconcile не
    # классифицирует никогда — она наша, а не пользовательская. Добавляем поверх
    # dept ignore-list'а, dept-запись в БД при этом не трогаем — расширяем только
    # локальный set на время этой инвентаризации.
    if server.management_user:
        ignored_logins = ignored_logins | {server.management_user}

    # Батчим выборки на N юзеров: один SELECT по login'ам, один по account_id'ам
    # их связок. Без батча reconcile делает 2·N запросов и проседает на больших
    # инвентаризациях.
    inventoried_logins = [item.login for item in payload.users]
    existing_by_login = await account_repo.list_accounts_on_server_by_logins(
        db, server_id, inventoried_logins,
    )
    links_by_account_id = await account_repo.list_links_for_server_by_account_ids(
        db, server_id, [acc.id for acc in existing_by_login.values()],
    )
    # Кандидаты на связку: аккаунты этого отдела с тем же login'ом, но НЕ
    # привязанные к инвентаризуемому серверу. Нужны, чтобы отличить «логин, под
    # который аккаунт в отделе уже есть» от настоящего unknown. Линковать и
    # создавать ничего не будем — только классифицируем для ответа.
    dept_accounts_by_login = await account_repo.list_accounts_in_department_by_logins(
        db, server.department_id, inventoried_logins,
    )

    created = 0
    present = 0
    drifted = 0
    # Незнакомые юзеры (на боксе есть, не привязаны, не в ignore-list'е,
    # аккаунта под login в отделе вовсе нет). Возвращаем оператору —
    # discovered-аккаунт больше НЕ заводим автоматически.
    unknown_users: list[dict] = []
    # Логины, под которые в отделе УЖЕ есть аккаунт, просто не привязанный к
    # этому серверу. Inventory сюда ничего не линкует — отдаёт оператору, UI
    # предложит связать существующий аккаунт с сервером.
    unlinked_existing: list[dict] = []
    # Дрейф эмитим после commit'а — события best-effort, в транзакцию не входят.
    drift_emits: list[dict] = []
    # Структурированный per-account diff с самими значениями (expected/found)
    # для существующих привязанных аккаунтов с расхождением атрибутов. Уходит
    # в ответ, чтобы worker положил его в `task.result`, а UI показал оператору
    # для ручного ревью. БД при этом НЕ перетирается — политика warn-on-drift.
    attr_diffs: list[dict] = []
    # Аккумулируем account_id'ы под bulk-апдейты в конце цикла — один UPDATE
    # на N связок вместо N flush'ей в `mark_link_inventoried`.
    present_account_ids: list[str] = []
    missing_account_ids: list[str] = []

    for item in payload.users:
        # Логин в ignore-list'е отдела (или управляющая учётка сервера) —
        # штатная служебная учётка, которую reconcile не классифицирует:
        # ни drift, ни unknown_users, ни unlinked_existing.
        if item.login in ignored_logins:
            continue
        existing = existing_by_login.get(item.login)
        if existing is None:
            # На боксе есть, к этому серверу не привязан, не заигнорен.
            # Если под этот login в отделе УЖЕ есть аккаунт — это не unknown,
            # а «существующий, но не привязанный»: отдаём отдельной категорией,
            # чтобы UI предложил связать. Login не уникален в отделе — кандидатов
            # может быть несколько, отдаём списком, не падаем.
            candidates = dept_accounts_by_login.get(item.login)
            if candidates:
                unlinked_existing.append({
                    "login": item.login,
                    "uid": item.uid,
                    "candidates": [
                        {
                            "account_id": acc.id,
                            "department_id": acc.department_id,
                            "source": acc.source,
                        }
                        for acc in candidates
                    ],
                })
                continue
            # Настоящий unknown: ни привязки, ни аккаунта под login в отделе.
            # discovered-аккаунт НЕ создаём — отдаём оператору в unknown_users,
            # он сам решит (импорт / игнор). Сигнал drift всё равно поднимаем.
            unknown_users.append({
                "login": item.login,
                "uid": item.uid,
                "has_sudo": item.has_sudo,
                "unix_groups": list(item.unix_groups),
                "shell": item.shell,
            })
            drifted += 1
            drift_emits.append({
                "login": item.login,
                "drift": "unknown_login",
            })
        else:
            # БД — истина: атрибуты аккаунта НЕ перетираем, только presence.
            diff = _account_attr_drift(existing, item)
            if existing.id in links_by_account_id:
                present_account_ids.append(existing.id)
            present += 1
            if diff:
                drifted += 1
                drift_emits.append({
                    "login": item.login,
                    "drift": "attributes",
                    "fields": sorted(diff.keys()),
                    "diff": diff,
                })
                attr_diffs.append({
                    "account_id": existing.id,
                    "login": existing.login,
                    "fields": diff,
                })

    # Привязанные в API, но не найденные на сервере — drift.
    # Emit идёт только при переходе present_on_server: True → False — это
    # «свежий» drift на конкретном скане. Если link уже был помечен False в
    # прошлый раз и юзера на боксе всё ещё нет, повторно эмитить нечего:
    # иначе каждый периодический скан плодил бы дубликат warning'ов в audit.
    # `is_new_drift` уезжает в details, чтобы потребители (loging_service /
    # отчёты) могли отличить первое срабатывание от прежнего состояния.
    for link in links:
        if link.login in ignored_logins:
            continue
        if link.login not in seen_logins:
            missing_account_ids.append(link.account_id)
            is_new_drift = link.present_on_server is True
            if is_new_drift:
                drifted += 1
                drift_emits.append({
                    "login": link.login,
                    "drift": "missing_on_box",
                    "is_new_drift": True,
                })

    # Bulk-flush presence: два statement'а вместо N flush'ей в цикле.
    if present_account_ids:
        await account_repo.mark_links_inventoried_bulk(
            db, server_id, present_account_ids, present=True,
        )
    if missing_account_ids:
        await account_repo.mark_links_inventoried_bulk(
            db, server_id, missing_account_ids, present=False,
        )

    await db.commit()

    for emit in drift_emits:
        details = {
            "server_id": server_id,
            "login": emit["login"],
            "drift": emit["drift"],
            "department_id": server.department_id,
        }
        if "fields" in emit:
            details["fields"] = emit["fields"]
        if "diff" in emit:
            details["expected"] = {f: v["expected"] for f, v in emit["diff"].items()}
            details["found"] = {f: v["found"] for f, v in emit["diff"].items()}
        if "is_new_drift" in emit:
            details["is_new_drift"] = emit["is_new_drift"]
        audit_service.emit(
            "server_account.drift_detected",
            target_id=server_id, target_type="server_account",
            status="warning", allowed=True,
            details=details,
        )

    audit_service.emit(
        "server_account.users_inventory_received",
        target_id=server_id, target_type="server",
        status="success", allowed=True,
        details={
            "created": created,
            "present": present,
            "drifted": drifted,
            "found": len(payload.users),
            "unknown": len(unknown_users),
            "unlinked_existing": len(unlinked_existing),
            "department_id": server.department_id,
            "caller_type": identity.subject_type,
        },
    )

    # Сводный result_summary — worker сохранит его в `tasks.result_payload`,
    # либо клиент построит drift-отчёт через `GET /servers/{id}/drift`.
    drift_items = [
        {
            "login": emit["login"],
            "drift_type": emit["drift"],
            "fields": emit.get("fields"),
        }
        for emit in drift_emits
    ]
    return {
        "ok": True,
        "created": created,
        "present": present,
        "drifted": drifted,
        "diffs": attr_diffs,
        "unknown_users": unknown_users,
        "unlinked_existing": unlinked_existing,
        "result_summary": {
            "total_users": len(payload.users),
            "created_discovered": created,
            "drifts": drift_items,
        },
    }


async def record_provision_status(
    db: AsyncSession,
    identity: IdentityContext,
    server_id: str,
    account_id: str,
    payload: ProvisionStatusRequest,
    target_department_id: str | None = None,
) -> dict:
    """Зафиксировать результат useradd/usermod/userdel на боксе (callback worker'а).

    Право: `(server_account, *, provision_on_host)` — узкий грант worker_bot'а.

    Обновляет `present_on_server` на связке аккаунт ↔ сервер: provision/update
    → True, deprovision → False. `last_inventory_at` не трогается — это не
    инвентаризация. Аккаунт обязан быть привязан к серверу, иначе 404.
    """
    # Permission ВЫШЕ existence/dept: caller без grant'а получает 403
    # permission_denied вне зависимости от того, в каком dept'е сервер —
    # факт привязки account_id к серверу чужого dept не утекает. Само
    # cross-dept смешение остаётся скрытым за 404 ACCOUNT_NOT_FOUND ниже
    # (`_check_target_department_for_account` отвечает 404 ACCOUNT_NOT_FOUND).
    try:
        await permissions.require_action(
            db, identity, EntityType.SERVER_ACCOUNT, Action.PROVISION_ON_HOST,
        )
    except AuthorizationError:
        audit_service.emit(
            "server_account.provision_status",
            target_id=account_id, target_type="server_account",
            status="denied", allowed=False,
            details={"reason": "permission_denied", "server_id": server_id},
        )
        raise
    server = await server_repo.get_by_id(db, server_id)
    if server is None:
        audit_service.emit(
            "server_account.provision_status",
            target_id=account_id, target_type="server_account",
            status="failure", allowed=True,
            details={"reason": "server_not_found", "server_id": server_id},
        )
        raise NotFoundError(
            error_code="ACCOUNT_NOT_FOUND",
            message="Server account not found on this server",
        )
    server_department_id = server.department_id
    _check_target_department_for_account(
        audit_action="server_account.provision_status",
        target_id=account_id,
        server_department_id=server_department_id,
        header_department_id=target_department_id,
        actor_department_id=identity.department_id,
        extra_details={"server_id": server_id},
    )

    # FOR UPDATE на server_accounts — fan-out из mass-rotation может прислать
    # несколько callback'ов в одном окне; без row-lock'а параллельные UPDATE
    # `credentials_pending_apply = False` гонятся между собой и с user-facing
    # `update_account` / linkage-операциями (последние тоже берут лок через
    # `get_for_update`). Лок сериализует их без потерь.
    account = await account_repo.get_for_update(db, account_id)
    link = await account_repo.get_link(db, account_id, server_id)
    if account is not None and link is None and not payload.present:
        # Deprovision-callback на уже снятой связке: user-facing deprovision /
        # unbind удаляют связку сразу при постановке userdel'а, не дожидаясь
        # callback'а. К моменту, как worker подтвердит `present=False`, связки
        # уже нет — но целевое состояние (учётки на сервере нет) достигнуто,
        # поэтому отвечаем идемпотентным 200, а не 404.
        audit_service.emit(
            "server_account.provision_status",
            target_id=account_id, target_type="server_account",
            status="success", allowed=True,
            details={
                "reason": "link_already_removed",
                "server_id": server_id,
                "operation": payload.operation,
                "present_on_server": False,
                "department_id": server_department_id,
                "caller_type": identity.subject_type,
            },
        )
        return {"ok": True, "present_on_server": False}
    if account is None or link is None:
        audit_service.emit(
            "server_account.provision_status",
            target_id=account_id, target_type="server_account",
            status="failure", allowed=True,
            details={"reason": "account_not_found", "server_id": server_id},
        )
        raise NotFoundError(
            error_code="ACCOUNT_NOT_FOUND",
            message="Server account not found on this server",
        )

    # Defense-in-depth: dept-pair sanity check. Аккаунт и сервер обязаны
    # быть в одном dept'е — link (`server_account_servers`) теоретически
    # позволил бы пробросить cross-dept linkage'у через прямой UPDATE'ом
    # репозитория мимо доменных guard'ов. На callback'е воркера это финальная
    # точка верификации перед записью в БД: рассинхрон рассматриваем как
    # incident, в БД не пишем, возвращаем 422 INVALID_DEPT_PAIR.
    if account.department_id != server_department_id:
        audit_service.emit(
            "server_account.provision_status",
            target_id=account_id, target_type="server_account",
            status="failure", allowed=True,
            details={
                "reason": "invalid_dept_pair",
                "server_id": server_id,
                "server_department_id": server_department_id,
                "account_department_id": account.department_id,
            },
        )
        raise ConflictError(
            error_code="INVALID_DEPT_PAIR",
            message=(
                "Server account department does not match server department; "
                "cross-department linkage is not allowed"
            ),
        )

    await account_repo.set_link_presence(db, link, present=payload.present)
    # Callback от worker'а подтверждает, что credentials, сгенерированные на
    # dispatch'е и сохранённые в server_accounts.password_encrypted /
    # ssh_private_key_encrypted, доехали до бокса. Семантика fan-out'а: pending_apply
    # снимается первым успешным callback'ом любого сервера в группе — это
    # «доехало хотя бы до одного». Per-server состояние live видно через
    # `server_account_servers.present` (set_link_presence выше); pending_apply —
    # короткий drift-флаг для UI «свежевыданные creds в процессе раскатки».
    #
    # Снимать флаг можно только на provision/update (`present=True`) — именно они
    # подтверждают, что новый материал реально доехал на бокс. Deprovision
    # (`present=False`) лишь удаляет пользователя и ничего не применяет: если
    # параллельно идёт mass-rotate, его pending снимать на deprovision-callback'е
    # нельзя — иначе переходный период ротации закроется до того, как новый
    # пароль раскатался хоть куда-то, и удержанный previous занулится зря.
    if payload.present and account.credentials_pending_apply:
        account.credentials_pending_apply = False
        # Переходный период ротации завершён: новый пароль доехал хотя бы до
        # одного сервера группы. Удержанный прежний пароль больше не нужен —
        # зануляем его вместе со снятием флага (тот же row под FOR UPDATE).
        account.previous_password_encrypted = None
        account.previous_password_rotated_at = None
        await db.flush()
    await db.commit()

    audit_service.emit(
        "server_account.provision_status",
        target_id=account_id, target_type="server_account",
        status="success", allowed=True,
        details={
            "server_id": server_id,
            "login": account.login,
            "operation": payload.operation,
            "present_on_server": payload.present,
            "department_id": server_department_id,
            "caller_type": identity.subject_type,
        },
    )
    return {"ok": True, "present_on_server": payload.present}


async def record_server_prepared(
    db: AsyncSession,
    identity: IdentityContext,
    server_id: str,
    payload: ServerPrepareCallbackRequest,
    target_department_id: str | None = None,
) -> dict:
    """Зафиксировать завершение бутстрапа управления сервером (callback worker'а).

    Право: `(server, *, prepare_callback)` — узкий грант worker_bot'а.

    Помечает сервер подготовленным: `is_managed=True`, `prepared_at=now`,
    `management_user=<имя>`, плюс `management_mode` (детектнутая воркером
    редакция ОС), если он пришёл в callback'е. `management_mode=None` (старый
    воркер / детект не отработал) прежнее значение не перетирает. Идемпотентно:
    повторный callback просто переписывает те же поля. Аудит — CRITICAL.
    """
    # Permission ВЫШЕ existence/dept: caller без grant'а получает 403
    # permission_denied вне зависимости от того, в каком dept'е сервер —
    # факт существования сервера в чужом dept не утекает через 404/403 enum.
    try:
        await permissions.require_action(
            db, identity, EntityType.SERVER, Action.PREPARE_CALLBACK,
        )
    except AuthorizationError:
        audit_service.emit(
            "server.prepared",
            target_id=server_id, target_type="server",
            status="denied", allowed=False,
            details={"reason": "permission_denied"},
        )
        raise
    # Dept-check ВЫШЕ existence-проверки: `_check_target_department_for_server`
    # на header-mismatch отвечает 404 SERVER_NOT_FOUND, факт существования
    # сервера в чужом dept не утекает. Несуществующий server_id обрабатываем ДО
    # dept-check'а — иначе SIEM ловит фейковый target_department_mismatch на
    # тычках в air.
    server = await server_repo.get_by_id(db, server_id)
    if server is None:
        audit_service.emit(
            "server.prepared",
            target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "server_not_found"},
        )
        raise NotFoundError(
            error_code="SERVER_NOT_FOUND", message="Server not found",
        )
    server_department_id = server.department_id
    _check_target_department_for_server(
        audit_action="server.prepared",
        target_id=server_id,
        server_department_id=server_department_id,
        header_department_id=target_department_id,
        actor_department_id=identity.department_id,
    )

    prepared_at = datetime.now(timezone.utc)
    updates: dict = {
        "is_managed": True,
        "management_user": payload.management_user,
        "prepared_at": prepared_at,
        # Per-server управляющие креды (#3) сгенерены и положены в БД на
        # dispatch'е prepare с pending_apply=True; этот callback подтверждает,
        # что воркер их применил на боксе (authorized_keys + chpasswd), —
        # снимаем флаг. fetch с этого момента отдаёт текущий материал.
        "mgmt_creds_pending_apply": False,
    }
    if server.busy_state == BusyState.ACS:
        # Этот prepare — авто-диспатч после успешного restore снимка ACS
        # (`record_acs_snapshot_restore_done`), который намеренно держал
        # busy_state=acs до сих пор. Обычный (не-ACS) prepare сюда не
        # заходит: busy_state уже free/что угодно другое, ветка не трогает
        # остальной прежний путь функции. Возвращаем бронь к тому, что было
        # до всей цепочки restore→prepare, а не сбрасываем в free безусловно.
        reservation.restore_pre_acs_state(server)
        updates["busy_state"] = server.busy_state
        updates["busy_user_id"] = server.busy_user_id
        updates["busy_since"] = server.busy_since
        updates["busy_note"] = server.busy_note
        updates["pre_acs_busy_snapshot"] = server.pre_acs_busy_snapshot
    # `management_mode` пишем только если воркер его прислал — None оставляет
    # прежнее значение (старый воркер без детекта не должен затирать режим).
    if payload.management_mode is not None:
        updates["management_mode"] = payload.management_mode.value
    await server_repo.update(db, server, updates)
    await db.commit()

    audit_service.emit(
        "server.prepared",
        target_id=server_id, target_type="server",
        status="success", allowed=True,
        details={
            "management_user": payload.management_user,
            "management_mode": (
                payload.management_mode.value
                if payload.management_mode is not None else None
            ),
            "prepared_at": prepared_at.isoformat(),
            "department_id": server.department_id,
            "caller_type": identity.subject_type,
        },
    )
    # Авто-обновление inventory + power сразу после prepare: оператору не надо
    # жать вручную. Best-effort — фейл диспатчей не откатывает уже завершённый
    # prepare-callback (сервер managed). Свои audit'ы эмитятся внутри
    # (`server.inventory_sync` / `server.power_status`, source=auto_prepared).
    try:
        await auto_inventory.refresh_server(
            db, server,
            source=auto_inventory.SOURCE_PREPARED,
            actor_id=identity.user_id,
        )
    except Exception:  # noqa: BLE001 — авто-рефреш не должен валить callback
        logger.warning(
            "auto inventory/power refresh after prepare failed server_id=%s",
            server_id, exc_info=True,
        )
    return {
        "ok": True,
        "is_managed": True,
        "prepared_at": prepared_at.isoformat(),
    }


async def record_server_astra_updated(
    db: AsyncSession,
    identity: IdentityContext,
    server_id: str,
    payload: ServerAstraUpdateCallbackRequest,
    target_department_id: str | None = None,
) -> dict:
    """Зафиксировать исход обновления ОС (callback воркера astra_update).

    Право: `(server, *, prepare_callback)` — узкий грант worker_bot'а, общий с
    prepared-callback'ом.

    Снимает updating-блокировку (busy → free) в любом исходе. При
    `succeeded=True` дополнительно привязывает сервер к целевой версии ОС
    (если она ещё есть в каталоге) и best-effort запускает inventory.sync,
    чтобы освежить факты/режим после обновления. При `succeeded=False`
    версию не трогает и inventory не гоняет — сервер просто освобождается.
    Идемпотентно: повторный callback на уже освобождённом сервере снова
    выставит те же поля.
    """
    try:
        await permissions.require_action(
            db, identity, EntityType.SERVER, Action.PREPARE_CALLBACK,
        )
    except AuthorizationError:
        audit_service.emit(
            "server.astra_updated",
            target_id=server_id, target_type="server",
            status="denied", allowed=False,
            details={"reason": "permission_denied"},
        )
        raise
    server = await server_repo.get_by_id(db, server_id)
    if server is None:
        audit_service.emit(
            "server.astra_updated",
            target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "server_not_found"},
        )
        raise NotFoundError(
            error_code="SERVER_NOT_FOUND", message="Server not found",
        )
    _check_target_department_for_server(
        audit_action="server.astra_updated",
        target_id=server_id,
        server_department_id=server.department_id,
        header_department_id=target_department_id,
        actor_department_id=identity.department_id,
    )

    # Блокировку снимаем всегда — сервер снова доступен операторам. Прочие
    # busy-поля обнуляем симметрично clear_busy.
    updates: dict = {
        "busy_state": BusyState.FREE,
        "busy_user_id": None,
        "busy_since": None,
        "busy_note": None,
    }
    applied_os_version_id: str | None = None
    if payload.succeeded:
        # Привязываем сервер к целевой версии, только если она ещё в каталоге:
        # прямая запись несуществующего FK упала бы IntegrityError'ом. Версию
        # мог удалить админ, пока шло обновление — тогда просто не трогаем
        # os_version_id, inventory.sync позже переопределит по факту с бокса.
        target_version = await osv_repo.get_by_id(db, payload.os_version_id)
        if target_version is not None:
            updates["os_version_id"] = target_version.id
            updates["os_last_synced_at"] = datetime.now(timezone.utc)
            applied_os_version_id = target_version.id
    await server_repo.update(db, server, updates)
    await db.commit()

    audit_service.emit(
        "server.astra_updated",
        target_id=server_id, target_type="server",
        status="success" if payload.succeeded else "failure",
        allowed=True,
        details={
            "succeeded": payload.succeeded,
            "os_version_id": applied_os_version_id,
            "requested_os_version_id": payload.os_version_id,
            "department_id": server.department_id,
            "caller_type": identity.subject_type,
        },
    )
    # Успешное обновление — освежаем inventory + power, чтобы подхватить новую
    # версию/режим с бокса. Best-effort: фейл диспатчей не откатывает уже
    # снятую блокировку. На проваленном обновлении inventory не гоняем.
    if payload.succeeded:
        try:
            await auto_inventory.refresh_server(
                db, server,
                source=auto_inventory.SOURCE_PREPARED,
                actor_id=identity.user_id,
            )
        except Exception:  # noqa: BLE001 — авто-рефреш не должен валить callback
            logger.warning(
                "auto inventory/power refresh after astra_update failed server_id=%s",
                server_id, exc_info=True,
            )
    return {
        "ok": True,
        "os_version_id": applied_os_version_id,
        "busy_state": BusyState.FREE.value,
    }


async def record_acs_snapshot_created(
    db: AsyncSession,
    identity: IdentityContext,
    server_id: str,
    payload: AcsSnapshotCreatedCallbackRequest,
    target_department_id: str | None = None,
) -> dict:
    """Зафиксировать исход создания снимка диска через ACS (callback воркера).

    Право: `(server, *, prepare_callback)` — тот же узкий грант, что у
    prepared/astra-updated.

    Снимает `busy_state=acs` в любом исходе — create (Clonezilla save-disk)
    не переписывает диск сервера, он остаётся тем же самым независимо от
    результата снятия снимка. Бронь возвращается к тому, что было до
    ACS-dispatch'а (`pre_acs_busy_snapshot`), а не сбрасывается в free
    безусловно. Аудит `server.acs_snapshot_created`, status = success/failure
    по `succeeded`.
    """
    try:
        await permissions.require_action(
            db, identity, EntityType.SERVER, Action.PREPARE_CALLBACK,
        )
    except AuthorizationError:
        audit_service.emit(
            "server.acs_snapshot_created",
            target_id=server_id, target_type="server",
            status="denied", allowed=False,
            details={"reason": "permission_denied"},
        )
        raise
    server = await server_repo.get_by_id(db, server_id)
    if server is None:
        audit_service.emit(
            "server.acs_snapshot_created",
            target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "server_not_found"},
        )
        raise NotFoundError(
            error_code="SERVER_NOT_FOUND", message="Server not found",
        )
    _check_target_department_for_server(
        audit_action="server.acs_snapshot_created",
        target_id=server_id,
        server_department_id=server.department_id,
        header_department_id=target_department_id,
        actor_department_id=identity.department_id,
    )

    reservation.restore_pre_acs_state(server)
    await db.flush()
    await db.commit()

    audit_service.emit(
        "server.acs_snapshot_created",
        target_id=server_id, target_type="server",
        status="success" if payload.succeeded else "failure",
        allowed=True,
        details={
            "succeeded": payload.succeeded,
            "os_version_id": payload.os_version_id,
            "snapshot_name": payload.snapshot_name,
            "error": payload.error,
            "department_id": server.department_id,
            "caller_type": identity.subject_type,
        },
    )
    return {"ok": True, "busy_state": server.busy_state}


async def record_acs_snapshot_restore_done(
    db: AsyncSession,
    identity: IdentityContext,
    server_id: str,
    payload: AcsSnapshotRestoreDoneCallbackRequest,
    target_department_id: str | None = None,
) -> dict:
    """Зафиксировать исход восстановления снимка диска через ACS (callback воркера).

    Право: `(server, *, prepare_callback)`.

    Ключевое отличие от `record_server_astra_updated`: при `succeeded=True`
    `busy_state=acs` НЕ снимается. Restore (Clonezilla restore-backup)
    переписывает диск сервера целиком — управляющий SSH-ключ DBOS не
    переживает reimage, поэтому вместо снятия блокировки server_service
    резолвит bootstrap-пароль версии и сам диспатчит `server.prepare` тем же
    Redis-механизмом (`prepare_creds_key`/`store_prepare_creds`), что и
    обычный ручной prepare. Снимает блокировку только последующий callback
    `prepared` (см. расширение `record_server_prepared`).

    Пароль резолвится ЗДЕСЬ, а не на dispatch'е restore: dispatch лишь
    проверял факт его существования (`get_bootstrap_password_status`) —
    реальная расшифровка и Redis-стэш идут прямо перед dispatch'ем prepare,
    так авто-prepare не зависит от TTL стэша, пережившего всё окно
    restore+reachability (10-30 минут по опыту).

    При `succeeded=False` — restore не удался, сервер остался на прежнем
    диске: бронь возвращается к тому, что было до ACS-dispatch'а
    (`pre_acs_busy_snapshot`), prepare не диспатчится.
    """
    try:
        await permissions.require_action(
            db, identity, EntityType.SERVER, Action.PREPARE_CALLBACK,
        )
    except AuthorizationError:
        audit_service.emit(
            "server.acs_snapshot_restore_done",
            target_id=server_id, target_type="server",
            status="denied", allowed=False,
            details={"reason": "permission_denied"},
        )
        raise
    server = await server_repo.get_by_id(db, server_id)
    if server is None:
        audit_service.emit(
            "server.acs_snapshot_restore_done",
            target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={"reason": "server_not_found"},
        )
        raise NotFoundError(
            error_code="SERVER_NOT_FOUND", message="Server not found",
        )
    _check_target_department_for_server(
        audit_action="server.acs_snapshot_restore_done",
        target_id=server_id,
        server_department_id=server.department_id,
        header_department_id=target_department_id,
        actor_department_id=identity.department_id,
    )

    os_version = await osv_repo.get_by_id(db, payload.os_version_id)
    os_version_name = os_version.name if os_version is not None else payload.os_version_id

    if not payload.succeeded:
        reservation.restore_pre_acs_state(server)
        await db.flush()
        await db.commit()
        audit_service.emit(
            "server.acs_snapshot_restore_done",
            target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={
                "succeeded": False,
                "os_version_id": payload.os_version_id,
                "snapshot_name": payload.snapshot_name,
                "error": payload.error,
                "department_id": server.department_id,
                "caller_type": identity.subject_type,
            },
        )
        return {"ok": True, "busy_state": server.busy_state, "prepare_task_id": None}

    # succeeded=True — держим busy_state=acs, только освежаем заметку под-этапа;
    # снимает блокировку последующий callback `prepared`, не мы здесь.
    await server_repo.update(db, server, {
        "busy_note": f"ACS_RESTORE_PREPARE_{os_version_name}",
    })
    await db.commit()

    bootstrap = await bootstrap_password_svc.get_bootstrap_password_for_os_version(
        db, payload.os_version_id,
    )
    if bootstrap is None:
        # Пароль стёрли между dispatch'ем restore и этим callback'ом — редкий
        # race (кто-то поменял/стёр пароль версии посреди многочасового
        # restore). Диск уже переписан, откатывать нечего: сервер остаётся в
        # acs с явной пометкой, оператор донастраивает пароль и запускает
        # prepare вручную.
        await server_repo.update(db, server, {
            "busy_note": f"ACS_RESTORE_BOOTSTRAP_MISSING_{os_version_name}",
        })
        await db.commit()
        audit_service.emit(
            "server.acs_snapshot_restore_done",
            target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={
                "succeeded": True,
                "reason": "bootstrap_password_missing_at_callback",
                "os_version_id": payload.os_version_id,
                "snapshot_name": payload.snapshot_name,
                "department_id": server.department_id,
            },
        )
        return {"ok": True, "busy_state": BusyState.ACS.value, "prepare_task_id": None}

    mgmt_cfg = await management_user_config_svc.get_config(db)
    _, mgmt_creds, _ = await management_creds_svc.ensure_management_credentials(db, server)

    bootstrap_creds: dict = {
        "bootstrap_login": bootstrap["ssh_username"],
        "bootstrap_password": bootstrap["password"],
        "mgmt_install": {
            "management_user": mgmt_cfg.login,
            "public_key": mgmt_creds["public_key"],
            "private_key": mgmt_creds["private_key"],
            "password": mgmt_creds["password"],
        },
    }
    creds_key = worker_client.prepare_creds_key(prepare_creds_id())
    try:
        await worker_client.store_prepare_creds(creds_key, bootstrap_creds)
    except ServiceUnavailableError:
        audit_service.emit(
            "server.acs_snapshot_restore_done",
            target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={
                "succeeded": True,
                "reason": "creds_store_unavailable",
                "os_version_id": payload.os_version_id,
                "department_id": server.department_id,
            },
        )
        raise
    except Exception as exc:  # noqa: BLE001 — любой runtime-фейл Redis
        await worker_client.delete_prepare_creds(creds_key)
        audit_service.emit(
            "server.acs_snapshot_restore_done",
            target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={
                "succeeded": True,
                "reason": "creds_store_failed",
                "os_version_id": payload.os_version_id,
                "department_id": server.department_id,
                "error_class": type(exc).__name__,
            },
        )
        raise ServiceUnavailableError(
            error_code="WORKER_REDIS_UNAVAILABLE",
            message="Failed to stash bootstrap credentials for auto-prepare after restore",
        ) from exc

    management_modes = {
        mode.value: cfg.model_dump(mode="json")
        for mode, cfg in mgmt_cfg.modes.items()
    }
    prepare_payload = {
        "server_id": server.id,
        "target_department_id": server.department_id,
        "host": str(server.ip_address),
        "ssh_port": server.ssh_port,
        "is_managed": server.is_managed,
        "management_user": server.management_user,
        "management_login": mgmt_cfg.login,
        "management_modes": management_modes,
        "bootstrap_creds_key": creds_key,
    }
    try:
        prepare_task_id = await worker_client.dispatch_task(
            db=db,
            task_kind="server.prepare",
            target_server_id=server.id,
            payload=prepare_payload,
            created_by=identity.user_id,
            request_id=None,
        )
        await db.commit()
    except (ConflictError, ServiceUnavailableError) as exc:
        await worker_client.delete_prepare_creds(creds_key)
        reason = (
            "idempotent_conflict" if isinstance(exc, ConflictError) else "worker_unreachable"
        )
        audit_service.emit(
            "server.acs_snapshot_restore_done",
            target_id=server_id, target_type="server",
            status="failure", allowed=True,
            details={
                "succeeded": True,
                "reason": reason,
                "os_version_id": payload.os_version_id,
                "department_id": server.department_id,
            },
        )
        raise

    audit_service.emit(
        "server.acs_snapshot_restore_done",
        target_id=server_id, target_type="server",
        status="success", allowed=True,
        details={
            "succeeded": True,
            "os_version_id": payload.os_version_id,
            "snapshot_name": payload.snapshot_name,
            "prepare_task_id": prepare_task_id,
            "department_id": server.department_id,
            "caller_type": identity.subject_type,
        },
    )
    return {
        "ok": True,
        "busy_state": BusyState.ACS.value,
        "prepare_task_id": prepare_task_id,
    }


async def run_auto_inventory_sweep(
    db: AsyncSession,
    identity: IdentityContext,
) -> dict:
    """Плановый авто-inventory прогон по всем managed-серверам (callback воркера).

    Триггерится worker-scheduler'ом (`auto_inventory.sweep`) по cron'у: воркер
    даёт лишь расписание, а сам фан-аут (список managed + dispatch inventory.sync
    + power.status на каждый) идёт здесь, через штатный `worker_client` с outbox'ом.
    Так воркер не дублирует БД server_service — он лишь дёргает internal-эндпоинт.

    Право: `(server, *, prepare_callback)` — тот же глобальный callback-грант
    worker_bot'а, что у `prepared`/`power-state`; прогон платформенный, не привязан
    к отделу, поэтому X-Target-Department-Id здесь не требуется.
    """
    try:
        await permissions.require_action(
            db, identity, EntityType.SERVER, Action.PREPARE_CALLBACK,
        )
    except AuthorizationError:
        audit_service.emit(
            "server.inventory_sync",
            target_type="server",
            status="denied", allowed=False,
            details={"reason": "permission_denied", "source": "auto_scheduled"},
        )
        raise
    # Перед фан-аутом освобождаем серверы, застрявшие в updating дольше TTL
    # (воркер не прислал astra-updated). Иначе они остались бы заблокированы
    # навсегда. Sweep уже ходит по cron'у — это его естественный хук.
    recovery = await server_svc.recover_stuck_updating(db)
    summary = await auto_inventory.fanout_auto_inventory(
        db, actor_id=identity.user_id,
    )
    return {"ok": True, "stuck_updating_recovered": recovery["recovered"], **summary}


async def run_power_sweep(
    db: AsyncSession,
    identity: IdentityContext,
) -> dict:
    """Частый прогон живой пробы питания по всем серверам (callback воркера).

    Триггерится worker-scheduler'ом (`power.sweep`) по частому cron'у: воркер
    даёт лишь расписание, а фан-аут (список всех активных серверов + dispatch
    `power.status` на каждый) идёт здесь. В отличие от auto-inventory-sweep, тут
    нет inventory.sync и нет фильтра `is_managed` — ping/ssh/ipmi снимаются для
    ЛЮБОГО сервера, чтобы доступность и питание были актуальны, а не «прочерк».

    Право: `(server, *, prepare_callback)` — тот же глобальный callback-грант
    worker_bot'а, что у auto-inventory-sweep; прогон платформенный, не привязан
    к отделу, поэтому X-Target-Department-Id здесь не требуется.
    """
    try:
        await permissions.require_action(
            db, identity, EntityType.SERVER, Action.PREPARE_CALLBACK,
        )
    except AuthorizationError:
        audit_service.emit(
            "server.power_status",
            target_type="server",
            status="denied", allowed=False,
            details={"reason": "permission_denied", "source": "auto_power_sweep"},
        )
        raise
    summary = await auto_inventory.fanout_power_sweep(
        db, actor_id=identity.user_id,
    )
    return {"ok": True, **summary}


async def run_vm_status_sweep(
    db: AsyncSession,
    identity: IdentityContext,
) -> dict:
    """Частый прогон статус-пробы по всем ВМ (callback воркера).

    Зеркало `run_power_sweep`, но по ВМ: воркер-scheduler (`vms.status_sweep`)
    даёт лишь расписание, а фан-аут (список всех активных ВМ + dispatch
    `vm.status` на каждую) идёт здесь. `vm.status` снимает три сигнала гостя:
    питание домена (domstate) + ping + ssh. Так питание и доступность держатся
    актуальными между lifecycle-операциями.

    Право: `(server, *, prepare_callback)` — тот же callback-грант worker_bot'а,
    что у power-sweep'а; прогон платформенный, поэтому X-Target-Department-Id
    здесь не требуется.
    """
    try:
        await permissions.require_action(
            db, identity, EntityType.SERVER, Action.PREPARE_CALLBACK,
        )
    except AuthorizationError:
        audit_service.emit(
            "vm.status", target_type="vm",
            status="denied", allowed=False,
            details={"reason": "permission_denied", "source": "auto_vm_status_sweep"},
        )
        raise
    summary = await vm_svc.fanout_vm_status_sweep(
        db, actor_id=identity.user_id,
    )
    return {"ok": True, **summary}


async def list_probe_targets(
    db: AsyncSession,
    identity: IdentityContext,
) -> dict:
    """Отдать плоский список целей пробинга воркер-loop'ам (серверы + ВМ).

    Замена частым sweep'ам `power.sweep`/`vms.status_sweep`: те диспатчили
    `power.status`/`vm.status` на каждую цель и плодили task-row'ы. Теперь воркер
    держит фоновые probe-циклы и просто тянет отсюда, кого пробить, — серверы
    (ping/ssh + ipmi по `server_id`) и ВМ (domstate + ping/ssh гостя через hub).
    Сам фан-аут (enumerate + cap) остаётся на server_service, воркер не дублирует
    его БД.

    Право: `(server, *, prepare_callback)` — тот же глобальный callback-грант
    worker_bot'а, что у sweep-эндпоинтов и `/internal/settings/probes`. Прогон
    платформенный, поэтому `X-Target-Department-Id` здесь не требуется.
    """
    try:
        await permissions.require_action(
            db, identity, EntityType.SERVER, Action.PREPARE_CALLBACK,
        )
    except AuthorizationError:
        audit_service.emit(
            "server.probe_targets_listed", target_type="server",
            status="denied", allowed=False,
            details={"reason": "permission_denied"},
        )
        raise
    servers, servers_truncated = await auto_inventory.enumerate_server_probe_targets(db)
    vms, vms_truncated = await vm_svc.enumerate_vm_probe_targets(db)
    return {
        "servers": servers,
        "vms": vms,
        "servers_truncated": servers_truncated,
        "vms_truncated": vms_truncated,
    }


async def run_vm_create_reconcile(
    db: AsyncSession,
    identity: IdentityContext,
) -> dict:
    """Прогон reconcile'а упавших `vm.create` (callback воркера).

    Триггерится периодиком `vms.reconcile_failed_creates` (worker-scheduler):
    воркер даёт только расписание, а логику (ВМ в `busy_state='creating'` +
    статус их `vm.create`-задач + удаление провалившихся) выполняет
    server_service. Прогон платформенный, `X-Target-Department-Id` не требуется.

    Право: `(server, *, prepare_callback)` — тот же callback-грант worker_bot'а,
    что у sweep'ов. Удаление ВМ идёт под actor'ом = исходным создателем ВМ
    (см. `vm.reconcile_failed_vm_creates`), а не под воркер-ботом.
    """
    try:
        await permissions.require_action(
            db, identity, EntityType.SERVER, Action.PREPARE_CALLBACK,
        )
    except AuthorizationError:
        audit_service.emit(
            "vm.create_failed", target_type="vm",
            status="denied", allowed=False,
            details={"reason": "permission_denied", "source": "vm_create_reconcile"},
        )
        raise
    return await vm_svc.reconcile_failed_vm_creates(
        db, actor_id=identity.user_id,
    )


async def record_ipmi_credentials_rotated(
    db: AsyncSession,
    identity: IdentityContext,
    controller_id: str,
    payload: IpmiCredentialsRotatedRequest,
    target_department_id: str | None = None,
) -> dict:
    """Сохранить результат rotate'а IPMI-credentials, инициированного worker'ом.

    Симметрия с `rotate_account_password`: worker присылает plaintext по TLS
    внутри cluster'а, server_service шифрует через `secrets_service.encrypt()`
    и сохраняет ciphertext в `ipmi_controllers.password_encrypted`. У worker'а
    нет `SERVER_ENCRYPTION_KEY`, поэтому encrypt происходит на приёмной стороне.

    Аудит — WARNING, `ipmi_controller.credentials_rotated_callback`.
    """
    # Dept-check ВЫШЕ permission: cross-dept caller без grant'а иначе ловит 403
    # permission_denied, что enum-oracle'ит факт привязки controller_id к
    # серверу чужого dept. Но если controller_id фактически не существует —
    # эмитим честный `controller_not_found` ДО dept-check'а, иначе SIEM ловит
    # фейковый `target_department_mismatch` на каждом тычке несуществующим id.
    # Клиенту всё равно уходит 404 NO_IPMI_CONTROLLER, oracle не открывается.
    ctrl = await ipmi_repo.get_by_id(db, controller_id)
    if ctrl is None:
        audit_service.emit(
            "ipmi_controller.credentials_rotated_callback",
            target_id=controller_id, target_type="ipmi_controller",
            status="failure", allowed=True,
            details={"reason": "controller_not_found"},
        )
        raise NotFoundError(
            error_code="NO_IPMI_CONTROLLER",
            message="IPMI controller not found",
        )
    server = await server_repo.get_by_id(db, ctrl.server_id)
    if server is None:
        # Orphaned controller: controller-row жив, но server_id ссылается на
        # удалённый сервер. Без явной ветки _check_target_department сравнил бы
        # actor.department_id с None и эмитнул ложный `target_department_mismatch`,
        # будто caller лез в чужой dept — на деле dept-конфликта нет, просто
        # сервер пропал. Пишем честный `orphaned_ipmi_controller` и отдаём 404,
        # пусть оператор подчистит запись.
        audit_service.emit(
            "ipmi_controller.credentials_rotated_callback",
            target_id=controller_id, target_type="ipmi_controller",
            status="failure", allowed=True,
            details={
                "reason": "orphaned_ipmi_controller",
                "server_id": ctrl.server_id,
            },
        )
        raise NotFoundError(
            error_code="NO_IPMI_CONTROLLER",
            message="IPMI controller not found",
        )
    server_dept = server.department_id
    _check_target_department_for_controller(
        audit_action="ipmi_controller.credentials_rotated_callback",
        target_id=controller_id,
        server_department_id=server_dept,
        header_department_id=target_department_id,
        actor_department_id=identity.department_id,
        extra_details={"server_id": ctrl.server_id},
    )
    try:
        await permissions.require_action(
            db, identity, EntityType.IPMI_CONTROLLER, Action.ROTATE_CREDENTIALS,
        )
    except AuthorizationError:
        audit_service.emit(
            "ipmi_controller.credentials_rotated_callback",
            target_id=controller_id, target_type="ipmi_controller",
            status="denied", allowed=False,
            details={"reason": "permission_denied"},
        )
        raise

    rotated_at = payload.rotated_at
    if rotated_at.tzinfo is None:
        rotated_at = rotated_at.replace(tzinfo=timezone.utc)
    verified_at = payload.verified_at
    if verified_at.tzinfo is None:
        verified_at = verified_at.replace(tzinfo=timezone.utc)

    # `rotated_at` ложится в `password_rotated_at`, по которому UI сортирует
    # карточки. Worker с убежавшими часами (NTP-drift) или умышленно подменённый
    # PAT мог бы прислать «ротация на год вперёд» и навсегда вытолкнуть запись
    # в топ списка. Допускаем небольшой перекос — окно настраивается через
    # `Settings.rotated_at_skew_seconds` (env ROTATED_AT_SKEW_SECONDS, default 600s).
    rotated_skew_max = get_settings().rotated_at_skew_seconds
    now = datetime.now(timezone.utc)
    rotated_drift = (rotated_at - now).total_seconds()
    if rotated_drift > rotated_skew_max:
        audit_service.emit(
            "ipmi_controller.credentials_rotated_callback",
            target_id=controller_id, target_type="ipmi_controller",
            status="failure", allowed=True,
            details={
                "reason": "rotated_at_in_future",
                "server_id": ctrl.server_id,
                "rotated_at": rotated_at.isoformat(),
                "rotated_drift_seconds": round(rotated_drift, 3),
                "max_skew_seconds": rotated_skew_max,
            },
        )
        raise BadRequestError(
            error_code="ROTATED_AT_IN_FUTURE",
            message=(
                f"rotated_at must be within {rotated_skew_max}s of now "
                "(NTP-skew tolerated)"
            ),
            details={
                "rotated_at": rotated_at.isoformat(),
                "max_skew_seconds": rotated_skew_max,
            },
        )
    # Симметрично — нижняя граница. Worker с очень отставшими часами либо
    # подменённый PAT мог бы прислать `rotated_at` из глубокого прошлого и
    # утопить новую запись в конце сортировки UI. То же окно, что и сверху.
    if rotated_drift < -rotated_skew_max:
        audit_service.emit(
            "ipmi_controller.credentials_rotated_callback",
            target_id=controller_id, target_type="ipmi_controller",
            status="failure", allowed=True,
            details={
                "reason": "rotated_at_too_old",
                "server_id": ctrl.server_id,
                "rotated_at": rotated_at.isoformat(),
                "rotated_drift_seconds": round(rotated_drift, 3),
                "max_skew_seconds": rotated_skew_max,
            },
        )
        raise BadRequestError(
            error_code="ROTATED_AT_TOO_OLD",
            message=(
                f"rotated_at must be within {rotated_skew_max}s of now "
                "(NTP-skew tolerated)"
            ),
            details={
                "rotated_at": rotated_at.isoformat(),
                "max_skew_seconds": rotated_skew_max,
            },
        )

    # verify-then-storage: пишем ciphertext только если worker подтвердил
    # удачный BMC test-call в окне `IPMI_VERIFY_MAX_AGE_SECONDS`. Старый /
    # отсутствующий verify => 400. Иначе сохранили бы пароль, которым
    # нельзя залогиниться, и out-of-band доступ потерян до ручной починки.
    #
    # Окна асимметричны: stale-сторону держим узкой (это про возраст
    # successful BMC test-call'а, реиспользовать давний proof нельзя),
    # future-сторону можно расширить на стендах с заметным NTP-skew между
    # worker'ом и приёмником через `VERIFY_FUTURE_SKEW_SECONDS`. По
    # умолчанию оба окна равны и работа идёт как раньше.
    settings = get_settings()
    max_age = settings.ipmi_verify_max_age_seconds
    future_skew = settings.verify_future_skew_seconds
    verify_age = (now - verified_at).total_seconds()
    is_future = verify_age < 0
    too_future = is_future and -verify_age > future_skew
    too_old = (not is_future) and verify_age > max_age
    if too_future or too_old:
        reason = "verify_in_future" if is_future else "verify_stale"
        if is_future:
            message = (
                f"BMC verified_at is in the future: must be within "
                f"{future_skew}s of now"
            )
            window = future_skew
        else:
            message = (
                "Stale or missing BMC verify proof: verified_at must be "
                f"within {max_age}s of now"
            )
            window = max_age
        audit_service.emit(
            "ipmi_controller.credentials_rotated_callback",
            target_id=controller_id, target_type="ipmi_controller",
            status="failure", allowed=True,
            details={
                "reason": reason,
                "server_id": ctrl.server_id,
                "verified_at": verified_at.isoformat(),
                "verify_age_seconds": round(verify_age, 3),
                "max_age_seconds": window,
            },
        )
        raise BadRequestError(
            error_code="BMC_VERIFY_REQUIRED",
            message=message,
            details={
                "verified_at": verified_at.isoformat(),
                "max_age_seconds": window,
            },
        )

    # Берём row-lock перед чтением `credentials_pending_apply` — ретрай
    # worker'а после успешного callback'а (или умышленно повторённый POST)
    # без лока успел бы пройти pending-проверку оба раза и второй раз
    # перезаписал бы свежий ciphertext чужим plaintext'ом. Под FOR UPDATE
    # второй callback ждёт коммит первого и видит уже снятый флаг.
    locked = await ipmi_repo.get_for_update(db, controller_id)
    if locked is None:
        audit_service.emit(
            "ipmi_controller.credentials_rotated_callback",
            target_id=controller_id, target_type="ipmi_controller",
            status="failure", allowed=True,
            details={"reason": "controller_not_found"},
        )
        raise NotFoundError(
            error_code="NO_IPMI_CONTROLLER",
            message="IPMI controller not found",
        )
    if not locked.credentials_pending_apply:
        audit_service.emit(
            "ipmi_controller.credentials_rotated_callback",
            target_id=controller_id, target_type="ipmi_controller",
            status="failure", allowed=True,
            details={
                "reason": "credentials_already_applied",
                "server_id": locked.server_id,
            },
        )
        raise ConflictError(
            error_code="CREDENTIALS_ALREADY_APPLIED",
            message="IPMI credentials are not pending apply",
        )

    encrypted = secrets_service.encrypt(
        payload.new_password,
        aad=secrets_service.aad_for_ipmi_credential(locked.id),
    )
    await ipmi_repo.update(db, locked, {
        "password_encrypted": encrypted,
        "password_rotated_at": rotated_at,
        # Callback worker'а — единственная точка, где БД-ciphertext
        # подтверждён применением на BMC. Снимаем pending_apply, чтобы
        # следующий dispatch не уходил в force_replace-режим.
        "credentials_pending_apply": False,
    })
    await db.commit()

    audit_service.emit(
        "ipmi_controller.credentials_rotated_callback",
        target_id=controller_id, target_type="ipmi_controller",
        status="success", allowed=True,
        details={
            "server_id": ctrl.server_id,
            "rotated_at": rotated_at.isoformat(),
            "verified_at": verified_at.isoformat(),
            "department_id": server_dept,
            "caller_type": identity.subject_type,
        },
    )
    return {"ok": True, "rotated_at": rotated_at.isoformat()}


# ── VM-домен: callback'и воркера (worker → server_service) ───────────────────


async def record_vm_state(
    db: AsyncSession,
    identity: IdentityContext,
    vm_id: str,
    payload: VmStateCallbackRequest,
    target_department_id: str | None = None,
) -> dict:
    """Callback воркера: состояние ВМ (power/ip/status/busy_state/error).

    Идемпотентно и частично: применяем только присланные (не None) поля.
    `busy_state=<value>` ставит lifecycle-lock; `clear_busy_state=True` снимает
    его (null в busy_state неотличим от «не прислано», поэтому отдельный флаг).
    `error` пишется в `last_error`.

    Доступ: `(server, *, prepare_callback)` — тот же callback-грант worker_bot'а,
    что и у серверных callback'ов; авторизуем в зоне server. Аудит:
    `vm.state_updated` (INFO — рутинный кэш-апдейт).
    """
    try:
        await permissions.require_action(
            db, identity, EntityType.SERVER, Action.PREPARE_CALLBACK,
        )
    except AuthorizationError:
        audit_service.emit(
            "vm.state_updated", target_id=vm_id, target_type="vm",
            status="denied", allowed=False, details={"reason": "permission_denied"},
        )
        raise
    vm = await vm_repo.get_by_id(db, vm_id)
    if vm is None:
        audit_service.emit(
            "vm.state_updated", target_id=vm_id, target_type="vm",
            status="failure", allowed=True, details={"reason": "vm_not_found"},
        )
        raise NotFoundError(error_code="VM_NOT_FOUND", message="VM not found")
    _check_target_department(
        audit_action="vm.state_updated",
        target_id=vm_id, target_type="vm",
        server_department_id=vm.department_id,
        header_department_id=target_department_id,
        actor_department_id=identity.department_id,
        not_found_error_code="VM_NOT_FOUND",
        not_found_message="VM not found",
    )
    checked_at = datetime.now(timezone.utc)
    if payload.power_state is not None:
        vm.power_state = payload.power_state.value
        vm.power_state_checked_at = checked_at
    if payload.ip_address is not None:
        vm.ip_address = str(payload.ip_address)
    if payload.status is not None:
        vm.status = payload.status
    # busy_state: явный clear имеет приоритет; иначе — новое значение, если прислано.
    if payload.clear_busy_state:
        vm.busy_state = None
        vm.busy_since = None
    elif payload.busy_state is not None:
        vm.busy_state = payload.busy_state.value
        vm.busy_since = checked_at
    if payload.ping_reachable is not None:
        vm.ping_reachable = payload.ping_reachable
        vm.ping_checked_at = checked_at
    if payload.ssh_reachable is not None:
        vm.ssh_reachable = payload.ssh_reachable
        vm.ssh_checked_at = checked_at
    if payload.graphics_port is not None:
        vm.graphics_port = payload.graphics_port
    if payload.error is not None:
        vm.last_error = payload.error
    await db.commit()
    await db.refresh(vm)
    audit_service.emit(
        "vm.state_updated", target_id=vm_id, target_type="vm",
        status="success", allowed=True,
        details={
            "power_state": vm.power_state,
            "busy_state": vm.busy_state,
            "department_id": vm.department_id,
            "has_error": payload.error is not None,
        },
    )
    return {
        "ok": True, "vm_id": vm.id,
        "power_state": vm.power_state, "busy_state": vm.busy_state,
    }


async def record_vms_hub_state(
    db: AsyncSession,
    identity: IdentityContext,
    server_id: str,
    payload: VmsHubStateCallbackRequest,
    target_department_id: str | None = None,
) -> dict:
    """Callback воркера: исход подготовки сервера как VMS-hub (vms_hub.prepare).

    `prepared=True` → `is_vms_hub=True`, `vms_hub_prepared_at=now`,
    `virtualization=True`; `phy_if` (если прислан) пишется в
    `network_interface_name`. `prepared=False` → флаг hub'а не ставится,
    `error` фиксируется в аудите.

    Доступ: `(server, *, prepare_callback)`. Аудит: `vms_hub.prepared`.
    """
    try:
        await permissions.require_action(
            db, identity, EntityType.SERVER, Action.PREPARE_CALLBACK,
        )
    except AuthorizationError:
        audit_service.emit(
            "vms_hub.prepared", target_id=server_id, target_type="server",
            status="denied", allowed=False, details={"reason": "permission_denied"},
        )
        raise
    server = await server_repo.get_by_id(db, server_id)
    if server is None:
        audit_service.emit(
            "vms_hub.prepared", target_id=server_id, target_type="server",
            status="failure", allowed=True, details={"reason": "server_not_found"},
        )
        raise NotFoundError(error_code="SERVER_NOT_FOUND", message="Server not found")
    _check_target_department_for_server(
        audit_action="vms_hub.prepared",
        target_id=server_id,
        server_department_id=server.department_id,
        header_department_id=target_department_id,
        actor_department_id=identity.department_id,
    )
    update: dict = {}
    if payload.prepared:
        update["is_vms_hub"] = True
        update["vms_hub_prepared_at"] = datetime.now(timezone.utc)
        update["virtualization"] = True
        if payload.phy_if is not None:
            update["network_interface_name"] = payload.phy_if
    if update:
        await server_repo.update(db, server, update)
        await db.commit()
    audit_service.emit(
        "vms_hub.prepared", target_id=server_id, target_type="server",
        status="success" if payload.prepared else "failure",
        allowed=True,
        details={
            "prepared": payload.prepared,
            "phy_if": payload.phy_if,
            "department_id": server.department_id,
            "error": payload.error,
        },
    )
    return {"ok": True, "server_id": server.id, "is_vms_hub": server.is_vms_hub}


async def record_vm_disks_state(
    db: AsyncSession,
    identity: IdentityContext,
    vm_id: str,
    payload: VmDisksCallbackRequest,
    target_department_id: str | None = None,
) -> dict:
    """Callback воркера: синк фактов дисков ВМ (state/path/target_dev/serial/size).

    Частичный, идемпотентный апдейт по `disk_id`. Диски, не принадлежащие ВМ или
    уже удалённые, тихо пропускаются (счётчик `synced` считает только реально
    обновлённые строки).

    Доступ: `(server, *, prepare_callback)`. Аудит: `vm.disks_synced` (INFO).
    """
    try:
        await permissions.require_action(
            db, identity, EntityType.SERVER, Action.PREPARE_CALLBACK,
        )
    except AuthorizationError:
        audit_service.emit(
            "vm.disks_synced", target_id=vm_id, target_type="vm",
            status="denied", allowed=False, details={"reason": "permission_denied"},
        )
        raise
    vm = await vm_repo.get_by_id(db, vm_id)
    if vm is None:
        audit_service.emit(
            "vm.disks_synced", target_id=vm_id, target_type="vm",
            status="failure", allowed=True, details={"reason": "vm_not_found"},
        )
        raise NotFoundError(error_code="VM_NOT_FOUND", message="VM not found")
    _check_target_department(
        audit_action="vm.disks_synced",
        target_id=vm_id, target_type="vm",
        server_department_id=vm.department_id,
        header_department_id=target_department_id,
        actor_department_id=identity.department_id,
        not_found_error_code="VM_NOT_FOUND",
        not_found_message="VM not found",
    )
    synced = 0
    for item in payload.disks:
        disk = await vm_disk_repo.get_by_id(db, item.disk_id)
        if disk is None or disk.vm_id != vm.id:
            continue
        if item.state is not None:
            disk.state = item.state
        if item.path is not None:
            disk.path = item.path
        if item.target_dev is not None:
            disk.target_dev = item.target_dev
        if item.serial is not None:
            disk.serial = item.serial
        if item.size_gb is not None:
            disk.size_gb = item.size_gb
        synced += 1
    await db.commit()
    audit_service.emit(
        "vm.disks_synced", target_id=vm_id, target_type="vm",
        status="success", allowed=True,
        details={"synced": synced, "department_id": vm.department_id},
    )
    return {"ok": True, "vm_id": vm.id, "synced": synced}


async def record_vm_packages(
    db: AsyncSession,
    identity: IdentityContext,
    vm_id: str,
    payload: VmPackagesCallbackRequest,
    target_department_id: str | None = None,
) -> dict:
    """Callback воркера: список установленных пакетов гостя ВМ (vm.list_packages).

    Полная перезапись инвентаря (одна строка на ВМ). server_service нормализует
    имя/версию и сохраняет; `GET /vms/{id}/packages` отдаёт последний известный
    список.

    Доступ: `(server, *, prepare_callback)`. Аудит: `vm.packages_synced` (INFO).
    """
    try:
        await permissions.require_action(
            db, identity, EntityType.SERVER, Action.PREPARE_CALLBACK,
        )
    except AuthorizationError:
        audit_service.emit(
            "vm.packages_synced", target_id=vm_id, target_type="vm",
            status="denied", allowed=False, details={"reason": "permission_denied"},
        )
        raise
    vm = await vm_repo.get_by_id(db, vm_id)
    if vm is None:
        audit_service.emit(
            "vm.packages_synced", target_id=vm_id, target_type="vm",
            status="failure", allowed=True, details={"reason": "vm_not_found"},
        )
        raise NotFoundError(error_code="VM_NOT_FOUND", message="VM not found")
    _check_target_department(
        audit_action="vm.packages_synced",
        target_id=vm_id, target_type="vm",
        server_department_id=vm.department_id,
        header_department_id=target_department_id,
        actor_department_id=identity.department_id,
        not_found_error_code="VM_NOT_FOUND",
        not_found_message="VM not found",
    )
    packages = [
        {"name": item.name, "version": item.version} for item in payload.packages
    ]
    inventory = await vm_package_repo.upsert(
        db, vm.id,
        packages=packages,
        source=payload.source,
        task_id=payload.task_id,
    )
    await db.commit()
    audit_service.emit(
        "vm.packages_synced", target_id=vm_id, target_type="vm",
        status="success", allowed=True,
        details={
            "package_count": inventory.package_count,
            "source": payload.source,
            "department_id": vm.department_id,
        },
    )
    return {"ok": True, "vm_id": vm.id, "package_count": inventory.package_count}


async def receive_vm_inventory(
    db: AsyncSession,
    identity: IdentityContext,
    vm_id: str,
    payload: InventoryCallbackRequest,
    target_department_id: str | None = None,
) -> dict:
    """Принять факты гостя ВМ от worker'а после `vm.inventory_sync`.

    VM-аналог `receive_inventory`. Тело — тот же `InventoryCallbackRequest`, что
    и на сервере (воркер собирает факты общим кодом). Отличия ВМ:

    * версия ОС — box-authoritative свободная строка (`vms.os_version`), каталога
      `os_versions` тут нет: пишем присланную версию как есть (box→DB), в ответе
      возвращаем строку и флаг её смены;
    * hostname/kernel гостя — тоже box→DB (пишем то, что реально в госте);
    * vCPU карточки (`vms.cpu`) — конфигурация, а не факт с гостя: расхождение
      guest-видимых ядер с ней НЕ перетираем, поднимаем WARNING
      `vm.inventory_drift_detected` (модель warn-on-drift, как hardware-поля
      сервера). RAM/диски у ВМ так не сверяем — гость видит чуть меньше
      сконфигурированного (kernel reserve), это шум.

    Доступ: `(server, *, inventory_submit)` — тот же worker_bot-грант, что и у
    серверного inventory-приёма. Аудит: `vm.inventory_received` (INFO).
    """
    try:
        await permissions.require_action(
            db, identity, EntityType.SERVER, Action.INVENTORY_SUBMIT,
        )
    except AuthorizationError:
        audit_service.emit(
            "vm.inventory_received", target_id=vm_id, target_type="vm",
            status="denied", allowed=False, details={"reason": "permission_denied"},
        )
        raise
    vm = await vm_repo.get_by_id(db, vm_id)
    if vm is None:
        audit_service.emit(
            "vm.inventory_received", target_id=vm_id, target_type="vm",
            status="failure", allowed=True, details={"reason": "vm_not_found"},
        )
        raise NotFoundError(error_code="VM_NOT_FOUND", message="VM not found")
    _check_target_department(
        audit_action="vm.inventory_received",
        target_id=vm_id, target_type="vm",
        server_department_id=vm.department_id,
        header_department_id=target_department_id,
        actor_department_id=identity.department_id,
        not_found_error_code="VM_NOT_FOUND",
        not_found_message="VM not found",
    )

    # Версия ОС — box-authoritative: перезаписываем свободную строку карточки.
    old_os = vm.os_version
    vm.os_version = payload.os_version
    os_changed = old_os is not None and old_os != payload.os_version

    # Гостевые факты, тоже box→DB: реальный hostname гостя и версия ядра.
    vm.hostname = payload.hostname
    vm.kernel = payload.kernel
    vm.os_last_synced_at = datetime.now(timezone.utc)

    # Warn-on-drift: сконфигурированные vCPU карточки — не факт с гостя.
    # Расхождение guest-видимых ядер с ним НЕ перетираем, только сигналим.
    drift: dict = {}
    if (
        vm.cpu is not None
        and payload.cpu_cores is not None
        and payload.cpu_cores != vm.cpu
    ):
        drift["cpu"] = {"old": vm.cpu, "new": payload.cpu_cores}

    await db.commit()
    await db.refresh(vm)

    if drift:
        audit_service.emit(
            "vm.inventory_drift_detected",
            target_id=vm_id, target_type="vm",
            status="warning", allowed=True,
            details={
                "fields": sorted(drift.keys()),
                "drift": drift,
                "department_id": vm.department_id,
                "caller_type": identity.subject_type,
            },
        )
    audit_service.emit(
        "vm.inventory_received", target_id=vm_id, target_type="vm",
        status="success", allowed=True,
        details={
            "hostname": payload.hostname,
            "kernel": payload.kernel,
            "os_version": payload.os_version,
            "os_changed": os_changed,
            "drift_fields": sorted(drift.keys()),
            "department_id": vm.department_id,
            "caller_type": identity.subject_type,
        },
    )
    return {
        "ok": True,
        "vm_id": vm.id,
        "os_version": vm.os_version,
        "os_changed": os_changed,
        "drift_fields": sorted(drift.keys()),
    }


async def receive_vm_users_inventory(
    db: AsyncSession,
    identity: IdentityContext,
    vm_id: str,
    payload: UsersInventoryCallbackRequest,
    target_department_id: str | None = None,
) -> dict:
    """Принять список OS-пользователей гостя ВМ и reconcile'ить против привязок.

    VM-аналог `receive_users_inventory`. Аккаунты ВМ живут в общем пуле
    `server_accounts` через M2M `server_account_vms`; истина — БД. Reconcile
    фиксирует факт-состояние гостя, но НЕ перетирает поля аккаунта — расхождение
    поднимает WARNING `vm.account_drift_detected`, чтобы оператор разобрался.

    Reconcile (по привязкам инвентаризуемой ВМ):

      * найден в госте, привязка есть → present + `present_on_vm=True`; если
        атрибуты (`has_sudo`/`unix_groups`/`shell`) разошлись с БД — drift, поля
        НЕ трогаем;
      * найден, привязки нет, логин в ignore-list'е отдела (или mgmt-учётка ВМ)
        → пропускаем целиком;
      * найден, привязки нет, но под login в отделе УЖЕ есть аккаунт → уходит в
        `unlinked_existing` (оператор свяжет вручную), не дрейфим;
      * найден, привязки нет, аккаунта под login в отделе тоже нет → в
        `unknown_users` (оператор решает: импорт/игнор), поднимаем drift
        `unknown_login`;
      * привязан, но в госте не найден → drift `missing_on_box` + `present_on_vm=
        False`, привязку НЕ удаляем.

    Доступ: `(server_account, *, inventory_submit)` — тот же worker_bot-грант,
    что и у серверного users-приёма. Аудит: `vm.users_inventory_received` (INFO).
    """
    try:
        await permissions.require_action(
            db, identity, EntityType.SERVER_ACCOUNT, Action.INVENTORY_SUBMIT,
        )
    except AuthorizationError:
        audit_service.emit(
            "vm.users_inventory_received", target_id=vm_id, target_type="vm",
            status="denied", allowed=False, details={"reason": "permission_denied"},
        )
        raise
    vm = await vm_repo.get_by_id(db, vm_id)
    if vm is None:
        audit_service.emit(
            "vm.users_inventory_received", target_id=vm_id, target_type="vm",
            status="failure", allowed=True, details={"reason": "vm_not_found"},
        )
        raise NotFoundError(error_code="VM_NOT_FOUND", message="VM not found")
    _check_target_department(
        audit_action="vm.users_inventory_received",
        target_id=vm_id, target_type="vm",
        server_department_id=vm.department_id,
        header_department_id=target_department_id,
        actor_department_id=identity.department_id,
        not_found_error_code="VM_NOT_FOUND",
        not_found_message="VM not found",
    )

    vm_links = await account_repo.list_vm_links(db, vm.id)
    linked_by_login = {account.login: (link, account) for link, account in vm_links}
    seen_logins = {item.login for item in payload.users}
    ignored_logins = await ignored_login_repo.ignored_logins_for_department(
        db, vm.department_id,
    )
    # Управляющую учётку ВМ (та, под которой ходим по SSH) reconcile не
    # классифицирует — расширяем локальный set, dept-запись не трогаем.
    if vm.mgmt_user:
        ignored_logins = ignored_logins | {vm.mgmt_user}
    inventoried_logins = [item.login for item in payload.users]
    dept_accounts_by_login = await account_repo.list_accounts_in_department_by_logins(
        db, vm.department_id, inventoried_logins,
    )

    created = 0
    present = 0
    drifted = 0
    unknown_users: list[dict] = []
    unlinked_existing: list[dict] = []
    drift_emits: list[dict] = []
    attr_diffs: list[dict] = []

    for item in payload.users:
        if item.login in ignored_logins:
            continue
        linked = linked_by_login.get(item.login)
        if linked is None:
            candidates = dept_accounts_by_login.get(item.login)
            if candidates:
                unlinked_existing.append({
                    "login": item.login,
                    "uid": item.uid,
                    "candidates": [
                        {
                            "account_id": acc.id,
                            "department_id": acc.department_id,
                            "source": acc.source,
                        }
                        for acc in candidates
                    ],
                })
                continue
            unknown_users.append({
                "login": item.login,
                "uid": item.uid,
                "has_sudo": item.has_sudo,
                "unix_groups": list(item.unix_groups),
                "shell": item.shell,
            })
            drifted += 1
            drift_emits.append({"login": item.login, "drift": "unknown_login"})
        else:
            link, account = linked
            diff = _account_attr_drift(account, item)
            link.present_on_vm = True
            present += 1
            if diff:
                drifted += 1
                drift_emits.append({
                    "login": item.login,
                    "drift": "attributes",
                    "fields": sorted(diff.keys()),
                    "diff": diff,
                })
                attr_diffs.append({
                    "account_id": account.id,
                    "login": account.login,
                    "fields": diff,
                })

    # Привязанные, но не найденные в госте — drift. Emit только на переходе
    # present_on_vm True → False, иначе периодический скан плодил бы дубли.
    for link, _account in vm_links:
        if link.login in ignored_logins:
            continue
        if link.login not in seen_logins:
            is_new_drift = link.present_on_vm is True
            link.present_on_vm = False
            if is_new_drift:
                drifted += 1
                drift_emits.append({
                    "login": link.login,
                    "drift": "missing_on_box",
                    "is_new_drift": True,
                })

    await db.commit()

    for emit in drift_emits:
        details = {
            "vm_id": vm_id,
            "login": emit["login"],
            "drift": emit["drift"],
            "department_id": vm.department_id,
        }
        if "fields" in emit:
            details["fields"] = emit["fields"]
        if "diff" in emit:
            details["expected"] = {f: v["expected"] for f, v in emit["diff"].items()}
            details["found"] = {f: v["found"] for f, v in emit["diff"].items()}
        if "is_new_drift" in emit:
            details["is_new_drift"] = emit["is_new_drift"]
        audit_service.emit(
            "vm.account_drift_detected",
            target_id=vm_id, target_type="vm",
            status="warning", allowed=True,
            details=details,
        )

    audit_service.emit(
        "vm.users_inventory_received", target_id=vm_id, target_type="vm",
        status="success", allowed=True,
        details={
            "created": created,
            "present": present,
            "drifted": drifted,
            "found": len(payload.users),
            "unknown": len(unknown_users),
            "unlinked_existing": len(unlinked_existing),
            "department_id": vm.department_id,
            "caller_type": identity.subject_type,
        },
    )

    drift_items = [
        {
            "login": emit["login"],
            "drift_type": emit["drift"],
            "fields": emit.get("fields"),
        }
        for emit in drift_emits
    ]
    return {
        "ok": True,
        "created": created,
        "present": present,
        "drifted": drifted,
        "diffs": attr_diffs,
        "unknown_users": unknown_users,
        "unlinked_existing": unlinked_existing,
        "result_summary": {
            "total_users": len(payload.users),
            "created_discovered": created,
            "drifts": drift_items,
        },
    }


async def record_vm_snapshots(
    db: AsyncSession,
    identity: IdentityContext,
    vm_id: str,
    payload: VmSnapshotsCallbackRequest,
    target_department_id: str | None = None,
) -> dict:
    """Callback воркера: батч-синк снимков ВМ по имени (+ креды-по-снимку).

    Матчинг по имени в пределах ВМ: известный снимок обновляется, незнакомый
    заводится (worker создаёт `<ver>_build`/`<ver>` в ходе vm.create). Креды
    (`mgmt_password`/`mgmt_ssh_private_key`) приходят plaintext'ом — шифруем под
    AAD снимка (per_snapshot). `is_current=True` делает снимок текущим и снимает
    флаг с остальных снимков ВМ (revert).

    Доступ: `(server, *, prepare_callback)`. Аудит: `vm.snapshots_synced` (INFO).
    """
    try:
        await permissions.require_action(
            db, identity, EntityType.SERVER, Action.PREPARE_CALLBACK,
        )
    except AuthorizationError:
        audit_service.emit(
            "vm.snapshots_synced", target_id=vm_id, target_type="vm",
            status="denied", allowed=False, details={"reason": "permission_denied"},
        )
        raise
    vm = await vm_repo.get_by_id(db, vm_id)
    if vm is None:
        audit_service.emit(
            "vm.snapshots_synced", target_id=vm_id, target_type="vm",
            status="failure", allowed=True, details={"reason": "vm_not_found"},
        )
        raise NotFoundError(error_code="VM_NOT_FOUND", message="VM not found")
    _check_target_department(
        audit_action="vm.snapshots_synced",
        target_id=vm_id, target_type="vm",
        server_department_id=vm.department_id,
        header_department_id=target_department_id,
        actor_department_id=identity.department_id,
        not_found_error_code="VM_NOT_FOUND",
        not_found_message="VM not found",
    )
    existing = {s.name: s for s in await vm_snapshot_repo.list_all_for_vm(db, vm.id)}
    created = 0
    updated = 0
    current_name: str | None = None
    for item in payload.snapshots:
        snap = existing.get(item.name)
        if snap is None:
            snap = await vm_snapshot_repo.create(db, {
                "id": vm_snapshot_id(),
                "vm_id": vm.id,
                "name": item.name,
                "snapshot_type": item.snapshot_type or "disk_only",
                "kind": item.kind or "user",
                "os_version": item.os_version,
                "mode": item.mode,
                "is_system": bool(item.is_system) if item.is_system is not None else item.name.endswith("_build"),
                "state": item.state or "ready",
                "is_current": False,
            })
            existing[item.name] = snap
            created += 1
        else:
            if item.snapshot_type is not None:
                snap.snapshot_type = item.snapshot_type
            if item.kind is not None:
                snap.kind = item.kind
            if item.os_version is not None:
                snap.os_version = item.os_version
            if item.mode is not None:
                snap.mode = item.mode
            if item.is_system is not None:
                snap.is_system = item.is_system
            if item.state is not None:
                snap.state = item.state
            updated += 1
        if item.size_bytes is not None:
            snap.size_bytes = item.size_bytes
        if item.parent is not None:
            parent = existing.get(item.parent)
            snap.parent_snapshot_id = parent.id if parent is not None else None
        if item.mgmt_user is not None:
            snap.mgmt_user = item.mgmt_user
        if item.mgmt_password is not None:
            snap.mgmt_password_encrypted = secrets_service.encrypt(
                item.mgmt_password,
                aad=secrets_service.aad_for_vm_snapshot_password(snap.id),
            )
        if item.mgmt_ssh_private_key is not None:
            snap.mgmt_ssh_private_key_encrypted = secrets_service.encrypt(
                item.mgmt_ssh_private_key,
                aad=secrets_service.aad_for_vm_snapshot_ssh_key(snap.id),
            )
        if item.is_current:
            current_name = item.name
    # Ровно один текущий снимок: переносим флаг на присланный, снимаем с остальных.
    if current_name is not None:
        for name, snap in existing.items():
            snap.is_current = (name == current_name)
    await db.commit()
    audit_service.emit(
        "vm.snapshots_synced", target_id=vm_id, target_type="vm",
        status="success", allowed=True,
        details={
            "synced": created + updated, "created": created, "updated": updated,
            "current": current_name, "department_id": vm.department_id,
        },
    )
    return {
        "ok": True, "vm_id": vm.id,
        "synced": created + updated, "created": created, "updated": updated,
    }


async def fetch_vm_management_credentials(
    db: AsyncSession,
    identity: IdentityContext,
    vm_id: str,
    target_department_id: str | None = None,
) -> dict:
    """Расшифровать и вернуть per-VM управляющие креды воркеру.

    Зеркало `fetch_management_credentials` серверов: воркер just-in-time тянет
    приватный ключ + пароль управляющего пользователя ВМ перед managed-операцией
    по SSH в гость. Авторизация — тем же `(server, view_management_credentials)`,
    что и у серверного fetch'а (VM-домен переиспользует серверные worker_bot-
    гранты). Аудит: `vm.mgmt_credentials_revealed` (WARNING — штатный pull).
    """
    try:
        await permissions.require_action(
            db, identity, EntityType.SERVER, Action.VIEW_MANAGEMENT_CREDENTIALS,
        )
    except AuthorizationError:
        audit_service.emit(
            "vm.mgmt_credentials_revealed", target_id=vm_id, target_type="vm",
            status="denied", allowed=False,
            details={"reason": "permission_denied", "caller_type": identity.subject_type},
        )
        raise
    vm = await vm_repo.get_by_id(db, vm_id)
    if vm is None:
        audit_service.emit(
            "vm.mgmt_credentials_revealed", target_id=vm_id, target_type="vm",
            status="failure", allowed=True, details={"reason": "vm_not_found"},
        )
        raise NotFoundError(error_code="VM_NOT_FOUND", message="VM not found")
    _check_target_department(
        audit_action="vm.mgmt_credentials_revealed",
        target_id=vm_id, target_type="vm",
        server_department_id=vm.department_id,
        header_department_id=target_department_id,
        actor_department_id=identity.department_id,
        not_found_error_code="VM_NOT_FOUND",
        not_found_message="VM not found",
    )
    if vm.mgmt_ssh_private_key_encrypted is None or vm.mgmt_password_encrypted is None:
        audit_service.emit(
            "vm.mgmt_credentials_revealed", target_id=vm_id, target_type="vm",
            status="failure", allowed=True,
            details={"reason": "no_creds_stored", "department_id": vm.department_id},
        )
        raise NotFoundError(
            error_code="VM_MANAGEMENT_CREDS_NOT_FOUND",
            message="VM has no stored management credentials (not prepared yet)",
        )
    try:
        private_pem = secrets_service.decrypt(
            vm.mgmt_ssh_private_key_encrypted,
            aad=secrets_service.aad_for_vm_mgmt_ssh_key(vm.id),
        )
        password = secrets_service.decrypt(
            vm.mgmt_password_encrypted,
            aad=secrets_service.aad_for_vm_mgmt_password(vm.id),
        )
    except AppException:
        metrics.increment_secrets_decrypt_failures()
        audit_service.emit(
            "vm.mgmt_credentials_revealed", target_id=vm_id, target_type="vm",
            status="failure", allowed=True,
            details={"reason": "decrypt_failed", "department_id": vm.department_id},
        )
        raise
    audit_service.emit(
        "vm.mgmt_credentials_revealed", target_id=vm_id, target_type="vm",
        status="success", allowed=True,
        details={
            "department_id": vm.department_id,
            "management_user": vm.mgmt_user,
            "caller_type": identity.subject_type,
        },
    )
    return {
        "management_user": vm.mgmt_user,
        "ssh_private_key": private_pem,
        "password": password,
    }


async def record_vm_prepared(
    db: AsyncSession,
    identity: IdentityContext,
    vm_id: str,
    payload: VmPreparedCallbackRequest,
    target_department_id: str | None = None,
) -> dict:
    """Callback воркера: per-VM управляющие креды установлены в госте (vm.prepare/ротация).

    `prepared=True` → `is_managed=True`, `mgmt_creds_pending_apply=False`,
    `mgmt_creds_rotated_at=now`, снят lifecycle-lock. `management_user` (если
    прислан) пишется в модель. Опциональные plaintext-креды в payload'е
    перезаписывают ciphertext под AAD ВМ (worker сообщает реально установленный
    материал). `prepared=False` → флаги не трогаем, ошибку фиксируем в last_error.

    Доступ: `(server, prepare_callback)`. Аудит: `vm.prepared` (CRITICAL).
    """
    try:
        await permissions.require_action(
            db, identity, EntityType.SERVER, Action.PREPARE_CALLBACK,
        )
    except AuthorizationError:
        audit_service.emit(
            "vm.prepared", target_id=vm_id, target_type="vm",
            status="denied", allowed=False, details={"reason": "permission_denied"},
        )
        raise
    vm = await vm_repo.get_by_id(db, vm_id)
    if vm is None:
        audit_service.emit(
            "vm.prepared", target_id=vm_id, target_type="vm",
            status="failure", allowed=True, details={"reason": "vm_not_found"},
        )
        raise NotFoundError(error_code="VM_NOT_FOUND", message="VM not found")
    _check_target_department(
        audit_action="vm.prepared",
        target_id=vm_id, target_type="vm",
        server_department_id=vm.department_id,
        header_department_id=target_department_id,
        actor_department_id=identity.department_id,
        not_found_error_code="VM_NOT_FOUND",
        not_found_message="VM not found",
    )
    if not payload.prepared:
        vm.busy_state = None
        vm.busy_since = None
        vm.mgmt_creds_pending_apply = False
        if payload.error is not None:
            vm.last_error = payload.error
        await db.commit()
        audit_service.emit(
            "vm.prepared", target_id=vm_id, target_type="vm",
            status="failure", allowed=True,
            details={"reason": "worker_reported_failure", "error": payload.error,
                     "department_id": vm.department_id},
        )
        return {"ok": True, "vm_id": vm.id, "is_managed": vm.is_managed}

    if payload.management_user is not None:
        vm.mgmt_user = payload.management_user
    if payload.mgmt_ssh_public_key is not None:
        vm.mgmt_ssh_public_key = payload.mgmt_ssh_public_key
    if payload.mgmt_password is not None:
        vm.mgmt_password_encrypted = secrets_service.encrypt(
            payload.mgmt_password, aad=secrets_service.aad_for_vm_mgmt_password(vm.id),
        )
    if payload.mgmt_ssh_private_key is not None:
        vm.mgmt_ssh_private_key_encrypted = secrets_service.encrypt(
            payload.mgmt_ssh_private_key,
            aad=secrets_service.aad_for_vm_mgmt_ssh_key(vm.id),
        )
    vm.is_managed = True
    vm.mgmt_creds_pending_apply = False
    vm.mgmt_creds_rotated_at = datetime.now(timezone.utc)
    vm.busy_state = None
    vm.busy_since = None
    await db.commit()
    await db.refresh(vm)
    audit_service.emit(
        "vm.prepared", target_id=vm_id, target_type="vm",
        status="success", allowed=True,
        details={
            "management_user": vm.mgmt_user,
            "department_id": vm.department_id,
            "caller_type": identity.subject_type,
        },
    )
    return {"ok": True, "vm_id": vm.id, "is_managed": vm.is_managed}


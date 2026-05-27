"""HTTP-клиент к internal-эндпоинтам server_service.

Эти эндпоинты расшифровывают и отдают credentials — worker подключается к
серверам/IPMI без необходимости самому держать `SERVER_ENCRYPTION_KEY`.

Каждый вызов опционально форвардит ``X-Target-Department-Id`` — server_service
сверяет этот хинт с фактическим `department_id` сервера, чтобы кросс-tenant
использование worker-PAT было видно (и refusable в strict-mode). Caller'ы
пробрасывают значение из task `payload` (server_service сам кладёт туда
``target_department_id`` при dispatch'е).
"""

import logging

import httpx

from src.core.config import get_settings
from src.core.exceptions import CredentialFetchError

logger = logging.getLogger(__name__)

_warned_empty_token = False


def _headers(target_department_id: str | None = None) -> dict[str, str]:
    """Собрать HTTP-заголовки для запроса к server_service.

    Включает `Authorization: Bearer <worker_bot_token>` если PAT
    выставлен, плюс `X-Target-Department-Id` если caller передал dept
    (для cross-tenant cross-check'а в internal-эндпоинтах).

    При пустом `worker_bot_token` один раз пишет WARNING в лог: без PAT
    server_service отдаст 401 на любой internal-вызов, и worker увидит
    только `IPMI_CREDENTIALS_UNAVAILABLE`/`ACCOUNT_PASSWORD_UNAVAILABLE`
    без подсказки про причину. Повторные вызовы не warn'ят — иначе при
    burst'е тасков лог зальёт одной и той же строкой.
    """
    global _warned_empty_token
    settings = get_settings()
    headers: dict[str, str] = {}
    if settings.worker_bot_token:
        headers["Authorization"] = f"Bearer {settings.worker_bot_token}"
    elif not _warned_empty_token:
        logger.warning(
            "worker_bot_token is empty — internal calls will fail with 401",
        )
        _warned_empty_token = True
    if target_department_id is not None:
        headers["X-Target-Department-Id"] = target_department_id
    return headers


async def fetch_ipmi_credentials(
    server_id: str,
    target_department_id: str | None = None,
) -> dict:
    """Запросить расшифрованные IPMI-креды для сервера.

    Возвращает: `{kind, endpoint_url, username, password}` от
    `GET /api/server/v1/internal/servers/{id}/ipmi/credentials`.

    Возможные ошибки: `CredentialFetchError` с `error_code`:
      * `SERVER_SERVICE_UNREACHABLE` — transport (timeout/connect).
      * `IPMI_CREDENTIALS_UNAVAILABLE` — server_service вернул не 200
        (нет IPMI у сервера, отказ авторизации, dept-mismatch, ...).
    """
    settings = get_settings()
    url = f"{settings.server_service_url.rstrip('/')}/api/server/v1/internal/servers/{server_id}/ipmi/credentials"
    try:
        async with httpx.AsyncClient(timeout=settings.http_request_timeout_seconds) as client:
            response = await client.get(url, headers=_headers(target_department_id))
    except httpx.HTTPError as exc:
        raise CredentialFetchError(
            error_code="SERVER_SERVICE_UNREACHABLE",
            message=f"Failed to call server_service: {type(exc).__name__}",
        ) from exc
    if response.status_code != 200:
        raise CredentialFetchError(
            error_code="IPMI_CREDENTIALS_UNAVAILABLE",
            message=f"server_service returned {response.status_code}",
            details={"server_id": server_id, "status_code": response.status_code},
        )
    return response.json()


async def fetch_account_password(
    server_id: str,
    account_id: str,
    target_department_id: str | None = None,
) -> dict:
    """Запросить расшифрованный пароль аккаунта сервера.

    Возвращает: `{login, password}` от
    `GET /api/server/v1/internal/servers/{id}/accounts/{aid}/password`.

    Возможные ошибки: `CredentialFetchError` с `error_code`:
      * `SERVER_SERVICE_UNREACHABLE` — transport.
      * `ACCOUNT_PASSWORD_UNAVAILABLE` — server_service вернул не 200.
    """
    settings = get_settings()
    url = (
        f"{settings.server_service_url.rstrip('/')}"
        f"/api/server/v1/internal/servers/{server_id}/accounts/{account_id}/password"
    )
    try:
        async with httpx.AsyncClient(timeout=settings.http_request_timeout_seconds) as client:
            response = await client.get(url, headers=_headers(target_department_id))
    except httpx.HTTPError as exc:
        raise CredentialFetchError(
            error_code="SERVER_SERVICE_UNREACHABLE",
            message=f"Failed to call server_service: {type(exc).__name__}",
        ) from exc
    if response.status_code != 200:
        raise CredentialFetchError(
            error_code="ACCOUNT_PASSWORD_UNAVAILABLE",
            message=f"server_service returned {response.status_code}",
            details={"server_id": server_id, "account_id": account_id, "status_code": response.status_code},
        )
    return response.json()


async def submit_rotated_password(
    server_id: str,
    account_id: str,
    new_password: str,
    target_department_id: str | None = None,
) -> dict:
    """Отдать только что сгенерированный пароль обратно в server_service.

    Тот шифрует и сохраняет ciphertext в БД. Используется в финале
    `account.rotate_password` task'а — без round-trip'а ротация была бы
    «локальная»: пароль на сервере сменился, а сохранить новый никто не
    смог.

    Возвращает: `{rotated_at}` от
    `POST /api/server/v1/internal/servers/{id}/accounts/{aid}/password/rotate`.

    Возможные ошибки: `CredentialFetchError` с `error_code`:
      * `SERVER_SERVICE_UNREACHABLE` — transport.
      * `PASSWORD_ROTATE_REJECTED` — server_service вернул не 200
        (валидация policy, dept-mismatch, отказ хранилища).
    """
    settings = get_settings()
    url = (
        f"{settings.server_service_url.rstrip('/')}"
        f"/api/server/v1/internal/servers/{server_id}/accounts/{account_id}/password/rotate"
    )
    try:
        async with httpx.AsyncClient(timeout=settings.http_request_timeout_seconds) as client:
            response = await client.post(
                url,
                headers=_headers(target_department_id),
                json={"password": new_password},
            )
    except httpx.HTTPError as exc:
        raise CredentialFetchError(
            error_code="SERVER_SERVICE_UNREACHABLE",
            message=f"Failed to call server_service: {type(exc).__name__}",
        ) from exc
    if response.status_code != 200:
        raise CredentialFetchError(
            error_code="PASSWORD_ROTATE_REJECTED",
            message=f"server_service returned {response.status_code}",
            details={"server_id": server_id, "account_id": account_id, "status_code": response.status_code},
        )
    return response.json()


async def submit_inventory_facts(
    server_id: str,
    inventory_payload: dict,
    target_department_id: str | None = None,
) -> dict:
    """Отдать собранный inventory обратно в server_service.

    Используется в финале `inventory.sync` task'а — без round-trip'а
    facts остались бы только в `task.result` worker-DB, и UI / другие
    сервисы не увидели бы свежий cpu_model / os_version / disks для
    сервера.

    `inventory_payload` — flat dict под `InventoryCallbackRequest`
    server_service'а: `{hostname, kernel, cpu_model, cpu_cores,
    os_version, disks: [...], lspci?}`. Маппинг из сырых SSH-facts
    делает caller (`tasks/inventory.py` через helper в
    `services/ssh_client.py`).

    Возвращает: `{ok, cpu_id, os_version_id, disks_upserted}` от
    `POST /api/server/v1/internal/servers/{id}/inventory`.

    Возможные ошибки: `CredentialFetchError` (имя класса историческое,
    покрывает все ошибки на этом канале) с `error_code`:
      * `SERVER_SERVICE_UNREACHABLE` — transport (timeout/connect).
      * `INVENTORY_SUBMIT_REJECTED` — server_service вернул не 2xx
        (валидация полей, dept-mismatch, отказ хранилища).
    """
    settings = get_settings()
    url = (
        f"{settings.server_service_url.rstrip('/')}"
        f"/api/server/v1/internal/servers/{server_id}/inventory"
    )
    try:
        async with httpx.AsyncClient(timeout=settings.http_request_timeout_seconds) as client:
            response = await client.post(
                url,
                headers=_headers(target_department_id),
                json=inventory_payload,
            )
    except httpx.HTTPError as exc:
        raise CredentialFetchError(
            error_code="SERVER_SERVICE_UNREACHABLE",
            message=f"Failed to call server_service: {type(exc).__name__}",
        ) from exc
    if response.status_code >= 300:
        raise CredentialFetchError(
            error_code="INVENTORY_SUBMIT_REJECTED",
            message=f"server_service returned {response.status_code}",
            details={"server_id": server_id, "status_code": response.status_code},
        )
    try:
        return response.json()
    except ValueError:
        # 2xx без body — допустимо для callback-endpoint'ов.
        return {}


async def submit_users_inventory(
    server_id: str,
    users_payload: dict,
    target_department_id: str | None = None,
) -> dict:
    """Отдать список найденных OS-пользователей обратно в server_service.

    Финал `users.inventory` task'а. server_service reconcile'ит список
    против привязанных к серверу `server_accounts`: создаёт discovered,
    обновляет существующие, помечает drift.

    `users_payload` — `{"users": [{login, uid, shell, home_dir,
    unix_groups, has_sudo}, ...]}` под `UsersInventoryCallbackRequest`.

    Возвращает: `{ok, created, updated, drifted}` от
    `POST /api/server/v1/internal/servers/{id}/users/inventory`.

    Возможные ошибки: `CredentialFetchError` с `error_code`:
      * `SERVER_SERVICE_UNREACHABLE` — transport (timeout/connect).
      * `USERS_INVENTORY_SUBMIT_REJECTED` — server_service вернул не 2xx.
    """
    settings = get_settings()
    url = (
        f"{settings.server_service_url.rstrip('/')}"
        f"/api/server/v1/internal/servers/{server_id}/users/inventory"
    )
    try:
        async with httpx.AsyncClient(timeout=settings.http_request_timeout_seconds) as client:
            response = await client.post(
                url,
                headers=_headers(target_department_id),
                json=users_payload,
            )
    except httpx.HTTPError as exc:
        raise CredentialFetchError(
            error_code="SERVER_SERVICE_UNREACHABLE",
            message=f"Failed to call server_service: {type(exc).__name__}",
        ) from exc
    if response.status_code >= 300:
        raise CredentialFetchError(
            error_code="USERS_INVENTORY_SUBMIT_REJECTED",
            message=f"server_service returned {response.status_code}",
            details={"server_id": server_id, "status_code": response.status_code},
        )
    try:
        return response.json()
    except ValueError:
        return {}


async def fetch_secrets_migration_status() -> dict:
    """Запросить сводку по постепенной ротации мастер-ключа.

    Возвращает: `{remaining, total, active_version, by_version}` от
    `GET /api/server/v1/internal/secrets/migration_status`.

    Возможные ошибки: `CredentialFetchError` с `error_code`:
      * `SERVER_SERVICE_UNREACHABLE` — transport.
      * `SECRETS_MIGRATION_STATUS_UNAVAILABLE` — server_service вернул не 200.
    """
    settings = get_settings()
    url = (
        f"{settings.server_service_url.rstrip('/')}"
        f"/api/server/v1/internal/secrets/migration_status"
    )
    try:
        async with httpx.AsyncClient(timeout=settings.http_request_timeout_seconds) as client:
            response = await client.get(url, headers=_headers())
    except httpx.HTTPError as exc:
        raise CredentialFetchError(
            error_code="SERVER_SERVICE_UNREACHABLE",
            message=f"Failed to call server_service: {type(exc).__name__}",
        ) from exc
    if response.status_code != 200:
        raise CredentialFetchError(
            error_code="SECRETS_MIGRATION_STATUS_UNAVAILABLE",
            message=f"server_service returned {response.status_code}",
            details={"status_code": response.status_code},
        )
    return response.json()


async def trigger_secrets_reencrypt_batch(limit: int) -> dict:
    """Дёрнуть один батч ре-шифрации `limit` записей.

    Возвращает: `{processed, errors}` от
    `POST /api/server/v1/internal/secrets/reencrypt_batch?limit=N`.

    Возможные ошибки: `CredentialFetchError` с `error_code`:
      * `SERVER_SERVICE_UNREACHABLE` — transport.
      * `SECRETS_REENCRYPT_REJECTED` — server_service вернул не 200.
    """
    settings = get_settings()
    url = (
        f"{settings.server_service_url.rstrip('/')}"
        f"/api/server/v1/internal/secrets/reencrypt_batch"
    )
    try:
        async with httpx.AsyncClient(timeout=settings.http_request_timeout_seconds) as client:
            response = await client.post(
                url,
                params={"limit": limit},
                headers=_headers(),
            )
    except httpx.HTTPError as exc:
        raise CredentialFetchError(
            error_code="SERVER_SERVICE_UNREACHABLE",
            message=f"Failed to call server_service: {type(exc).__name__}",
        ) from exc
    if response.status_code != 200:
        raise CredentialFetchError(
            error_code="SECRETS_REENCRYPT_REJECTED",
            message=f"server_service returned {response.status_code}",
            details={"limit": limit, "status_code": response.status_code},
        )
    return response.json()


async def submit_rotated_ipmi_password(
    ipmi_controller_id: str,
    new_password: str,
    rotated_at: str,
    target_department_id: str | None = None,
) -> dict:
    """Отдать сгенерированный IPMI-пароль обратно в server_service.

    Симметрия `submit_rotated_password` — но для IPMI-controller'а
    (отдельная таблица `ipmi_controllers`, не `server_accounts`).
    server_service шифрует через `secrets_service.encrypt()` (AES-256-GCM
    + master-key) и сохраняет ciphertext в `ipmi_controllers.password_encrypted`.

    Контракт: worker отдаёт plaintext по TLS внутри cluster'а; encrypt'ит
    приёмная сторона — у worker'а нет `SERVER_ENCRYPTION_KEY`. Этот
    round-trip обязателен ДО `RedfishClient.rotate_user_password` —
    иначе если процесс умрёт между «BMC сменил пароль» и «storage
    сохранил», out-of-band доступ потерян навсегда.

    `ipmi_controller_id` — id записи в `ipmi_controllers`, не `server_id`
    (resource у endpoint'а — controller). Worker берёт его из
    `fetch_ipmi_credentials` response.

    `rotated_at` — ISO-8601 UTC timestamp момента генерации пароля
    (worker фиксирует ДО storage round-trip'а).

    Возвращает: `{ok, rotated_at}` от
    `POST /api/server/v1/internal/ipmi-controllers/{controller_id}/credentials_rotated`.

    Возможные ошибки: `CredentialFetchError` с `error_code`:
      * `SERVER_SERVICE_UNREACHABLE` — transport.
      * `IPMI_ROTATE_REJECTED` — server_service вернул не 200
        (валидация policy, dept-mismatch, отказ хранилища).
    """
    settings = get_settings()
    url = (
        f"{settings.server_service_url.rstrip('/')}"
        f"/api/server/v1/internal/ipmi-controllers/{ipmi_controller_id}/credentials_rotated"
    )
    try:
        async with httpx.AsyncClient(timeout=settings.http_request_timeout_seconds) as client:
            response = await client.post(
                url,
                headers=_headers(target_department_id),
                json={"new_password": new_password, "rotated_at": rotated_at},
            )
    except httpx.HTTPError as exc:
        raise CredentialFetchError(
            error_code="SERVER_SERVICE_UNREACHABLE",
            message=f"Failed to call server_service: {type(exc).__name__}",
        ) from exc
    if response.status_code != 200:
        raise CredentialFetchError(
            error_code="IPMI_ROTATE_REJECTED",
            message=f"server_service returned {response.status_code}",
            details={
                "ipmi_controller_id": ipmi_controller_id,
                "status_code": response.status_code,
            },
        )
    return response.json()



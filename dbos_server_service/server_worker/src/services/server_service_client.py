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
from src.services.http_pool import get_server_service_client

logger = logging.getLogger(__name__)


def _headers(target_department_id: str | None = None) -> dict[str, str]:
    """Собрать HTTP-заголовки для запроса к server_service.

    Включает `Authorization: Bearer <worker_bot_token>` (наличие токена
    гарантируется startup-валидатором в `src.core.config.Settings`: в
    dev/staging/production пустой `WORKER_BOT_TOKEN` валит старт воркера,
    в local/test conftest подставляет placeholder), плюс
    `X-Target-Department-Id` если caller передал dept (для cross-tenant
    cross-check'а в internal-эндпоинтах).
    """
    settings = get_settings()
    headers: dict[str, str] = {
        "Authorization": f"Bearer {settings.worker_bot_token}",
    }
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
    client = get_server_service_client()
    try:
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
    client = get_server_service_client()
    try:
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
    client = get_server_service_client()
    try:
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
    client = get_server_service_client()
    try:
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
    client = get_server_service_client()
    try:
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


async def submit_provision_status(
    server_id: str,
    account_id: str,
    operation: str,
    present: bool,
    target_department_id: str | None = None,
) -> dict:
    """Сообщить server_service результат useradd/usermod/userdel на боксе.

    Финал `account.provision` / `account.update_on_host` / `account.deprovision`
    task'ов. server_service обновляет `present_on_server` на связке аккаунт ↔
    сервер: provision/update → True, deprovision → False.

    `operation` — `provision` / `update` / `deprovision`. `present` — целевое
    состояние присутствия (передаём явно, не выводим на приёмной стороне).

    Возвращает: `{ok, present_on_server}` от
    `POST /api/server/v1/internal/servers/{id}/accounts/{aid}/provision_status`.

    Возможные ошибки: `CredentialFetchError` с `error_code`:
      * `SERVER_SERVICE_UNREACHABLE` — transport (timeout/connect).
      * `PROVISION_STATUS_REJECTED` — server_service вернул не 2xx.
    """
    settings = get_settings()
    url = (
        f"{settings.server_service_url.rstrip('/')}"
        f"/api/server/v1/internal/servers/{server_id}/accounts/{account_id}/provision_status"
    )
    client = get_server_service_client()
    try:
        response = await client.post(
            url,
            headers=_headers(target_department_id),
            json={"operation": operation, "present": present},
        )
    except httpx.HTTPError as exc:
        raise CredentialFetchError(
            error_code="SERVER_SERVICE_UNREACHABLE",
            message=f"Failed to call server_service: {type(exc).__name__}",
        ) from exc
    if response.status_code >= 300:
        raise CredentialFetchError(
            error_code="PROVISION_STATUS_REJECTED",
            message=f"server_service returned {response.status_code}",
            details={
                "server_id": server_id,
                "account_id": account_id,
                "status_code": response.status_code,
            },
        )
    try:
        return response.json()
    except ValueError:
        return {}


async def submit_prepared(
    server_id: str,
    management_user: str,
    target_department_id: str | None = None,
) -> dict:
    """Сообщить server_service, что бутстрап управления сервера завершён.

    Финал `server.prepare` task'а: управляющий пользователь заведён и
    публичный ключ положен. server_service помечает сервер подготовленным
    (`is_managed=True`, `prepared_at`, management_user).

    Возвращает: `{ok, is_managed, prepared_at}` от
    `POST /api/server/v1/internal/servers/{id}/prepared`.

    Возможные ошибки: `CredentialFetchError` с `error_code`:
      * `SERVER_SERVICE_UNREACHABLE` — transport (timeout/connect).
      * `PREPARE_STATUS_REJECTED` — server_service вернул не 2xx.
    """
    settings = get_settings()
    url = (
        f"{settings.server_service_url.rstrip('/')}"
        f"/api/server/v1/internal/servers/{server_id}/prepared"
    )
    client = get_server_service_client()
    try:
        response = await client.post(
            url,
            headers=_headers(target_department_id),
            json={"management_user": management_user},
        )
    except httpx.HTTPError as exc:
        raise CredentialFetchError(
            error_code="SERVER_SERVICE_UNREACHABLE",
            message=f"Failed to call server_service: {type(exc).__name__}",
        ) from exc
    if response.status_code >= 300:
        raise CredentialFetchError(
            error_code="PREPARE_STATUS_REJECTED",
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
    client = get_server_service_client()
    try:
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
    """Legacy sync-путь: дёрнуть один батч ре-шифрации `limit` записей.

    Сохранён для совместимости с тестами и операторских ad-hoc вызовов.
    Новый периодик идёт через outbox (`seed_reencrypt_outbox` →
    `claim_reencrypt_outbox_pending` → `finalize_reencrypt_outbox_done`).

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
    client = get_server_service_client()
    try:
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


async def seed_reencrypt_outbox(limit: int = 500) -> dict:
    """Попросить server_service просканировать owner-таблицы и публиковать
    pending outbox-row'ы.

    Возвращает: `{inserted, scanned, active_version}` от
    `POST /api/server/v1/internal/secrets/reencrypt_outbox/seed?limit=N`.
    `inserted=0` — больше публиковать нечего; caller прекращает повторные
    вызовы в текущем тике.

    Возможные ошибки: `CredentialFetchError` с `error_code`:
      * `SERVER_SERVICE_UNREACHABLE` — transport.
      * `SECRETS_OUTBOX_SEED_REJECTED` — server_service вернул не 200.
    """
    settings = get_settings()
    url = (
        f"{settings.server_service_url.rstrip('/')}"
        f"/api/server/v1/internal/secrets/reencrypt_outbox/seed"
    )
    client = get_server_service_client()
    try:
        response = await client.post(
            url, params={"limit": limit}, headers=_headers(),
        )
    except httpx.HTTPError as exc:
        raise CredentialFetchError(
            error_code="SERVER_SERVICE_UNREACHABLE",
            message=f"Failed to call server_service: {type(exc).__name__}",
        ) from exc
    if response.status_code != 200:
        raise CredentialFetchError(
            error_code="SECRETS_OUTBOX_SEED_REJECTED",
            message=f"server_service returned {response.status_code}",
            details={"limit": limit, "status_code": response.status_code},
        )
    return response.json()


async def claim_reencrypt_outbox_pending(limit: int) -> list[dict]:
    """Claim'нуть до `limit` pending outbox-row'ов для обработки.

    Server-side: `FOR UPDATE SKIP LOCKED` + переход pending → processing.
    Параллельные replica'и не конфликтуют — каждая получает свой непустой
    непересекающийся набор.

    Возвращает: список `{id, entity_type, entity_id, legacy_ciphertext,
    attempts}`. Пустой список — очередь иссякла.

    Возможные ошибки: `CredentialFetchError` с `error_code`:
      * `SERVER_SERVICE_UNREACHABLE` — transport.
      * `SECRETS_OUTBOX_CLAIM_REJECTED` — server_service вернул не 200.
    """
    settings = get_settings()
    url = (
        f"{settings.server_service_url.rstrip('/')}"
        f"/api/server/v1/internal/secrets/reencrypt_outbox/pending"
    )
    client = get_server_service_client()
    try:
        response = await client.get(
            url, params={"limit": limit}, headers=_headers(),
        )
    except httpx.HTTPError as exc:
        raise CredentialFetchError(
            error_code="SERVER_SERVICE_UNREACHABLE",
            message=f"Failed to call server_service: {type(exc).__name__}",
        ) from exc
    if response.status_code != 200:
        raise CredentialFetchError(
            error_code="SECRETS_OUTBOX_CLAIM_REJECTED",
            message=f"server_service returned {response.status_code}",
            details={"limit": limit, "status_code": response.status_code},
        )
    body = response.json() or {}
    items = body.get("items")
    return list(items) if isinstance(items, list) else []


async def finalize_reencrypt_outbox_done(outbox_id: str) -> dict:
    """Закрыть outbox-row: server_service делает decrypt+encrypt, пишет
    обратно в owner-row и помечает outbox `done`.

    Возвращает: `{id, status, skipped}`. `status="done"` — happy path;
    `skipped=True` — owner-row уже не legacy (ротация прошла параллельно).

    Возможные ошибки: `CredentialFetchError` с `error_code`:
      * `SERVER_SERVICE_UNREACHABLE` — transport.
      * `SECRETS_OUTBOX_FINALIZE_REJECTED` — server_service вернул не 200
        (включая `404 SECRETS_OUTBOX_ROW_NOT_FOUND` и `500
        SECRETS_REENCRYPT_FINALIZE_FAILED` при crypto-ошибке).
    """
    settings = get_settings()
    url = (
        f"{settings.server_service_url.rstrip('/')}"
        f"/api/server/v1/internal/secrets/reencrypt_outbox/{outbox_id}/done"
    )
    client = get_server_service_client()
    try:
        response = await client.post(url, headers=_headers())
    except httpx.HTTPError as exc:
        raise CredentialFetchError(
            error_code="SERVER_SERVICE_UNREACHABLE",
            message=f"Failed to call server_service: {type(exc).__name__}",
        ) from exc
    if response.status_code != 200:
        raise CredentialFetchError(
            error_code="SECRETS_OUTBOX_FINALIZE_REJECTED",
            message=f"server_service returned {response.status_code}",
            details={"outbox_id": outbox_id, "status_code": response.status_code},
        )
    return response.json()


async def finalize_reencrypt_outbox_failed(outbox_id: str, error: str) -> dict:
    """Пометить outbox-row `failed` с причиной (worker не смог завершить).

    Используется, когда `finalize_reencrypt_outbox_done` бросил
    SERVER_SERVICE_UNREACHABLE на повторных попытках, либо когда worker
    сам поймал ошибку до отправки POST .../done.

    Возвращает: `{id, status}`.
    """
    settings = get_settings()
    url = (
        f"{settings.server_service_url.rstrip('/')}"
        f"/api/server/v1/internal/secrets/reencrypt_outbox/{outbox_id}/failed"
    )
    # Текст ошибки обрезаем здесь же — schema требует ≤4096.
    body = {"error": (error or "unknown")[:4096]}
    client = get_server_service_client()
    try:
        response = await client.post(url, headers=_headers(), json=body)
    except httpx.HTTPError as exc:
        raise CredentialFetchError(
            error_code="SERVER_SERVICE_UNREACHABLE",
            message=f"Failed to call server_service: {type(exc).__name__}",
        ) from exc
    if response.status_code != 200:
        raise CredentialFetchError(
            error_code="SECRETS_OUTBOX_FINALIZE_REJECTED",
            message=f"server_service returned {response.status_code}",
            details={"outbox_id": outbox_id, "status_code": response.status_code},
        )
    return response.json()


async def submit_rotated_ipmi_password(
    ipmi_controller_id: str,
    new_password: str,
    rotated_at: str,
    target_department_id: str | None = None,
    verified_at: str | None = None,
) -> dict:
    """Отдать сгенерированный IPMI-пароль обратно в server_service.

    Симметрия `submit_rotated_password` — но для IPMI-controller'а
    (отдельная таблица `ipmi_controllers`, не `server_accounts`).
    server_service шифрует через `secrets_service.encrypt()` (AES-256-GCM
    + master-key) и сохраняет ciphertext в `ipmi_controllers.password_encrypted`.

    Контракт: worker применяет новый пароль на BMC, делает read-only verify
    запрос с НОВЫМ паролем (доказательство что BMC принял), и только потом
    POST'ит plaintext в server_service вместе с `verified_at`. server_service
    отказывает в записи без `verified_at` — иначе ciphertext мог бы хранить
    пароль, который BMC не принял (например, политика сложности).

    `ipmi_controller_id` — id записи в `ipmi_controllers`, не `server_id`
    (resource у endpoint'а — controller). Worker берёт его из
    `fetch_ipmi_credentials` response.

    `rotated_at` — ISO-8601 UTC timestamp момента успешного apply на BMC
    (фиксируется сразу после `dispatch_rotate_user_password` и сохраняется
    в Redis-stash'е, чтобы retry submit'а не пересчитывал его и не
    дрейфил относительно реального момента смены пароля).
    `verified_at` — ISO-8601 UTC timestamp успешного verify-вызова к BMC
    с новым паролем (BMC подтвердил применение).

    Возвращает: `{ok, rotated_at}` от
    `POST /api/server/v1/internal/ipmi-controllers/{controller_id}/credentials_rotated`.

    Возможные ошибки: `CredentialFetchError` с `error_code`:
      * `SERVER_SERVICE_UNREACHABLE` — transport.
      * `IPMI_ROTATE_REJECTED` — server_service вернул не 200
        (валидация policy, dept-mismatch, отказ хранилища, отсутствие
        `verified_at`).
    """
    settings = get_settings()
    url = (
        f"{settings.server_service_url.rstrip('/')}"
        f"/api/server/v1/internal/ipmi-controllers/{ipmi_controller_id}/credentials_rotated"
    )
    body: dict = {"new_password": new_password, "rotated_at": rotated_at}
    if verified_at is not None:
        body["verified_at"] = verified_at
    client = get_server_service_client()
    try:
        response = await client.post(
            url,
            headers=_headers(target_department_id),
            json=body,
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



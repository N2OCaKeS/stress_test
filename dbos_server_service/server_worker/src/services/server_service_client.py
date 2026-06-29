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
from src.core.http import bearer_header
from src.core.identifiers import validate_outbox_id
from src.services.http_pool import get_server_service_client

logger = logging.getLogger(__name__)


def _headers(target_department_id: str | None = None) -> dict[str, str]:
    """Собрать HTTP-заголовки для запроса к server_service.

    Включает:
      * `Authorization: Bearer <worker_bot_token>` (наличие токена
        гарантируется startup-валидатором в `src.core.config.Settings`: в
        dev/staging/production пустой `WORKER_BOT_TOKEN` валит старт воркера,
        в local/test conftest подставляет placeholder);
      * `X-Service-Identity: server_worker` — defense-in-depth для
        s2s-маркировки канала. Симметрично `audit_client.emit`
        (`X-Service-Identity: server_worker` уже шлётся в loging_service)
        и introspect-каналам соседних сервисов. server_service сегодня
        этот header на `/internal/*` не enforce'ит, но иметь его в каждом
        запросе worker'а позволяет (а) идентифицировать источник по audit
        access-log'у server_service, (б) в будущем включить strict-режим
        без изменения worker'ской стороны;
      * `X-Target-Department-Id` если caller передал dept (для cross-tenant
        cross-check'а в internal-эндпоинтах).
    """
    settings = get_settings()
    headers: dict[str, str] = bearer_header(settings.worker_bot_token)
    headers["X-Service-Identity"] = "server_worker"
    if target_department_id is not None:
        headers["X-Target-Department-Id"] = target_department_id
    return headers


def _url(path: str) -> str:
    """Собрать абсолютный URL к internal-эндпоинту server_service."""
    base = get_settings().server_service_url.rstrip("/")
    return f"{base}{path}"


async def _request(
    method: str,
    path: str,
    *,
    reject_code: str,
    target_department_id: str | None = None,
    json: dict | None = None,
    params: dict | None = None,
    details: dict | None = None,
    parse: bool = True,
    allow_empty_body: bool = False,
) -> dict:
    """Единая обвязка для internal-вызовов server_service.

    Каждая обёртка выше отличается только глаголом, путём, телом/параметрами
    и кодом ошибки на не-2xx (`reject_code`). Транспортный сбой всегда
    становится `SERVER_SERVICE_UNREACHABLE`, не-2xx — `reject_code` с
    `status_code` в `details`.

    `parse=True` — вернуть `response.json()`. `allow_empty_body=True` —
    2xx без тела (callback-endpoint'ы) отдаёт `{}` вместо падения на
    `ValueError`. `parse=False` — вернуть пустой dict, не читая тело.
    """
    client = get_server_service_client()
    request = getattr(client, method)
    kwargs: dict = {"headers": _headers(target_department_id)}
    if json is not None:
        kwargs["json"] = json
    if params is not None:
        kwargs["params"] = params
    try:
        response = await request(_url(path), **kwargs)
    except httpx.HTTPError as exc:
        raise CredentialFetchError(
            error_code="SERVER_SERVICE_UNREACHABLE",
            message=f"Failed to call server_service: {type(exc).__name__}",
        ) from exc
    if response.status_code >= 300:
        err_details = dict(details or {})
        err_details["status_code"] = response.status_code
        raise CredentialFetchError(
            error_code=reject_code,
            message=f"server_service returned {response.status_code}",
            details=err_details,
        )
    if not parse:
        return {}
    if allow_empty_body:
        try:
            return response.json()
        except ValueError:
            # 2xx без body — допустимо для callback-endpoint'ов.
            return {}
    return response.json()


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
    return await _request(
        "get",
        f"/api/server/v1/internal/servers/{server_id}/ipmi/credentials",
        reject_code="IPMI_CREDENTIALS_UNAVAILABLE",
        target_department_id=target_department_id,
        details={"server_id": server_id},
    )


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
    return await _request(
        "get",
        f"/api/server/v1/internal/servers/{server_id}/accounts/{account_id}/password",
        reject_code="ACCOUNT_PASSWORD_UNAVAILABLE",
        target_department_id=target_department_id,
        details={"server_id": server_id, "account_id": account_id},
    )


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
    return await _request(
        "post",
        f"/api/server/v1/internal/servers/{server_id}/accounts/{account_id}/password/rotate",
        reject_code="PASSWORD_ROTATE_REJECTED",
        target_department_id=target_department_id,
        json={"password": new_password},
        details={"server_id": server_id, "account_id": account_id},
    )


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
    return await _request(
        "post",
        f"/api/server/v1/internal/servers/{server_id}/inventory",
        reject_code="INVENTORY_SUBMIT_REJECTED",
        target_department_id=target_department_id,
        json=inventory_payload,
        details={"server_id": server_id},
        allow_empty_body=True,
    )


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

    Возвращает: `{ok, created, present, drifted, diffs, result_summary}` от
    `POST /api/server/v1/internal/servers/{id}/users/inventory`. `diffs` —
    структурированный per-account diff (account_id/login/fields с
    expected/found) для drift'нувших привязок; task'а кладёт его в
    `task.result`.

    Возможные ошибки: `CredentialFetchError` с `error_code`:
      * `SERVER_SERVICE_UNREACHABLE` — transport (timeout/connect).
      * `USERS_INVENTORY_SUBMIT_REJECTED` — server_service вернул не 2xx.
    """
    return await _request(
        "post",
        f"/api/server/v1/internal/servers/{server_id}/users/inventory",
        reject_code="USERS_INVENTORY_SUBMIT_REJECTED",
        target_department_id=target_department_id,
        json=users_payload,
        details={"server_id": server_id},
        allow_empty_body=True,
    )


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
    return await _request(
        "post",
        f"/api/server/v1/internal/servers/{server_id}/accounts/{account_id}/provision_status",
        reject_code="PROVISION_STATUS_REJECTED",
        target_department_id=target_department_id,
        json={"operation": operation, "present": present},
        details={"server_id": server_id, "account_id": account_id},
        allow_empty_body=True,
    )


async def fetch_management_credentials(
    server_id: str,
    target_department_id: str | None = None,
) -> dict:
    """Запросить расшифрованные per-server управляющие креды сервера.

    Возвращает: `{management_user, ssh_private_key, password}` от
    `GET /api/server/v1/internal/servers/{id}/management/credentials`. Это
    приватный ключ и пароль управляющего пользователя `dbos` именно на этом
    сервере (своя пара на каждый бокс, шифруется в server_service). Worker
    тянет их just-in-time под управляющую сессию вместо глобального env-ключа —
    тот же паттерн, что `fetch_account_password` / `fetch_ipmi_credentials`.

    Инвариант server_service: пока креды в состоянии `pending_apply` (новый
    материал записан, на боксе ещё старый) — эндпоинт отдаёт previous-материал,
    то есть реально рабочий на боксе. Поэтому вход всегда идёт тем ключом,
    который сервер действительно принимает.

    Возможные ошибки: `CredentialFetchError` с `error_code`:
      * `SERVER_SERVICE_UNREACHABLE` — transport (timeout/connect).
      * `MANAGEMENT_CREDENTIALS_UNAVAILABLE` — server_service вернул не 200
        (сервер не подготовлен, нет кред, отказ авторизации, dept-mismatch).
    """
    return await _request(
        "get",
        f"/api/server/v1/internal/servers/{server_id}/management/credentials",
        reject_code="MANAGEMENT_CREDENTIALS_UNAVAILABLE",
        target_department_id=target_department_id,
        details={"server_id": server_id},
    )


async def submit_management_creds_applied(
    server_id: str,
    target_department_id: str | None = None,
) -> dict:
    """Подтвердить, что новые управляющие креды применены на боксе.

    Финал `server.rotate_management_creds` task'а: новый ключ установлен и
    проверен живым входом, старый убран. server_service по этому callback'у
    снимает `mgmt_creds_pending_apply`, зануляет previous-материал и фиксирует
    `mgmt_creds_rotated_at`. Тело пустое — server_service берёт `server_id` из
    пути.

    Возвращает: `{}` (или тело callback'а) от
    `POST /api/server/v1/internal/servers/{id}/management-credentials/applied`.

    Возможные ошибки: `CredentialFetchError` с `error_code`:
      * `SERVER_SERVICE_UNREACHABLE` — transport (timeout/connect).
      * `MANAGEMENT_CREDS_APPLIED_REJECTED` — server_service вернул не 2xx.
    """
    return await _request(
        "post",
        f"/api/server/v1/internal/servers/{server_id}/management-credentials/applied",
        reject_code="MANAGEMENT_CREDS_APPLIED_REJECTED",
        target_department_id=target_department_id,
        details={"server_id": server_id},
        allow_empty_body=True,
    )


async def submit_prepared(
    server_id: str,
    management_user: str,
    target_department_id: str | None = None,
    *,
    management_mode: str | None = None,
) -> dict:
    """Сообщить server_service, что бутстрап управления сервера завершён.

    Финал `server.prepare` task'а: управляющий пользователь заведён и
    публичный ключ положен. server_service помечает сервер подготовленным
    (`is_managed=True`, `prepared_at`, management_user, management_mode).

    `management_mode` — детектнутая на боксе редакция ОС
    (`astra_orel`/`astra_smolensk`/`astra_voronezh`/`other_os`); server_service
    сохраняет её на сервере. `None` (старый воркер / детект не отработал) —
    поле не отправляем, server_service оставит прежнее значение.

    Возвращает: `{ok, is_managed, prepared_at}` от
    `POST /api/server/v1/internal/servers/{id}/prepared`.

    Возможные ошибки: `CredentialFetchError` с `error_code`:
      * `SERVER_SERVICE_UNREACHABLE` — transport (timeout/connect).
      * `PREPARE_STATUS_REJECTED` — server_service вернул не 2xx.
    """
    body: dict = {"management_user": management_user}
    if management_mode is not None:
        body["management_mode"] = management_mode
    return await _request(
        "post",
        f"/api/server/v1/internal/servers/{server_id}/prepared",
        reject_code="PREPARE_STATUS_REJECTED",
        target_department_id=target_department_id,
        json=body,
        details={"server_id": server_id},
        allow_empty_body=True,
    )


async def fetch_secrets_migration_status() -> dict:
    """Запросить сводку по постепенной ротации мастер-ключа.

    Возвращает: `{remaining, total, active_version, by_version}` от
    `GET /api/server/v1/internal/secrets/migration_status`.

    Возможные ошибки: `CredentialFetchError` с `error_code`:
      * `SERVER_SERVICE_UNREACHABLE` — transport.
      * `SECRETS_MIGRATION_STATUS_UNAVAILABLE` — server_service вернул не 200.
    """
    return await _request(
        "get",
        "/api/server/v1/internal/secrets/migration_status",
        reject_code="SECRETS_MIGRATION_STATUS_UNAVAILABLE",
    )


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
    return await _request(
        "post",
        "/api/server/v1/internal/secrets/reencrypt_batch",
        reject_code="SECRETS_REENCRYPT_REJECTED",
        params={"limit": limit},
        details={"limit": limit},
    )


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
    return await _request(
        "post",
        "/api/server/v1/internal/secrets/reencrypt_outbox/seed",
        reject_code="SECRETS_OUTBOX_SEED_REJECTED",
        params={"limit": limit},
        details={"limit": limit},
    )


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
    body = await _request(
        "get",
        "/api/server/v1/internal/secrets/reencrypt_outbox/pending",
        reject_code="SECRETS_OUTBOX_CLAIM_REJECTED",
        params={"limit": limit},
        details={"limit": limit},
    ) or {}
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
    # `validate_outbox_id` пропускает только `[A-Za-z0-9_-]{1,64}` —
    # формат `rox_<32 hex>` из server_service. `/` `.` `:` и пробелы
    # отбиваются, поэтому путь-traversal (`../admin`) и SQLi-вставки до
    # подстановки в URL не доходят.
    outbox_id = validate_outbox_id(outbox_id)
    return await _request(
        "post",
        f"/api/server/v1/internal/secrets/reencrypt_outbox/{outbox_id}/done",
        reject_code="SECRETS_OUTBOX_FINALIZE_REJECTED",
        details={"outbox_id": outbox_id},
    )


async def finalize_reencrypt_outbox_failed(outbox_id: str, error: str) -> dict:
    """Пометить outbox-row `failed` с причиной (worker не смог завершить).

    Используется, когда `finalize_reencrypt_outbox_done` бросил
    SERVER_SERVICE_UNREACHABLE на повторных попытках, либо когда worker
    сам поймал ошибку до отправки POST .../done.

    Возвращает: `{id, status}`.
    """
    outbox_id = validate_outbox_id(outbox_id)
    # Текст ошибки обрезаем здесь же — schema требует ≤4096.
    body = {"error": (error or "unknown")[:4096]}
    return await _request(
        "post",
        f"/api/server/v1/internal/secrets/reencrypt_outbox/{outbox_id}/failed",
        reject_code="SECRETS_OUTBOX_FINALIZE_REJECTED",
        json=body,
        details={"outbox_id": outbox_id},
    )


async def submit_rotated_ipmi_password(
    ipmi_controller_id: str,
    new_password: str,
    rotated_at: str,
    target_department_id: str | None = None,
    *,
    verified_at: str,
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
    body: dict = {
        "new_password": new_password,
        "rotated_at": rotated_at,
        "verified_at": verified_at,
    }
    return await _request(
        "post",
        f"/api/server/v1/internal/ipmi-controllers/{ipmi_controller_id}/credentials_rotated",
        reject_code="IPMI_ROTATE_REJECTED",
        target_department_id=target_department_id,
        json=body,
        details={"ipmi_controller_id": ipmi_controller_id},
    )



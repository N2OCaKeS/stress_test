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


async def fetch_account_password_by_id(
    account_id: str,
    target_department_id: str | None = None,
) -> dict:
    """Запросить пароль аккаунта по одному `account_id` (без server_id).

    Нужно провижну привязанных к ВМ учёток: dispatch `vm.create` несёт только
    `account_id`/`login`, исходный сервер аккаунта неизвестен. server_service
    резолвит аккаунт по глобально-уникальному id и отдаёт `{login, password}`.

    Возвращает: `{login, password}` от
    `GET /api/server/v1/internal/accounts/{account_id}/password`.

    Возможные ошибки: `CredentialFetchError` с `error_code`:
      * `SERVER_SERVICE_UNREACHABLE` — transport.
      * `ACCOUNT_PASSWORD_UNAVAILABLE` — server_service вернул не 200.
    """
    return await _request(
        "get",
        f"/api/server/v1/internal/accounts/{account_id}/password",
        reject_code="ACCOUNT_PASSWORD_UNAVAILABLE",
        target_department_id=target_department_id,
        details={"account_id": account_id},
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


async def submit_power_state(
    server_id: str,
    power_state: str,
    source: str,
    target_department_id: str | None = None,
    *,
    ping_reachable: bool | None = None,
    ping_latency_ms: float | None = None,
    ssh_reachable: bool | None = None,
    ssh_latency_ms: float | None = None,
    ipmi_power_state: str | None = None,
) -> dict:
    """Отдать собранные сигналы состояния питания обратно в server_service.

    Финал `power.status` task'а: worker меряет три независимых сигнала (ping и
    ssh с latency, ipmi) и шлёт их все. server_service обновляет кэш
    `servers.power_state` и свежие сигналы; anti-clobber (не перетирать
    закэшированное `on`/`off` транзиентным `unknown`) он решает сам, видя все
    три сигнала. Без этого round-trip'а кэш всегда `unknown` — писать некому.

    `power_state` — legacy first-wins `on`/`off`/`unknown`; `source` —
    `bmc`/`ping`/`ssh`. Новые сигналы: `ping_reachable`/`ping_latency_ms`,
    `ssh_reachable`/`ssh_latency_ms`, `ipmi_power_state` (`on`/`off`/`unknown`).
    Latency — в миллисекундах либо `None` при недоступности. Параметры сигналов
    опциональны (старый вызов без них шлёт `null`), но `power.status` передаёт
    их все.

    Возвращает: `{ok, power_state, checked_at}` от
    `POST /api/server/v1/internal/servers/{id}/power-state`.

    Возможные ошибки: `CredentialFetchError` с `error_code`:
      * `SERVER_SERVICE_UNREACHABLE` — transport (timeout/connect).
      * `POWER_STATE_REJECTED` — server_service вернул не 2xx.
    """
    return await _request(
        "post",
        f"/api/server/v1/internal/servers/{server_id}/power-state",
        reject_code="POWER_STATE_REJECTED",
        target_department_id=target_department_id,
        json={
            "power_state": power_state,
            "source": source,
            "ping_reachable": ping_reachable,
            "ping_latency_ms": ping_latency_ms,
            "ssh_reachable": ssh_reachable,
            "ssh_latency_ms": ssh_latency_ms,
            "ipmi_power_state": ipmi_power_state,
        },
        details={"server_id": server_id},
        allow_empty_body=True,
    )


async def trigger_auto_inventory_sweep() -> dict:
    """Запустить плановый авто-inventory прогон на стороне server_service.

    Периодик `auto_inventory.sweep` (worker-scheduler) даёт лишь расписание;
    сам фан-аут (список managed-серверов + dispatch inventory.sync + power.status
    на каждый) делает server_service — воркер не дублирует его БД. Прогон
    платформенный, без привязки к отделу, поэтому `X-Target-Department-Id` не шлём.

    Возвращает: `{ok, total_managed, processed, dispatched_tasks, truncated}` от
    `POST /api/server/v1/internal/servers/auto-inventory-sweep`.

    Возможные ошибки: `CredentialFetchError` с `error_code`:
      * `SERVER_SERVICE_UNREACHABLE` — transport (timeout/connect).
      * `AUTO_INVENTORY_SWEEP_REJECTED` — server_service вернул не 2xx.
    """
    return await _request(
        "post",
        "/api/server/v1/internal/servers/auto-inventory-sweep",
        reject_code="AUTO_INVENTORY_SWEEP_REJECTED",
    )


async def trigger_power_sweep() -> dict:
    """Запустить частый power-sweep на стороне server_service.

    Периодик `power.sweep` (worker-scheduler) даёт лишь расписание; сам фан-аут
    (список ВСЕХ активных серверов + dispatch `power.status` на каждый) делает
    server_service. В отличие от auto-inventory-sweep — только проба ping/ssh/ipmi,
    без inventory.sync и без фильтра is_managed. Прогон платформенный, поэтому
    `X-Target-Department-Id` не шлём.

    Возвращает: `{ok, total_servers, processed, dispatched_tasks, truncated}` от
    `POST /api/server/v1/internal/servers/power-sweep`.

    Возможные ошибки: `CredentialFetchError` с `error_code`:
      * `SERVER_SERVICE_UNREACHABLE` — transport (timeout/connect).
      * `POWER_SWEEP_REJECTED` — server_service вернул не 2xx.
    """
    return await _request(
        "post",
        "/api/server/v1/internal/servers/power-sweep",
        reject_code="POWER_SWEEP_REJECTED",
    )


async def trigger_vm_create_reconcile() -> dict:
    """Запустить reconcile упавших vm.create на стороне server_service.

    Периодик `vms.reconcile_failed_creates` (worker-scheduler) даёт лишь
    расписание; логику (ВМ в busy_state=creating, чью vm.create-задачу воркер
    завершил ошибкой → best-effort undefine + каскадное удаление + уведомление
    создателя) выполняет server_service. Прогон платформенный, поэтому
    `X-Target-Department-Id` не шлём.

    Возвращает: `{ok, checked, deleted, skipped}` от
    `POST /api/server/v1/internal/vms/reconcile-failed-creates`.

    Возможные ошибки: `CredentialFetchError` с `error_code`:
      * `SERVER_SERVICE_UNREACHABLE` — transport (timeout/connect).
      * `VM_CREATE_RECONCILE_REJECTED` — server_service вернул не 2xx.
    """
    return await _request(
        "post",
        "/api/server/v1/internal/vms/reconcile-failed-creates",
        reject_code="VM_CREATE_RECONCILE_REJECTED",
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


async def submit_astra_update_result(
    server_id: str,
    os_version_id: str,
    succeeded: bool,
    target_department_id: str | None = None,
) -> dict:
    """Сообщить server_service исход обновления ОС (astra_update).

    Финал `server.astra_update` task'а. `succeeded=True` — `apt update &&
    astra-update` прошли; server_service привязывает сервер к целевой версии
    и запускает inventory.sync. `succeeded=False` — обновление упало; вызывается
    из except-ветки handler'а, чтобы снять updating-блокировку (иначе сервер
    завис бы «в обновлении» до ручного release'а). В обоих случаях server_service
    переводит `busy_state` обратно в `free`.

    Возвращает: `{ok, os_version_id, busy_state}` от
    `POST /api/server/v1/internal/servers/{id}/astra-updated`.

    Возможные ошибки: `CredentialFetchError` с `error_code`:
      * `SERVER_SERVICE_UNREACHABLE` — transport (timeout/connect).
      * `ASTRA_UPDATE_STATUS_REJECTED` — server_service вернул не 2xx.
    """
    return await _request(
        "post",
        f"/api/server/v1/internal/servers/{server_id}/astra-updated",
        reject_code="ASTRA_UPDATE_STATUS_REJECTED",
        target_department_id=target_department_id,
        json={"os_version_id": os_version_id, "succeeded": succeeded},
        details={"server_id": server_id},
        allow_empty_body=True,
    )


async def submit_vm_state(
    vm_id: str,
    target_department_id: str | None = None,
    *,
    power_state: str | None = None,
    ip_address: str | None = None,
    status: str | None = None,
    busy_state: str | None = None,
    clear_busy_state: bool = False,
    snapshots: list[str] | None = None,
    autostart: bool | None = None,
    graphics_port: int | None = None,
    error: str | None = None,
) -> dict:
    """Отдать server_service свежее состояние ВМ (финал `vm.create` / `vm.power`).

    По этому callback'у server_service обновляет строку `vm`: `power_state`
    (из `virsh domstate`), выданный `ip_address`, `status`/`busy_state`
    (booking/lock), зеркалит список снимков (`snapshots` — только plain-имена,
    без системных `_build`), `autostart` (флаг автозапуска ВМ при старте hub'а,
    финал `vm.set_autostart`) и `graphics_port` (TCP-порт дисплея vnc/spice на
    хабе, финал `vm.console_prep` — server_service кладёт его в `vms.graphics_port`
    и в консольный токен). `error` заполняется только при частичном/неудачном
    исходе, чтобы оператор увидел причину в карточке ВМ.

    Все поля опциональны: `vm.power` шлёт лишь `power_state`, `vm.create` —
    полный набор. `None`-поля server_service трактует как «не менять».
    `clear_busy_state=True` снимает lifecycle-lock (busy_state→NULL) — так
    `vm.update` завершает операцию (server_service выставлял `updating` на
    dispatch'е).

    Возвращает: тело `POST /api/server/v1/internal/vms/{vm_id}/state`.

    Возможные ошибки: `CredentialFetchError` с `error_code`:
      * `SERVER_SERVICE_UNREACHABLE` — transport (timeout/connect).
      * `VM_STATE_REJECTED` — server_service вернул не 2xx.
    """
    body: dict = {}
    if power_state is not None:
        body["power_state"] = power_state
    if ip_address is not None:
        body["ip_address"] = ip_address
    if status is not None:
        body["status"] = status
    if busy_state is not None:
        body["busy_state"] = busy_state
    if clear_busy_state:
        body["clear_busy_state"] = True
    if snapshots is not None:
        body["snapshots"] = snapshots
    if autostart is not None:
        body["autostart"] = autostart
    if graphics_port is not None:
        body["graphics_port"] = graphics_port
    if error is not None:
        body["error"] = error
    return await _request(
        "post",
        f"/api/server/v1/internal/vms/{vm_id}/state",
        reject_code="VM_STATE_REJECTED",
        target_department_id=target_department_id,
        json=body,
        details={"vm_id": vm_id},
        allow_empty_body=True,
    )


async def submit_vm_prepared(
    vm_id: str,
    management_user: str,
    target_department_id: str | None = None,
) -> dict:
    """Подтвердить, что per-VM управляющие креды установлены на госте.

    Финал `vm.prepare` task'а: worker зашёл на гостя дефолтными кредами образа
    (`u`/`1`), завёл управляющего пользователя, положил ему публичный ключ и
    пароль (их сгенерил и прислал server_service), проверил вход по ключу,
    захардил sshd и удалил базовую учётку `u`. По этому callback'у server_service
    помечает ВМ управляемой (`is_managed=True`, `management_user`) — зеркало
    `submit_prepared` для физсервера. Зашифрованный материал server_service уже
    держит у себя (он его выдал в dispatch-stash), worker ничего секретного
    обратно не шлёт.

    Возвращает: тело
    `POST /api/server/v1/internal/vms/{vm_id}/prepared`.

    Возможные ошибки: `CredentialFetchError` с `error_code`:
      * `SERVER_SERVICE_UNREACHABLE` — transport (timeout/connect).
      * `VM_PREPARE_STATUS_REJECTED` — server_service вернул не 2xx.
    """
    return await _request(
        "post",
        f"/api/server/v1/internal/vms/{vm_id}/prepared",
        reject_code="VM_PREPARE_STATUS_REJECTED",
        target_department_id=target_department_id,
        json={"management_user": management_user},
        details={"vm_id": vm_id},
        allow_empty_body=True,
    )


async def submit_vm_disk_state(
    vm_id: str,
    disk_id: str,
    state: str,
    target_department_id: str | None = None,
    *,
    target_dev: str | None = None,
    path: str | None = None,
    serial: str | None = None,
    size_gb: int | None = None,
    error: str | None = None,
) -> dict:
    """Отдать server_service состояние диска ВМ (финал disk-тасок).

    По этому callback'у server_service обновляет строку `vm_disks`: `state`
    (`ready`/`deleted`/`error`) и по факту привязки — `target_dev`, `path`,
    `serial`, `size_gb`. `attach` шлёт полный набор атрибутов созданного диска,
    `resize` — новый `size_gb`, `delete` — `state='deleted'`, ошибка любой из
    них — `state='error'` + `error`. `None`-поля трактуются как «не менять».

    Возвращает: тело
    `POST /api/server/v1/internal/vms/{vm_id}/disks` (батч-синк по `disk_id`).

    Возможные ошибки: `CredentialFetchError` с `error_code`:
      * `SERVER_SERVICE_UNREACHABLE` — transport (timeout/connect).
      * `VM_DISK_STATE_REJECTED` — server_service вернул не 2xx.
    """
    disk: dict = {"disk_id": disk_id, "state": state}
    if target_dev is not None:
        disk["target_dev"] = target_dev
    if path is not None:
        disk["path"] = path
    if serial is not None:
        disk["serial"] = serial
    if size_gb is not None:
        disk["size_gb"] = size_gb
    return await _request(
        "post",
        f"/api/server/v1/internal/vms/{vm_id}/disks",
        reject_code="VM_DISK_STATE_REJECTED",
        target_department_id=target_department_id,
        json={"disks": [disk]},
        details={"vm_id": vm_id, "disk_id": disk_id},
        allow_empty_body=True,
    )


async def submit_vm_snapshots(
    vm_id: str,
    snapshots: list[dict],
    target_department_id: str | None = None,
) -> dict:
    """Отдать server_service состояние снимков ВМ (финал snapshot-тасок).

    По этому callback'у server_service синкает строки `vm_snapshots` по
    `snapshot_id`/`name`: `state` (`ready`/`deleted`/`error`), `kind`,
    `is_current` и (для reroll/astra) свежий набор пересозданных снимков.
    Формат тела симметричен disk-callback'у — батч `{snapshots: [...]}`.

    `snapshot_create` шлёт один снимок `state='ready'` (`is_current=True`),
    `snapshot_delete` — `state='deleted'`, `snapshot_revert` — реверт-цель с
    `is_current=True`, `astra_update` — новый `<rc>`-снимок, `allta_update`/
    `passwd` — все пересозданные (не-`_build`) снимки. Для режима `per_snapshot`
    server_service по `is_current`-снимку переключает активные mgmt-креды ВМ.

    Каждый элемент — dict с обязательным `name` и опциональными `snapshot_id`,
    `state`, `snapshot_type` (способ снятия disk_only/full), `kind` (категория
    os_baseline/user), `mode` (oryol/smolensk), `os_version`, `is_system`,
    `is_current`, `error`. `None`-поля не кладём.

    Возвращает: тело
    `POST /api/server/v1/internal/vms/{vm_id}/snapshots`.

    Возможные ошибки: `CredentialFetchError` с `error_code`:
      * `SERVER_SERVICE_UNREACHABLE` — transport (timeout/connect).
      * `VM_SNAPSHOT_STATE_REJECTED` — server_service вернул не 2xx.
    """
    return await _request(
        "post",
        f"/api/server/v1/internal/vms/{vm_id}/snapshots",
        reject_code="VM_SNAPSHOT_STATE_REJECTED",
        target_department_id=target_department_id,
        json={"snapshots": snapshots},
        details={"vm_id": vm_id},
        allow_empty_body=True,
    )


async def record_vm_packages(
    vm_id: str,
    packages: list,
    target_department_id: str | None = None,
    *,
    source: str | None = None,
    task_id: str | None = None,
) -> dict:
    """Отдать server_service список установленных пакетов гостя ВМ (финал `vm.list_packages`).

    По этому callback'у server_service полностью перезаписывает инвентарь
    пакетов ВМ (строка `vm_package_inventory`) — как серверный
    `installed_packages.list`, но для гостя ВМ. `packages` — список
    `{name, version}` (`version` может быть `None`). `source` — какой менеджер
    снял срез (`dpkg`/`rpm`), для UI-подсказки. `task_id` — id задачи
    `vm.list_packages` для трассировки.

    Тело: `{packages: [{name, version}], source, task_id}` под
    `VmPackagesCallbackRequest` server_service'а.

    Возвращает: `{ok, vm_id, package_count}` от
    `POST /api/server/v1/internal/vms/{vm_id}/packages`.

    Возможные ошибки: `CredentialFetchError` с `error_code`:
      * `SERVER_SERVICE_UNREACHABLE` — transport (timeout/connect).
      * `VM_PACKAGES_REJECTED` — server_service вернул не 2xx.
    """
    body: dict = {"packages": packages}
    if source is not None:
        body["source"] = source
    if task_id is not None:
        body["task_id"] = task_id
    return await _request(
        "post",
        f"/api/server/v1/internal/vms/{vm_id}/packages",
        reject_code="VM_PACKAGES_REJECTED",
        target_department_id=target_department_id,
        json=body,
        details={"vm_id": vm_id},
        allow_empty_body=True,
    )


async def submit_vms_hub_state(
    server_id: str,
    prepared: bool,
    target_department_id: str | None = None,
    *,
    phy_if: str | None = None,
    error: str | None = None,
) -> dict:
    """Сообщить server_service исход подготовки сервера как VMS-hub'а.

    Финал `vms_hub.prepare` task'а. `prepared=True` — libvirt поднят, мост
    `br0` над физическим NIC настроен, storage-pool и образы на месте;
    server_service помечает сервер `vms_hub` (`virtualization=True`) и
    сохраняет `phy_if`. `prepared=False` вызывается из except-ветки handler'а с
    заполненным `error`, чтобы оператор увидел, на чём подготовка встала.

    Возвращает: тело
    `POST /api/server/v1/internal/servers/{server_id}/vms-hub-state`.

    Возможные ошибки: `CredentialFetchError` с `error_code`:
      * `SERVER_SERVICE_UNREACHABLE` — transport (timeout/connect).
      * `VMS_HUB_STATE_REJECTED` — server_service вернул не 2xx.
    """
    body: dict = {"prepared": prepared}
    if phy_if is not None:
        body["phy_if"] = phy_if
    if error is not None:
        body["error"] = error
    return await _request(
        "post",
        f"/api/server/v1/internal/servers/{server_id}/vms-hub-state",
        reject_code="VMS_HUB_STATE_REJECTED",
        target_department_id=target_department_id,
        json=body,
        details={"server_id": server_id},
        allow_empty_body=True,
    )


async def submit_vms_hub_torn_down(
    server_id: str,
    torn_down: bool,
    target_department_id: str | None = None,
    *,
    removed_vms: list[str] | None = None,
    error: str | None = None,
) -> dict:
    """Сообщить server_service исход teardown'а VMS-hub'а (финал `vms_hub.teardown`).

    Зеркало `submit_vms_hub_state`, но обратное: `torn_down=True` — ВМ отдела
    сняты и удалены, storage-pool и образы снесены, опционально выпилены пакеты
    виртуализации и мост `br0`; server_service снимает с сервера роль hub'а
    (`virtualization=False`, статус обратно из `vms_hub`). `removed_vms` —
    имена доменов, которые воркер реально снёс (для сверки/аудита).
    `torn_down=False` едет из except-ветки handler'а с заполненным `error`,
    чтобы оператор увидел, на чём teardown встал.

    Возвращает: тело
    `POST /api/server/v1/internal/servers/{server_id}/vms-hub-teardown`.

    Возможные ошибки: `CredentialFetchError` с `error_code`:
      * `SERVER_SERVICE_UNREACHABLE` — transport (timeout/connect).
      * `VMS_HUB_TEARDOWN_REJECTED` — server_service вернул не 2xx.
    """
    body: dict = {"torn_down": torn_down}
    if removed_vms is not None:
        body["removed_vms"] = removed_vms
    if error is not None:
        body["error"] = error
    return await _request(
        "post",
        f"/api/server/v1/internal/servers/{server_id}/vms-hub-teardown",
        reject_code="VMS_HUB_TEARDOWN_REJECTED",
        target_department_id=target_department_id,
        json=body,
        details={"server_id": server_id},
        allow_empty_body=True,
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



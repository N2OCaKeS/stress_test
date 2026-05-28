"""Управление питанием — power.on / power.off / power.reboot / power.status.

Все четыре идут по одной схеме: тянут IPMI-креды через server_service, ходят
в BMC и возвращают новое состояние питания. Транспорт выбирает `get_bmc_client`
(`src/clients/__init__.py`): HEAD `/redfish/v1/` → `RedfishClient` при успехе,
fallback на `IpmitoolClient` для legacy BMC без Redfish (Supermicro X9/X10,
generic IPMI 2.0).

`target_department_id` из payload (server_service кладёт при dispatch'е)
форвардится как заголовок `X-Target-Department-Id` — server_service сверяет
worker-PAT с фактическим dept сервера.

`AUDIT_SAFE_FIELDS` — whitelist ключей result'а, которые `_runner.run_task`
кладёт в `audit details.result`. Всё остальное (raw stdout, креды, токены)
в audit не уходит, даже если случайно попадёт в return-dict.

Все power.* возвращают только `{power_state, ...}` — `power_state` маппится
из Redfish-нотации (`On`/`Off`/`PoweringOn`/`PoweringOff`) в lower-case
строку. Для ipmitool transport приходит только `on`/`off`, нормализатор тот же.
Дополнительные поля: `rebooted: True` у `power_reboot`, `previous_power_state`
у `power_off`.
"""

from __future__ import annotations

import logging

from src.clients.ipmitool import IpmitoolError
from src.clients.redfish import RedfishClient, RedfishError
from src.core.config import get_settings
from src.main import broker
from src.services import server_service_client
from src.tasks._bmc_errors import (
    dispatch_get_power_state,
    dispatch_power_action,
    wrap_bmc_error,
)
from src.tasks._bmc_helpers import aclose_bmc as _aclose_bmc
from src.tasks._bmc_helpers import get_bmc as _get_bmc
from src.tasks._runner import run_task

logger = logging.getLogger(__name__)

# Whitelist для audit. power_state — единственное публичное поле во всех
# 4 power.* handler'ах; `rebooted` у power.reboot; `previous_power_state`
# полезен для post-action диагностики.
AUDIT_SAFE_FIELDS: set[str] = {"power_state", "rebooted", "previous_power_state"}


def _normalize_power_state(state: str) -> str:
    """Redfish/ipmitool `PowerState` → lower-case строка ('on'/'off'/'powering_on'/...).

    Сохраняем нижний регистр (как отдавал mock и как ожидают callers
    из server_service). Camel-case → snake_case: `PoweringOn` →
    `powering_on`, `On` → `on`. Для ipmitool вход уже `on`/`off`, transformation
    идемпотентна.
    """
    out = []
    for i, ch in enumerate(state):
        if i > 0 and ch.isupper():
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


def _build_client(creds: dict) -> RedfishClient:
    """Legacy-фабрика чистого `RedfishClient` для тестов.

    Используется только тестовым кодом (`monkeypatch.setattr(..., _build_client, ...)`)
    в `tests/test_task_handlers.py` и `tests/test_power_tasks_redfish.py`.
    Production-путь через `_get_bmc` — он сам решает Redfish vs ipmitool.
    """
    settings = get_settings()
    return RedfishClient(
        host=creds["endpoint_url"],
        username=creds["username"],
        password=creds["password"],
        verify_tls=settings.redfish_verify_tls,
        timeout=settings.redfish_timeout_seconds,
    )


@broker.task("power.on")
async def power_on(task_id: str) -> None:
    """Включить питание сервера через BMC (Redfish с fallback на ipmitool).

    Что делает: тянет IPMI-креды → `On` action на BMC → возвращает новое
    `PowerState`.

    Параметры: `task_id` — ID task'и в DB worker'а. Полезная нагрузка
    (`server_id`, опционально `target_department_id`) лежит в `task.payload`.

    Возвращает: ничего напрямую (результат пишется в `task.result` и
    audit через `_runner`). Handler-impl возвращает `{power_state}`.

    Возможные ошибки: `CredentialFetchError` (server_service недоступен
    или вернул не 200), `AppException(BMC_*)` — ловит `_runner` и
    retry'ит / mark_failed.

    Связано с: `server.power_on` audit action.
    """
    async def _impl(payload: dict) -> dict:
        server_id = payload["server_id"]
        target_dept = payload.get("target_department_id")
        creds = await server_service_client.fetch_ipmi_credentials(server_id, target_dept)
        client = await _get_bmc(creds)
        try:
            try:
                await dispatch_power_action(client, "On")
                state = await dispatch_get_power_state(client)
            except (RedfishError, IpmitoolError) as exc:
                raise wrap_bmc_error("power_on", exc) from exc
        finally:
            await _aclose_bmc(client)
        return {"power_state": _normalize_power_state(state)}

    await run_task(
        task_id,
        audit_action="server.power_on",
        audit_target_type="server",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS,
    )


@broker.task("power.off")
async def power_off(task_id: str) -> None:
    """Выключить питание сервера (всегда hard).

    Что делает: тянет IPMI-креды → `ForceOff` (Redfish) / `chassis power off`
    (ipmitool). Никакого ACPI-shutdown: ОС не получает SIGTERM, питание
    срезается немедленно. Возвращает новое `PowerState`.

    Параметры: `task_id`. Payload — `server_id`, опционально
    `target_department_id`. Никаких ключей вроде `graceful` payload не
    принимает — они тихо игнорируются.

    Возвращает: `{power_state, previous_power_state}`.

    Возможные ошибки: `CredentialFetchError`, `AppException(BMC_*)`.

    Связано с: `server.power_off` audit action.
    """
    async def _impl(payload: dict) -> dict:
        server_id = payload["server_id"]
        target_dept = payload.get("target_department_id")
        creds = await server_service_client.fetch_ipmi_credentials(server_id, target_dept)
        client = await _get_bmc(creds)
        try:
            try:
                previous = await dispatch_get_power_state(client)
                await dispatch_power_action(client, "ForceOff")
                state = await dispatch_get_power_state(client)
            except (RedfishError, IpmitoolError) as exc:
                raise wrap_bmc_error("power_off", exc) from exc
        finally:
            await _aclose_bmc(client)
        return {
            "power_state": _normalize_power_state(state),
            "previous_power_state": _normalize_power_state(previous),
        }

    await run_task(
        task_id,
        audit_action="server.power_off",
        audit_target_type="server",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS,
    )


@broker.task("power.reboot")
async def power_reboot(task_id: str) -> None:
    """Перезагрузить сервер через BMC.

    Что делает: тянет IPMI-креды → `GracefulRestart` (или `ForceRestart` если
    `force=true` в payload). Возвращает `{power_state, rebooted: True}`.

    Параметры: `task_id`. Payload — `server_id`, опционально
    `target_department_id`, опционально `force: bool` (default false —
    graceful если ОС жива, force когда явно «hard reset»).

    Возвращает: `{power_state, rebooted: True}`.

    Возможные ошибки: `CredentialFetchError`, `AppException(BMC_*)`.

    Связано с: `server.power_reboot` audit action.
    """
    async def _impl(payload: dict) -> dict:
        server_id = payload["server_id"]
        target_dept = payload.get("target_department_id")
        force = bool(payload.get("force", False))
        creds = await server_service_client.fetch_ipmi_credentials(server_id, target_dept)
        client = await _get_bmc(creds)
        try:
            try:
                await dispatch_power_action(
                    client, "ForceRestart" if force else "GracefulRestart",
                )
                state = await dispatch_get_power_state(client)
            except (RedfishError, IpmitoolError) as exc:
                raise wrap_bmc_error("power_reboot", exc) from exc
        finally:
            await _aclose_bmc(client)
        return {
            "power_state": _normalize_power_state(state),
            "rebooted": True,
        }

    await run_task(
        task_id,
        audit_action="server.power_reboot",
        audit_target_type="server",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS,
    )


@broker.task("power.status")
async def power_status(task_id: str) -> None:
    """Спросить у BMC текущее состояние питания.

    Что делает: тянет IPMI-креды → GET PowerState через Redfish либо
    `chassis power status` через ipmitool.

    Параметры: `task_id`. Payload — `server_id`, опционально
    `target_department_id`.

    Возвращает: `{power_state}` (`'on' | 'off' | 'powering_on' | ...`).

    Возможные ошибки: `CredentialFetchError`, `AppException(BMC_*)`.

    Связано с: `server.power_status` audit action.
    """
    async def _impl(payload: dict) -> dict:
        server_id = payload["server_id"]
        target_dept = payload.get("target_department_id")
        creds = await server_service_client.fetch_ipmi_credentials(server_id, target_dept)
        client = await _get_bmc(creds)
        try:
            try:
                state = await dispatch_get_power_state(client)
            except (RedfishError, IpmitoolError) as exc:
                raise wrap_bmc_error("power_status", exc) from exc
        finally:
            await _aclose_bmc(client)
        return {"power_state": _normalize_power_state(state)}

    await run_task(
        task_id,
        audit_action="server.power_status",
        audit_target_type="server",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS,
    )

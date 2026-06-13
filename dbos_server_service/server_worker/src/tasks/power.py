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

import json
import logging
from datetime import datetime, timezone

from src.clients.ipmitool import IpmitoolError
from src.clients.redfish import RedfishError
from src.core.constants import STASH_TTL_SECONDS
from src.core.identifiers import validate_task_id
from src.main import broker
from src.services import redis_pool
from src.services import server_service_client
from src.services import bmc_circuit_breaker as _breaker
from src.services.redis_stash_crypto import (
    aad_for_redis_stash,
    decrypt_stash,
    encrypt_stash,
)
from src.tasks._bmc_errors import (
    dispatch_get_power_state,
    dispatch_power_action,
    wrap_bmc_error,
)
from src.tasks._bmc_helpers import aclose_bmc as _aclose_bmc
from src.tasks._bmc_helpers import extract_bmc_host as _extract_bmc_host
from src.tasks._bmc_helpers import get_bmc as _get_bmc
from src.tasks._runner import run_task

logger = logging.getLogger(__name__)

# Whitelist для audit. power_state — единственное публичное поле во всех
# 4 power.* handler'ах; `rebooted` у power.reboot; `previous_power_state`
# полезен для post-action диагностики.
AUDIT_SAFE_FIELDS: set[str] = {"power_state", "rebooted", "previous_power_state"}


# One-shot marker для reboot'а. Симметрично stash'ам ротации паролей в
# `tasks/passwords.py`, но проще: хранить нечего, кроме факта «reboot уже
# выдан на BMC». Зачем guard именно reboot'у: on/off идемпотентны (повторный
# `On`/`ForceOff` приводит сервер в то же состояние), а вот reboot —
# деструктивная operation. Durable-retry поднимает `_impl` заново, если
# предыдущая попытка успела выдать reset на BMC, но упала на последующем
# `get_power_state` или commit'е терминального статуса. Без guard'а второй
# reset влетит в сервер, который может ещё грузить ОС после первого — окно
# для повреждения FS. Маркер ставим сразу после того, как BMC принял reset:
# повторный заход видит маркер, пропускает reset и идёт сразу к verify.
#
# Если упал САМ reset (BMC отбил/недоступен) — маркер не выставлен, retry
# честно повторяет reset. Guard срабатывает только против повторной выдачи
# ПОСЛЕ успешного reset'а.
_REBOOT_ISSUED_KEY_PREFIX = "dbos:power_reboot_issued:"


async def _read_reboot_issued(task_id: str) -> bool:
    """Проверить one-shot-маркер: был ли reset уже выдан на BMC в этой dispatch'е.

    Маркер зашифрован тем же master-key, что и остальные stash'и worker'а
    (envelope-формат, AAD binding'уется к task_id). Битый/swap-нутый маркер
    → `AppException(STASH_DECRYPT_*)` из `decrypt_stash` — это лучше тихого
    fallback'а: оператор увидит явную ошибку, а не молчаливый повторный
    reboot.
    """
    validate_task_id(task_id)
    client = redis_pool.get_redis()
    raw = await client.get(_REBOOT_ISSUED_KEY_PREFIX + task_id)
    if raw is None:
        return False
    text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
    # Расшифровываем, чтобы не принять чужой/битый ключ за валидный маркер.
    decrypt_stash(text, aad=aad_for_redis_stash(task_id))
    return True


async def _store_reboot_issued(task_id: str) -> None:
    """Поставить one-shot-маркер сразу после того, как BMC принял reset.

    Кладём с TTL (тем же, что у rotate-stash'ей): покрывает суммарное окно
    durable back-off'а. По истечении ключ исчезает сам — если к этому
    моменту dispatch так и не завершилась, новая попытка имеет право
    повторить reboot (старый reset давно «прожёван», ОС либо загрузилась,
    либо застряла, и повтор не страшнее ручного reset'а оператором).
    Value — минимальный JSON с моментом выдачи, для диагностики.
    """
    validate_task_id(task_id)
    client = redis_pool.get_redis()
    token = encrypt_stash(
        json.dumps({"issued_at": datetime.now(timezone.utc).isoformat()}),
        aad=aad_for_redis_stash(task_id),
    )
    await client.set(
        _REBOOT_ISSUED_KEY_PREFIX + task_id,
        token,
        ex=STASH_TTL_SECONDS,
    )


async def _delete_reboot_issued(task_id: str) -> None:
    """Снять one-shot-маркер после успешного завершения reboot'а.

    TTL подстрахует, явный DELETE сокращает окно жизни ключа. Ошибки
    глушим — посмертный cleanup не должен валить и без того успешную задачу.
    """
    validate_task_id(task_id)
    client = redis_pool.get_redis()
    try:
        await client.delete(_REBOOT_ISSUED_KEY_PREFIX + task_id)
    except Exception:  # noqa: BLE001
        logger.debug("failed to delete power reboot one-shot marker", exc_info=True)


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
        host = _extract_bmc_host(creds["endpoint_url"])
        await _breaker.check(host)
        client = await _get_bmc(creds)
        try:
            try:
                await dispatch_power_action(client, "On")
                state = await dispatch_get_power_state(client)
            except (RedfishError, IpmitoolError, ValueError, RuntimeError) as exc:
                await _breaker.record_failure(host)
                raise wrap_bmc_error("power_on", exc) from exc
            await _breaker.record_success(host)
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
        host = _extract_bmc_host(creds["endpoint_url"])
        await _breaker.check(host)
        client = await _get_bmc(creds)
        try:
            try:
                previous = await dispatch_get_power_state(client)
                await dispatch_power_action(client, "ForceOff")
                state = await dispatch_get_power_state(client)
            except (RedfishError, IpmitoolError, ValueError, RuntimeError) as exc:
                await _breaker.record_failure(host)
                raise wrap_bmc_error("power_off", exc) from exc
            await _breaker.record_success(host)
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

    One-shot guard: reset выдаётся ровно один раз на dispatch. Маркер в
    Redis (`_REBOOT_ISSUED_KEY_PREFIX + task_id`) ставится сразу после
    того, как BMC принял reset; durable-retry, поднявший `_impl` заново
    после фейла на get_power_state/commit, видит маркер и пропускает
    повторный reset. on/off такого guard'а не требуют — они идемпотентны.

    Возможные ошибки: `CredentialFetchError`, `AppException(BMC_*)`.

    Связано с: `server.power_reboot` audit action.
    """
    async def _impl(payload: dict) -> dict:
        server_id = payload["server_id"]
        target_dept = payload.get("target_department_id")
        force = bool(payload.get("force", False))
        creds = await server_service_client.fetch_ipmi_credentials(server_id, target_dept)
        host = _extract_bmc_host(creds["endpoint_url"])
        await _breaker.check(host)

        # Если предыдущая попытка уже выдала reset на BMC (а упала позже —
        # на get_power_state или commit'е терминального статуса), повторно
        # ребутить нельзя: сервер мог ещё грузиться. Пропускаем reset, идём
        # сразу к verify.
        already_issued = await _read_reboot_issued(task_id)

        client = await _get_bmc(creds)
        try:
            try:
                if not already_issued:
                    await dispatch_power_action(
                        client, "ForceRestart" if force else "GracefulRestart",
                    )
                    # Маркер ставим сразу после того, как BMC принял reset, но
                    # до get_power_state: если verify/commit упадут и пойдёт
                    # retry, он увидит маркер и не выдаст второй reset.
                    await _store_reboot_issued(task_id)
                state = await dispatch_get_power_state(client)
            except (RedfishError, IpmitoolError, ValueError, RuntimeError) as exc:
                await _breaker.record_failure(host)
                raise wrap_bmc_error("power_reboot", exc) from exc
            await _breaker.record_success(host)
        finally:
            await _aclose_bmc(client)

        # Дошли до успешного результата — маркер больше не нужен.
        await _delete_reboot_issued(task_id)

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
        host = _extract_bmc_host(creds["endpoint_url"])
        await _breaker.check(host)
        client = await _get_bmc(creds)
        try:
            try:
                state = await dispatch_get_power_state(client)
            except (RedfishError, IpmitoolError, ValueError, RuntimeError) as exc:
                await _breaker.record_failure(host)
                raise wrap_bmc_error("power_status", exc) from exc
            await _breaker.record_success(host)
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

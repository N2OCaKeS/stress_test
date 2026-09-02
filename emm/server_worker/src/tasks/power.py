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
from src.core.config import get_settings
from src.core.constants import STASH_TTL_SECONDS
from src.core.exceptions import AppException, CredentialFetchError
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
from src.tasks._reachability import probe_reachability_signals
from src.tasks._runner import run_task

logger = logging.getLogger(__name__)

# Whitelist для audit. power_state — единственное публичное поле во всех
# 4 power.* handler'ах; `rebooted` у power.reboot; `previous_power_state`
# полезен для post-action диагностики; `source` у power.status показывает
# оператору, откуда взято состояние (bmc / ping / ssh). Три независимых
# сигнала power.status (ping/ssh reachability + latency, ipmi_power_state) —
# диагностика, не секреты, поэтому тоже пускаем в audit.
AUDIT_SAFE_FIELDS: set[str] = {
    "power_state", "rebooted", "previous_power_state", "source",
    "ping_reachable", "ping_latency_ms",
    "ssh_reachable", "ssh_latency_ms",
    "ipmi_power_state",
}


# One-shot marker для reboot'а. Симметрично stash'ам ротации паролей в
# `tasks/passwords.py`, но проще: хранить нечего, кроме факта «reboot уже
# выдан на BMC». Зачем guard именно reboot'у: on/off идемпотентны (повторный
# `On`/`ForceOff` приводит сервер в то же состояние), а вот reboot —
# деструктивная operation. Durable-retry поднимает `_impl` заново, если
# предыдущая попытка успела выдать reset на BMC, но упала на последующем
# `get_power_state` или commit'е терминального статуса. Без guard'а второй
# reset влетит в сервер, который может ещё грузить ОС после первого — окно
# для повреждения FS. Повторный заход видит маркер, пропускает reset и идёт
# сразу к verify.
#
# Порядок важен: маркер ставим ДО выдачи reset'а. Если запись в Redis упадёт
# (блип), reset ещё не выдан — retry честно повторит его, и второго reset в
# грузящийся сервер не будет. Обратная сторона — reset выдаётся только после
# успешной записи маркера (на мёртвом Redis reboot ждёт восстановления, а не
# бьёт вслепую). Если упал САМ reset (BMC отбил/недоступен) — маркер снимаем в
# except, чтобы retry честно повторил reset. Guard срабатывает только против
# повторной выдачи ПОСЛЕ успешно принятого reset'а.
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

    Что делает: тянет IPMI-креды → всегда `ForceRestart`. Возвращает
    `{power_state, rebooted: True}`.

    Reboot всегда жёсткий: graceful-вариант полагается на гостевой ACPI-агент,
    которого на стендах нет. Без него BMC принимает GracefulRestart, но ОС его
    игнорирует и сервер молча не перезагружается — проверено на iLO/iDRAC.

    Параметры: `task_id`. Payload — `server_id`, опционально
    `target_department_id`. Поле `force` server_service ещё может слать, но оно
    больше не влияет на поведение.

    Возвращает: `{power_state, rebooted: True}`.

    One-shot guard: reset выдаётся ровно один раз на dispatch. Маркер в
    Redis (`_REBOOT_ISSUED_KEY_PREFIX + task_id`) ставится ДО выдачи reset'а;
    durable-retry, поднявший `_impl` заново после фейла на get_power_state/
    commit, видит маркер и пропускает повторный reset. Сбой записи маркера
    оставляет reset невыданным (retry повторит безопасно), а фейл самого
    reset'а снимает маркер (retry повторит reset). on/off такого guard'а не
    требуют — они идемпотентны.

    Возможные ошибки: `CredentialFetchError`, `AppException(BMC_*)`.

    Связано с: `server.power_reboot` audit action.
    """
    async def _impl(payload: dict) -> dict:
        server_id = payload["server_id"]
        target_dept = payload.get("target_department_id")
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
                    # Маркер ставим ДО reset'а. Если запись в Redis упадёт,
                    # исключение уйдёт в retry, а reset при этом ещё не выдан —
                    # повтор безопасен. store после reset'а (fail-open) на
                    # блипе Redis привёл бы ко второму reset'у на повторе.
                    await _store_reboot_issued(task_id)
                    try:
                        await dispatch_power_action(client, "ForceRestart")
                    except (RedfishError, IpmitoolError, ValueError, RuntimeError):
                        # Сам reset не прошёл — маркер снимаем, иначе retry
                        # пропустит reset и сервер не перезагрузится.
                        await _delete_reboot_issued(task_id)
                        raise
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


def _payload_ssh_port(payload: dict) -> int:
    """Достать SSH-порт сервера из payload, иначе дефолт из настроек.

    server_service кладёт `ssh_port`/`port` в payload для SSH-операций; для
    power.status порт нужен только reachability-пробе. На кривое значение
    тихо падаем на дефолтный порт — fallback не должен ронять из-за этого.
    """
    raw = payload.get("ssh_port") or payload.get("port")
    if raw:
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass
    return get_settings().power_reachability_ssh_port


# Нейтральный набор сетевых сигналов, когда пробовать нечего (нет host в
# payload либо reachability отключён настройкой).
_NO_REACHABILITY_SIGNALS: dict = {
    "ping_reachable": False,
    "ping_latency_ms": None,
    "ssh_reachable": False,
    "ssh_latency_ms": None,
}


async def _collect_reachability_signals(payload: dict) -> dict:
    """Замерить ping и ssh по адресу сервера из payload (`host`/`ssh_host`).

    Обе пробы независимые (не first-wins) и меряют latency. Адрес — это
    `str(server.ip_address)`, который server_service кладёт в payload; SSH-порт
    берём из `_payload_ssh_port`. Если reachability отключён настройкой или
    host в payload нет — возвращаем нейтральные сигналы (всё недоступно), а не
    падаем: это диагностика, а не обязательный шаг.
    """
    settings = get_settings()
    if not settings.power_reachability_fallback_enabled:
        return dict(_NO_REACHABILITY_SIGNALS)

    host = payload.get("host") or payload.get("ssh_host")
    if not host:
        logger.info(
            "power.status: no server host in payload; ping/ssh signals unavailable",
        )
        return dict(_NO_REACHABILITY_SIGNALS)

    return await probe_reachability_signals(
        host,
        ssh_port=_payload_ssh_port(payload),
        ping_timeout=settings.power_reachability_ping_timeout_seconds,
        tcp_timeout=settings.power_reachability_tcp_timeout_seconds,
    )


async def _probe_ipmi_power_state(server_id: str, target_dept: str | None) -> str:
    """Спросить состояние питания у BMC. Вернуть `on` / `off` / `unknown`.

    Best-effort: любой сбой на BMC-пути (нет IPMI-контроллера, breaker open,
    контроллер не ответил) сводится к `unknown` и НЕ роняет остальные сигналы
    power.status. Единственное исключение — транспортный сбой server_service
    (`SERVER_SERVICE_UNREACHABLE`): он реально временный, поэтому пробрасывается
    в retry, а не маскируется под `unknown`.

    Переходные состояния Redfish (`powering_on`/`powering_off`) сюда не
    попадают как definite — нормализуем к `unknown`, чтобы поле несло ровно
    `on`/`off`/`unknown`, как ждёт server_service.
    """
    try:
        creds = await server_service_client.fetch_ipmi_credentials(server_id, target_dept)
    except CredentialFetchError as exc:
        if exc.error_code == "SERVER_SERVICE_UNREACHABLE":
            raise
        # У сервера просто нет IPMI-контроллера/кред — штатная ситуация для
        # массы боксов, и периодический sweep дёргает эту ветку по каждому из
        # них. Пишем DEBUG, а не INFO: в логах не должно быть шума на каждом
        # тике. ipmi_power_state=unknown — валидный сигнал.
        logger.debug(
            "power.status: IPMI credentials unavailable (%s), ipmi_power_state=unknown",
            exc.error_code,
        )
        return "unknown"

    host = _extract_bmc_host(creds["endpoint_url"])
    try:
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
    except AppException as exc:
        logger.info(
            "power.status: BMC probe failed (%s), ipmi_power_state=unknown",
            exc.error_code,
        )
        return "unknown"

    normalized = _normalize_power_state(state)
    return normalized if normalized in ("on", "off") else "unknown"


def _assemble_power_result(signals: dict, ipmi_power_state: str) -> dict:
    """Собрать итог power.status: три сигнала + legacy `power_state`/`source`.

    Legacy-пара считается прежней first-wins логикой для обратной совместимости
    кэша `servers.power_state`: definite ipmi → (ipmi, `bmc`); иначе ping → (`on`,
    `ping`); иначе ssh → (`on`, `ssh`); иначе (`unknown`, `bmc`). Сетевая
    недоступность в `off` не превращается.
    """
    if ipmi_power_state in ("on", "off"):
        power_state, source = ipmi_power_state, "bmc"
    elif signals["ping_reachable"]:
        power_state, source = "on", "ping"
    elif signals["ssh_reachable"]:
        power_state, source = "on", "ssh"
    else:
        power_state, source = "unknown", "bmc"
    return {
        "power_state": power_state,
        "source": source,
        "ping_reachable": signals["ping_reachable"],
        "ping_latency_ms": signals["ping_latency_ms"],
        "ssh_reachable": signals["ssh_reachable"],
        "ssh_latency_ms": signals["ssh_latency_ms"],
        "ipmi_power_state": ipmi_power_state,
    }


async def _writeback_power_state(
    server_id: str, result: dict, target_dept: str | None,
) -> None:
    """Best-effort: сообщить итог power.status обратно в server_service.

    server_service по этому callback'у обновляет кэш `servers.power_state` и
    свежие сигналы (ping/ssh reachability + latency, ipmi_power_state). Фейл
    callback'а НЕ должен валить read-only power.status, поэтому глушим ЛЮБУЮ
    ошибку — не только `CredentialFetchError`, но и сырые transport/HTTP-
    исключения: результат уже вычислен и всё равно уйдёт в `task.result` и
    audit. Пробрось мы её — таска ушла бы в retry/queued, а состояние, которое
    уже определили, потерялось бы.

    Пишем ВСЕГДА, в том числе при `power_state=unknown`: `ping_reachable=False`
    или `ipmi_power_state=unknown` — это валидные сигналы, которые надо
    записать. Anti-clobber (не перетирать закэшированное `on`/`off`
    транзиентным `unknown`) теперь решает server_service на приёмной стороне,
    видя все три сигнала, а не худеет их до одного `power_state`.

    `target_dept` отсутствует в payload → `_headers` просто не добавит
    `X-Target-Department-Id`, как у соседних callback'ов; не падаем.
    """
    try:
        await server_service_client.submit_power_state(
            server_id,
            result["power_state"],
            result.get("source", "bmc"),
            target_dept,
            ping_reachable=result.get("ping_reachable"),
            ping_latency_ms=result.get("ping_latency_ms"),
            ssh_reachable=result.get("ssh_reachable"),
            ssh_latency_ms=result.get("ssh_latency_ms"),
            ipmi_power_state=result.get("ipmi_power_state"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "power.status writeback failed server_id=%s error=%s; "
            "result kept in task.result",
            server_id,
            getattr(exc, "error_code", type(exc).__name__),
        )


@broker.task("power.status")
async def power_status(task_id: str) -> None:
    """Собрать три независимых сигнала о состоянии сервера: ping, ssh, ipmi.

    Что делает: всегда меряет сетевую достижимость самого сервера (ICMP-ping
    и TCP SSH-порт по адресу из payload, с latency) И независимо опрашивает
    BMC (Redfish/ipmitool). Ни один из трёх сигналов не «схлопывает» остальные:
    даже если BMC недоступен, ping/ssh всё равно меряются, и наоборот.

    Параметры: `task_id`. Payload — `server_id`, опционально
    `target_department_id`, а для сетевых проб — `host`/`ssh_host` (+`ssh_port`)
    сервера (это `str(server.ip_address)`); без них ping/ssh недоступны.

    Возвращает: `{power_state, source, ping_reachable, ping_latency_ms,
    ssh_reachable, ssh_latency_ms, ipmi_power_state}`. Первые два — legacy-пара
    (first-wins) для обратной совместимости кэша `servers.power_state`;
    остальные пять — сырые сигналы, которые уходят в server_service.

    В отличие от power.on/off/reboot, power.status — read-only запрос и не
    падает на недоступном BMC: всегда возвращает какое-то состояние. Единственный
    fail — транспортный сбой server_service при запросе IPMI-кред
    (`SERVER_SERVICE_UNREACHABLE`), он уходит в retry.

    Связано с: `server.power_status` audit action.
    """
    async def _impl(payload: dict) -> dict:
        server_id = payload["server_id"]
        target_dept = payload.get("target_department_id")

        signals = await _collect_reachability_signals(payload)
        ipmi_power_state = await _probe_ipmi_power_state(server_id, target_dept)

        result = _assemble_power_result(signals, ipmi_power_state)
        await _writeback_power_state(server_id, result, target_dept)
        return result

    await run_task(
        task_id,
        audit_action="server.power_status",
        audit_target_type="server",
        impl=_impl,
        audit_safe_fields=AUDIT_SAFE_FIELDS,
    )

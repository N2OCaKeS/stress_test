"""Фоновые probe-циклы воркера: reachability (ping+ssh) и power (ipmi/domstate).

Заменяют частые sweep'ы `power.sweep`/`vms.status_sweep`, которые диспатчили
`power.status`/`vm.status` на каждую цель и плодили task-row'ы, конкурируя за
handler-слоты. Здесь те же пробы крутятся в двух фоновых asyncio-loop'ах (как
`heartbeat_loop`): loop тянет цели через `/internal/probe-targets`, снимает
сигналы своим пулом (выделенный `asyncio.Semaphore`, не taskiq-слоты) и пишет
результат тем же callback'ом, что и on-demand задачи (`submit_power_state` /
`submit_vm_state`). Task-row'ы не создаются.

Два независимых цикла с разной каденцией:

* **reachability** — каждые `reachability_probe_interval_seconds`: ping+ssh
  сервера (`probe_reachability_signals`) и гостя ВМ (через hub-сессию);
* **power** — каждые `power_probe_interval_seconds`: ipmi сервера
  (`_probe_ipmi_power_state`) и domstate домена ВМ (`_probe_power`).

Интервалы и вкл/выкл читаются свежими каждый цикл из
`/internal/settings/probes` — правки из UI применяются без рестарта воркера.
Loop never dies: сбой одной цели логируется и глотается, транспортный сбой
settings/targets откладывает цикл на fallback-интервал, `CancelledError`
пробрасывается для graceful shutdown'а.

On-demand `@broker.task("power.status")` / `@broker.task("vm.status")` остаются
рабочими для ручного «обновить статус» из UI — они редкие и не спамят.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger("server_worker.probe_loop")

# Fallback-интервалы, если settings недоступны (совпадают с дефолтами
# ProbeSettings на стороне server_service). Держим цикл живым, не молотя в
# server_service чаще, чем надо, пока он недоступен.
_FALLBACK_REACHABILITY_INTERVAL = 60.0
_FALLBACK_POWER_INTERVAL = 300.0

# Нижняя граница сна цикла — защита от кривых настроек (0/отрицательных),
# чтобы loop не превратился в busy-loop.
_MIN_INTERVAL = 5.0


def _semaphore() -> asyncio.Semaphore:
    from src.core.config import get_settings

    return asyncio.Semaphore(get_settings().probe_loop_concurrency)


# ── reachability loop ────────────────────────────────────────────────────────


async def _probe_server_reachability(sem: asyncio.Semaphore, target: dict) -> None:
    """Снять ping+ssh одного сервера и записать сигналы в server_service.

    Read-only: любой сбой пробы/записи логируется и глотается, чтобы одна цель
    не роняла тик. `power_state`/`source` считаем из ping/ssh (legacy-пара для
    списка серверов); ipmi эта проба не трогает (`ipmi_power_state=None`).
    """
    from src.services import server_service_client
    from src.tasks._reachability import probe_reachability_signals
    from src.tasks.power import _assemble_power_result
    from src.utils.redaction import redact_error_message
    from src.core.config import get_settings

    settings = get_settings()
    async with sem:
        try:
            signals = await probe_reachability_signals(
                str(target["host"]),
                ssh_port=int(target.get("ssh_port") or settings.power_reachability_ssh_port),
                ping_timeout=settings.power_reachability_ping_timeout_seconds,
                tcp_timeout=settings.power_reachability_tcp_timeout_seconds,
            )
            # ipmi не снимаем в reachability-цикле → legacy-пара считается из
            # ping/ssh (ipmi="unknown" в ассемблере); ipmi-колонку не трогаем.
            result = _assemble_power_result(signals, "unknown")
            await server_service_client.submit_power_state(
                target["server_id"],
                result["power_state"],
                result["source"],
                target.get("department_id"),
                ping_reachable=signals["ping_reachable"],
                ping_latency_ms=signals["ping_latency_ms"],
                ssh_reachable=signals["ssh_reachable"],
                ssh_latency_ms=signals["ssh_latency_ms"],
                ipmi_power_state=None,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — одна цель не должна ронять тик
            logger.warning(
                "reachability probe failed server_id=%s: %s",
                target.get("server_id"),
                redact_error_message(f"{type(exc).__name__}: {exc}"),
            )


async def _probe_vm_reachability(sem: asyncio.Semaphore, target: dict) -> None:
    """Снять ping+ssh гостя одной ВМ через hub-сессию и записать в server_service.

    Открывает управляющую SSH-сессию к hub'у (ВМ живёт на нём), определяет
    LAN-адрес гостя и с hub'а пингует его и пробит SSH-порт. NAT/SLIRP-гость без
    LAN-адреса → ping/ssh недоступны (не ошибка). Read-only: любой сбой глотается.
    """
    from src.services import server_service_client
    from src.tasks.vms_status import (
        _GUEST_SSH_PORT,
        _guest_ip_best_effort,
        _probe_ping,
        _probe_ssh,
    )
    from src.tasks._vms_helpers import open_hub_session, validate_name
    from src.utils.redaction import redact_error_message
    from src.core.config import get_settings

    settings = get_settings()
    ping_timeout = max(1, int(round(settings.power_reachability_ping_timeout_seconds)))
    tcp_timeout = max(1, int(round(settings.power_reachability_tcp_timeout_seconds)))
    payload = _vm_hub_payload(target)
    async with sem:
        try:
            vm_name = validate_name(
                target["vm_name"], str(target.get("hub_host") or "hub"), "vm_name",
            )
            ssh_port = target.get("guest_ssh_port") or _GUEST_SSH_PORT
            session, _host = await open_hub_session(payload)
            async with session as ssh:
                guest_ip = await _guest_ip_best_effort(ssh, vm_name, payload)
                if guest_ip:
                    ping_reachable = await _probe_ping(ssh, guest_ip, ping_timeout)
                    ssh_reachable = await _probe_ssh(
                        ssh, guest_ip, int(ssh_port), tcp_timeout,
                    )
                else:
                    ping_reachable = False
                    ssh_reachable = False
            await server_service_client.submit_vm_state(
                target["vm_id"],
                target_department_id=target.get("department_id"),
                ping_reachable=ping_reachable,
                ssh_reachable=ssh_reachable,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "reachability probe failed vm_id=%s: %s",
                target.get("vm_id"),
                redact_error_message(f"{type(exc).__name__}: {exc}"),
            )


# ── power loop ───────────────────────────────────────────────────────────────


async def _probe_server_power(sem: asyncio.Semaphore, target: dict) -> None:
    """Опросить питание сервера по BMC (ipmi) и записать сигнал в server_service.

    Best-effort: нет IPMI-контроллера / breaker open / BMC молчит → `unknown`,
    цель не роняет тик. Legacy-пару обновляем только на определённом on/off.
    """
    from src.services import server_service_client
    from src.tasks.power import _probe_ipmi_power_state
    from src.utils.redaction import redact_error_message

    async with sem:
        try:
            ipmi_state = await _probe_ipmi_power_state(
                target["server_id"], target.get("department_id"),
            )
            power_state = ipmi_state if ipmi_state in ("on", "off") else "unknown"
            await server_service_client.submit_power_state(
                target["server_id"],
                power_state,
                "bmc",
                target.get("department_id"),
                ipmi_power_state=ipmi_state,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "power probe failed server_id=%s: %s",
                target.get("server_id"),
                redact_error_message(f"{type(exc).__name__}: {exc}"),
            )


async def _probe_vm_power(sem: asyncio.Semaphore, target: dict) -> None:
    """Снять питание домена ВМ (`virsh domstate`) через hub-сессию и записать.

    Read-only: недоступный hub / кривой домен → любой сбой глотается, тик живёт.
    """
    from src.services import server_service_client
    from src.tasks.vms_status import _probe_power
    from src.tasks._vms_helpers import open_hub_session, validate_name
    from src.utils.redaction import redact_error_message

    payload = _vm_hub_payload(target)
    async with sem:
        try:
            vm_name = validate_name(
                target["vm_name"], str(target.get("hub_host") or "hub"), "vm_name",
            )
            session, _host = await open_hub_session(payload)
            async with session as ssh:
                power_state = await _probe_power(ssh, vm_name)
            await server_service_client.submit_vm_state(
                target["vm_id"],
                target_department_id=target.get("department_id"),
                power_state=power_state,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "power probe failed vm_id=%s: %s",
                target.get("vm_id"),
                redact_error_message(f"{type(exc).__name__}: {exc}"),
            )


def _vm_hub_payload(target: dict) -> dict:
    """Собрать payload для `open_hub_session`/probe-хелперов из ВМ-цели.

    Зеркалит то, что `_dispatch_vm_status` клал в task-payload (hub-адресация +
    имя домена + guest_ip), чтобы probe-функции из `vms_status.py` работали без
    изменений.
    """
    return {
        "server_id": target["hub_server_id"],
        "hub_server_id": target["hub_server_id"],
        "target_department_id": target.get("department_id"),
        "host": str(target["hub_host"]),
        "ssh_port": target.get("hub_ssh_port"),
        "is_managed": target.get("hub_is_managed", True),
        "management_user": target.get("hub_management_user"),
        "vm_id": target["vm_id"],
        "vm_name": target["vm_name"],
        "name": target["vm_name"],
        "guest_ip": target.get("guest_ip"),
    }


# ── tick + loop-обвязка ──────────────────────────────────────────────────────


async def _run_probes(
    server_probe, vm_probe, *, kind: str,
) -> None:
    """Один тик пробы: тянем цели и параллельно пробим их пулом с семафором.

    `server_probe`/`vm_probe` — coroutine-функции `(sem, target) -> None`, каждая
    сама глотает свои ошибки. Транспортный сбой на `get_probe_targets`
    пробрасывается наверх (loop отложит тик), чтобы не молотить в упавший
    server_service.
    """
    from src.services import server_service_client

    payload = await server_service_client.get_probe_targets()
    servers = payload.get("servers") or []
    vms = payload.get("vms") or []
    if not servers and not vms:
        return

    sem = _semaphore()
    tasks = [server_probe(sem, t) for t in servers]
    tasks += [vm_probe(sem, t) for t in vms]
    # return_exceptions=True — belt-and-suspenders: per-target coroutine'ы уже
    # глотают свои ошибки, но CancelledError не глушим (пробросится из gather).
    await asyncio.gather(*tasks, return_exceptions=True)
    logger.debug(
        "%s probe tick done: servers=%s vms=%s", kind, len(servers), len(vms),
    )


async def _interval_and_enabled(kind: str) -> tuple[float, bool]:
    """Прочитать свежие интервал + enabled для цикла `kind` из server_service.

    Транспортный сбой settings → fallback-интервал и enabled=False (пропускаем
    тик), чтобы цикл переживал недоступность server_service, не спамя пробами.
    """
    from src.services import server_service_client
    from src.utils.redaction import redact_error_message

    if kind == "reachability":
        interval_key = "reachability_probe_interval_seconds"
        enabled_key = "reachability_probe_enabled"
        fallback = _FALLBACK_REACHABILITY_INTERVAL
    else:
        interval_key = "power_probe_interval_seconds"
        enabled_key = "power_probe_enabled"
        fallback = _FALLBACK_POWER_INTERVAL

    try:
        settings = await server_service_client.get_probe_settings()
    except Exception as exc:  # noqa: BLE001 — settings недоступны → пропускаем тик
        logger.warning(
            "%s loop: probe settings unavailable, skipping tick: %s",
            kind, redact_error_message(f"{type(exc).__name__}: {exc}"),
        )
        return fallback, False

    interval = float(settings.get(interval_key) or fallback)
    enabled = bool(settings.get(enabled_key))
    return max(_MIN_INTERVAL, interval), enabled


async def run_reachability_loop() -> None:
    """Фоновый цикл reachability-проб (ping+ssh серверов и гостей ВМ)."""
    logger.info("reachability probe loop started")
    while True:
        interval, enabled = await _interval_and_enabled("reachability")
        if enabled:
            try:
                await _run_probes(
                    _probe_server_reachability,
                    _probe_vm_reachability,
                    kind="reachability",
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — loop не должен падать
                from src.utils.redaction import redact_error_message
                logger.warning(
                    "reachability loop tick failed: %s",
                    redact_error_message(f"{type(exc).__name__}: {exc}"),
                )
        await asyncio.sleep(interval)


async def run_power_loop() -> None:
    """Фоновый цикл power-проб (ipmi серверов и domstate доменов ВМ)."""
    logger.info("power probe loop started")
    while True:
        interval, enabled = await _interval_and_enabled("power")
        if enabled:
            try:
                await _run_probes(
                    _probe_server_power,
                    _probe_vm_power,
                    kind="power",
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — loop не должен падать
                from src.utils.redaction import redact_error_message
                logger.warning(
                    "power loop tick failed: %s",
                    redact_error_message(f"{type(exc).__name__}: {exc}"),
                )
        await asyncio.sleep(interval)

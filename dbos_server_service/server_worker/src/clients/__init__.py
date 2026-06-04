"""HTTP-клиенты к внешним системам (iDRAC/iLO/BMC через Redfish и т.п.).

Отличаются от `src/services/*` тем, что это «edge»-интеграции: говорят с
устройствами или внешними сервисами по HTTP, не несут бизнес-логики
worker'а. `services/` — внутренний слой (audit publisher, server_service
internal endpoints).

Два транспорта BMC:

* **Redfish** (HTTPS API DMTF) — основной для современных контроллеров
  (iDRAC9+, iLO5+, Supermicro X11+, OpenBMC). Async-клиент на `httpx`.
* **ipmitool** (CLI fallback) — для старых BMC (Supermicro X9/X10, generic
  IPMI 1.5/2.0), где Redfish API отсутствует либо отдаёт 404 на корне.

`get_bmc_client` пробует Redfish-probe (HEAD `/redfish/v1/`) и при неудаче
возвращает `IpmitoolClient`. Probe-таймаут короткий (1.5s), чтобы fallback
не задерживал power-task больше, чем нужно.
"""

from __future__ import annotations

import logging
import ssl
from typing import Literal

import httpx

from src.clients.ipmitool import IpmitoolClient, IpmitoolError
from src.clients.redfish import RedfishClient, resolve_manager_id
from src.core.config import get_settings
from src.services.http_pool import (
    get_bmc_probe_client,
    get_bmc_redfish_transport,
)

logger = logging.getLogger(__name__)

__all__ = [
    "IpmitoolClient",
    "IpmitoolError",
    "RedfishClient",
    "get_bmc_client",
]


_REDFISH_PROBE_PATH = "/redfish/v1/"
_REDFISH_PROBE_TIMEOUT_SECONDS = 1.5


def _is_tls_error(exc: BaseException) -> bool:
    """Распознать SSL/TLS-ошибку в цепочке исключений httpx.

    `httpx` оборачивает stdlib `ssl.SSLError` в `httpx.ConnectError`
    (через `httpcore`). Идём по `__cause__` / `__context__` пока не
    встретим `ssl.SSLError` или `ssl.SSLCertVerificationError`.
    """
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, ssl.SSLError):
            return True
        cur = cur.__cause__ or cur.__context__
    return False


async def _probe_redfish(host: str, *, scheme: str = "https") -> bool:
    """Одна попытка HEAD `/redfish/v1/` с заданными scheme и verify.

    Возвращает True если корень Redfish ответил 200/401/403/405 (есть и
    обслуживается). False — connection-refused / timeout / 404 / TLS-fail
    при verify=True.

    HEAD без auth — Redfish-сервисный корень публичен по спецификации
    DMTF и не требует креденшалов.

    TLS-валидация управляется через `Settings.redfish_verify_tls` (env
    `REDFISH_VERIFY_TLS`), как и у полноценного `RedfishClient`. Default —
    `verify=True`: без валидации MITM может вернуть HTTP 200 на HEAD и
    принудить dispatcher выбрать Redfish-транспорт там, где реальный BMC
    отвечает только через ipmitool.

    Fail-closed на SSL-ошибке при `verify=True`: считаем, что Redfish
    недоступен, и даём cascade'у попробовать следующий шаг
    (https-no-verify → http → ipmitool).
    """
    settings = get_settings()
    verify = settings.redfish_verify_tls
    if scheme == "https" and not verify:
        logger.warning(
            "Redfish probe to %s runs with verify=False (REDFISH_VERIFY_TLS=false); "
            "MITM-able. Acceptable only in dev/test.",
            host,
        )
    url = f"{scheme}://{host}{_REDFISH_PROBE_PATH}"
    try:
        client = get_bmc_probe_client(scheme=scheme, verify=verify)
        resp = await client.head(url)
    except (httpx.HTTPError, OSError) as exc:
        if scheme == "https" and verify and _is_tls_error(exc):
            # Cert не прошёл валидацию — пусть cascade попробует следующий
            # шаг (verify=False либо HTTP). Здесь возвращаем False, выбор
            # стратегии — у `_probe_redfish_cascade`.
            logger.warning(
                "Redfish probe to %s failed TLS verification (%s); "
                "will try lower-security probes in cascade.",
                host,
                exc.__class__.__name__,
            )
            return False
        logger.info("Redfish probe failed for %s scheme=%s: %s", host, scheme, exc.__class__.__name__)
        return False
    return resp.status_code in {200, 401, 403, 405}


async def _emit_tls_downgrade_audit(
    host: str, *, from_level: str, to_level: str,
) -> None:
    """Best-effort WARNING-событие о понижении уровня безопасности BMC.

    Эмитится при каждом фактическом переходе cascade'а: https-verify →
    https-no-verify, https-verify → http, https-no-verify → http. События
    идут через transactional outbox (`enqueue_audit` + commit), как и
    остальной worker-audit; если outbox упал (БД недоступна) — просто
    логируем и едем дальше, probe сам по себе не должен крэшить из-за
    audit'а.

    `host` — host[:port] BMC. Не редактируем: в audit'е оператору важно
    видеть, какой именно контроллер ответил только через http.
    """
    # Локальные импорты: модуль `clients` грузится из `main` ДО того, как
    # `repositories.task` / `db.session` готовы; держим import call-time'но.
    try:
        from src.db.session import AsyncSessionLocal
        from src.repositories import task as task_repo
    except Exception:  # noqa: BLE001 — audit-инфра не обязана быть на каждом call-site'е
        logger.debug("bmc.tls_downgrade: audit infra unavailable, host=%s", host)
        return

    payload = {
        "action": "bmc.tls_downgrade",
        "status": "success",
        "allowed": True,
        "actor_type": "service",
        "target_type": "ipmi_controller",
        "target_id": host,
        "severity": "WARNING",
        "details": {
            "host": host,
            "from": from_level,
            "to": to_level,
        },
    }
    try:
        async with AsyncSessionLocal() as session:
            await task_repo.enqueue_audit(session, task_id=None, payload=payload)
            await session.commit()
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.debug(
            "bmc.tls_downgrade audit emit failed host=%s: %s",
            host, exc.__class__.__name__,
        )


async def _probe_redfish_cascade(host: str) -> bool:
    """Каскадный probe BMC: https-verify → https-no-verify → http.

    Архитектурный порядок: сначала самый защищённый канал (TLS + verify),
    при fail'е спускаемся на следующий уровень. На каждой ступени
    отдельный HEAD-запрос, БЕЗ кеширования: следующий вызов опять начнёт
    с верхней ступени. Это сознательно — BMC может ответить иначе через
    минуту (apply сертификата, обновление firmware, network-route change),
    и кэш бы залип на прошлом ответе.

    Возвращает True если ХОТЯ БЫ один уровень увидел Redfish. False если
    все три провалились — caller уйдёт на ipmitool.

    Lowering security level каскадно логируется на WARNING и эмитит
    audit-event `bmc.tls_downgrade` (через outbox), чтобы оператор
    видел и в логах, и в audit-журнале «BMC ответил только через
    http/no-verify» — это сигнал обновить firmware или выписать cert.
    """
    settings = get_settings()
    verify = settings.redfish_verify_tls

    # 1) https + текущая verify-настройка. Самый защищённый канал, который
    # настроен в окружении: prod — verify=True, dev/staging с self-signed
    # iDRAC — verify=False (но всё равно поверх TLS).
    if await _probe_redfish(host, scheme="https"):
        return True

    # Уровень, с которого мы стартовали — нужен для audit-события
    # `bmc.tls_downgrade`. Если verify=False с самого начала, шаг 2 пропускаем,
    # и реальный переход — сразу на http (если он сработает); from-level
    # тогда `https_noverify`, потому что https-no-verify фактически и был
    # первой попыткой.
    from_level = "https_verify" if verify else "https_noverify"

    # 2) https без verify — fallback на случай self-signed cert'а. Имеет
    # смысл только если settings.verify=True (иначе шаг 1 уже это сделал).
    if verify:
        url = f"https://{host}{_REDFISH_PROBE_PATH}"
        try:
            client = get_bmc_probe_client(scheme="https", verify=False)
            resp = await client.head(url)
            if resp.status_code in {200, 401, 403, 405}:
                logger.warning(
                    "Redfish probe to %s succeeded only with verify=False "
                    "(self-signed cert?); transport will use TLS without "
                    "certificate validation.",
                    host,
                )
                await _emit_tls_downgrade_audit(
                    host, from_level="https_verify", to_level="https_noverify",
                )
                return True
        except (httpx.HTTPError, OSError) as exc:
            logger.info(
                "Redfish probe https-no-verify failed for %s: %s",
                host, exc.__class__.__name__,
            )

    # 3) Plain HTTP — legacy BMC (старые Supermicro, эмуляторы) без TLS.
    if await _probe_redfish(host, scheme="http"):
        logger.warning(
            "Redfish probe to %s succeeded only via plain HTTP; BMC has no "
            "TLS, traffic is unencrypted. Acceptable only on isolated "
            "management VLAN.",
            host,
        )
        await _emit_tls_downgrade_audit(
            host, from_level=from_level, to_level="http",
        )
        return True

    return False


async def get_bmc_client(
    host: str,
    username: str,
    password: str,
    *,
    prefer: Literal["redfish", "ipmitool"] = "redfish",
    ipmitool_port: int = 623,
    ipmitool_interface: str = "lanplus",
    kind: str | None = None,
):
    """Подбор BMC-клиента: probe Redfish, fallback на ipmitool.

    `prefer="redfish"` (default) — пробуем HEAD `/redfish/v1/`; при успехе
    возвращаем `RedfishClient`, иначе `IpmitoolClient`.

    `prefer="ipmitool"` — сразу возвращаем `IpmitoolClient`, не делая
    probe'а. Полезно, если оператор знает заранее (через `kind` в
    `ipmi_controllers`-таблице server_service), что Redfish недоступен.

    `kind` — тип BMC из `ipmi_controllers.kind`
    (`idrac`/`ilo`/`ipmi`/`redfish`). Используется для подбора Manager-id
    Redfish-пути. Если не передан — RedfishClient берёт default (iDRAC).
    """
    if prefer == "ipmitool":
        return IpmitoolClient(
            host=host,
            username=username,
            password=password,
            port=ipmitool_port,
            interface=ipmitool_interface,
        )

    if await _probe_redfish_cascade(host):
        settings = get_settings()
        # Shared transport под verify-уровень: TCP/TLS connections к одному
        # BMC переиспользуются между RedfishClient'ами одного worker'а.
        # `verify_tls` уносится в transport, RedfishClient передаёт его
        # туда же — клиенту дублировать не надо (httpx ругается на конфликт).
        transport = get_bmc_redfish_transport(verify=settings.redfish_verify_tls)
        kwargs: dict = {
            "host": host,
            "username": username,
            "password": password,
            "verify_tls": settings.redfish_verify_tls,
            "timeout": settings.redfish_timeout_seconds,
            "transport": transport,
        }
        if kind:
            manager_id = resolve_manager_id(kind)
            # Пустой '' для generic-kind (ipmi/redfish) → discovery через
            # /Managers внутри клиента; явный non-empty (idrac/ilo) — прямой path.
            kwargs["manager_id"] = manager_id
        return RedfishClient(**kwargs)

    logger.info("Redfish unavailable on %s; falling back to ipmitool", host)
    return IpmitoolClient(
        host=host,
        username=username,
        password=password,
        port=ipmitool_port,
        interface=ipmitool_interface,
    )

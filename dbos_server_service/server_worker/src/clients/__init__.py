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
        async with httpx.AsyncClient(
            verify=verify if scheme == "https" else True,
            timeout=_REDFISH_PROBE_TIMEOUT_SECONDS,
        ) as client:
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

    Lowering security level каскадно логируется на WARNING, чтобы оператор
    видел в журнале «BMC ответил только через http» — это сигнал
    обновить firmware / выписать cert.
    """
    settings = get_settings()
    verify = settings.redfish_verify_tls

    # 1) https + текущая verify-настройка. Самый защищённый канал, который
    # настроен в окружении: prod — verify=True, dev/staging с self-signed
    # iDRAC — verify=False (но всё равно поверх TLS).
    if await _probe_redfish(host, scheme="https"):
        return True

    # 2) https без verify — fallback на случай self-signed cert'а. Имеет
    # смысл только если settings.verify=True (иначе шаг 1 уже это сделал).
    if verify:
        # Временно отключим verify через локальный probe-вызов: переиспользуем
        # _probe_redfish с подменой settings — самый честный способ — сделать
        # отдельный httpx-запрос здесь, чтобы не плодить лишний state.
        url = f"https://{host}{_REDFISH_PROBE_PATH}"
        try:
            async with httpx.AsyncClient(
                verify=False,
                timeout=_REDFISH_PROBE_TIMEOUT_SECONDS,
            ) as client:
                resp = await client.head(url)
            if resp.status_code in {200, 401, 403, 405}:
                logger.warning(
                    "Redfish probe to %s succeeded only with verify=False "
                    "(self-signed cert?); transport will use TLS without "
                    "certificate validation.",
                    host,
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
        kwargs: dict = {
            "host": host,
            "username": username,
            "password": password,
            # Пробрасываем настроенные verify/timeout — без этого RedfishClient
            # подхватывал свои hard-coded дефолты (verify=False, timeout=30s),
            # и env-настройки `REDFISH_VERIFY_TLS`/`REDFISH_TIMEOUT_SECONDS`
            # не влияли на реальные запросы (probe их видел, transport — нет).
            "verify_tls": settings.redfish_verify_tls,
            "timeout": settings.redfish_timeout_seconds,
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

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
    """Быстрая проверка наличия Redfish на BMC.

    HEAD `/redfish/v1/` без auth — Redfish-сервисный корень публичен по
    спецификации DMTF и не требует креденшалов. 200/401/403 означают, что
    Redfish обслуживается; connection-refused / timeout / 404 — что нет.

    TLS-валидация управляется через `Settings.redfish_verify_tls`
    (env `REDFISH_VERIFY_TLS`), как и у полноценного `RedfishClient`.
    Default — `verify=True`: без валидации MITM может вернуть HTTP 200
    на HEAD-probe и принудить dispatcher выбрать Redfish-транспорт там,
    где реальный BMC отвечает только через ipmitool.

    `verify=False` допустим для dev/test (iDRAC out-of-box идёт с
    self-signed cert); при таком режиме поднимаем warning и продолжаем.

    Fail-closed на SSL-ошибке при `verify=True`: считаем, что Redfish
    недоступен, и даём dispatcher'у уйти на ipmitool вместо того, чтобы
    разговаривать с непроверенным эндпоинтом.
    """
    settings = get_settings()
    verify = settings.redfish_verify_tls
    if not verify:
        logger.warning(
            "Redfish probe to %s runs with verify=False (REDFISH_VERIFY_TLS=false); "
            "MITM-able. Acceptable only in dev/test.",
            host,
        )
    url = f"{scheme}://{host}{_REDFISH_PROBE_PATH}"
    try:
        async with httpx.AsyncClient(
            verify=verify,
            timeout=_REDFISH_PROBE_TIMEOUT_SECONDS,
        ) as client:
            resp = await client.head(url)
    except (httpx.HTTPError, OSError) as exc:
        if verify and _is_tls_error(exc):
            # Fail-closed: cert не прошёл валидацию — Redfish-эндпоинт
            # подозрительный, идём на ipmitool.
            logger.warning(
                "Redfish probe to %s failed TLS verification (%s); "
                "treating Redfish as unavailable, falling back to ipmitool.",
                host,
                exc.__class__.__name__,
            )
            return False
        logger.info("Redfish probe failed for %s: %s", host, exc.__class__.__name__)
        return False
    # 200 — корень доступен; 401/403/405 — Redfish есть, но требует auth
    # либо не поддерживает HEAD; 404 — Redfish отсутствует.
    return resp.status_code in {200, 401, 403, 405}


async def get_bmc_client(
    host: str,
    username: str,
    password: str,
    *,
    prefer: Literal["redfish", "ipmitool"] = "redfish",
    ipmitool_port: int = 623,
    ipmitool_interface: str = "lanplus",
    bmc_vendor: str | None = None,
):
    """Подбор BMC-клиента: probe Redfish, fallback на ipmitool.

    `prefer="redfish"` (default) — пробуем HEAD `/redfish/v1/`; при успехе
    возвращаем `RedfishClient`, иначе `IpmitoolClient`.

    `prefer="ipmitool"` — сразу возвращаем `IpmitoolClient`, не делая
    probe'а. Полезно, если оператор знает заранее (через `bmc_kind` в
    `ipmi_controllers`-таблице server_service), что Redfish недоступен.

    `bmc_vendor` — vendor BMC из `ipmi_controllers.bmc_vendor`
    (`idrac`/`ilo`/`ipmi_generic`). Используется для подбора Manager-id
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

    if await _probe_redfish(host):
        kwargs: dict = {"host": host, "username": username, "password": password}
        if bmc_vendor:
            manager_id = resolve_manager_id(bmc_vendor)
            # Пустой '' для ipmi_generic → discovery через /Managers
            # внутри клиента; явный non-empty (idrac/ilo) — прямой path.
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

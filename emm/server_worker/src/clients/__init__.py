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

import ipaddress
import logging
import socket
import ssl
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Literal, NamedTuple

import httpx

from src.clients.ipmitool import IpmitoolClient, IpmitoolError
from src.clients.redfish import (
    RedfishClient,
    resolve_manager_id,
    resolve_system_id,
)
from src.core.config import get_settings
from src.core.exceptions import AppException
from src.services.http_pool import (
    get_bmc_probe_client,
    get_bmc_redfish_transport,
)

logger = logging.getLogger(__name__)

__all__ = [
    "BmcEndpointBlockedError",
    "IpmitoolClient",
    "IpmitoolError",
    "RedfishClient",
    "ensure_bmc_host_allowed",
    "get_bmc_client",
    "tls_downgrade_audit_dedup",
]


class BmcEndpointBlockedError(AppException):
    """BMC endpoint указывает на запрещённый IP (loopback/link-local).

    Поднимается до probe'а Redfish/ipmitool: hostname резолвится в IP,
    и если попадает в локальный диапазон (`127.0.0.0/8`, `169.254.0.0/16`,
    `::1`, `fe80::/10`) — операция отбивается до сетевого вызова.
    Защита от SSRF: server_service может прислать любой `endpoint_url`,
    включая `169.254.169.254` (cloud-metadata) или `localhost`, и
    worker не должен туда ходить.
    """


# Task-локальный set уже-эмитированных `bmc.tls_downgrade` событий.
# Ключ — `(host, from_level, to_level)`. Нужен handler'ам, которые в рамках
# одного task'а зовут `_get_bmc` несколько раз (например, ipmi_rotate_password:
# apply + verify + verify-retry). Без дедупа BMC с self-signed cert'ом давал бы
# 3+ одинаковых `bmc.tls_downgrade` row'ы за одну ротацию — лишний шум в SIEM
# и cardinality, поднятая на ровном месте. Handler входит в
# `tls_downgrade_audit_dedup()` context — пока он активен, повторные эмиссии
# для тех же `(host, from, to)` становятся no-op. Probe сам по себе всё ещё
# стучится на каждом вызове (no-cache инвариант для BMC), кэшируется только
# эмиссия audit'а.
_tls_downgrade_emitted: "ContextVar[set[tuple[str, str, str]] | None]" = ContextVar(
    "_tls_downgrade_emitted", default=None,
)


@contextmanager
def tls_downgrade_audit_dedup():
    """Дедуп `bmc.tls_downgrade` audit-events в рамках своего scope'а.

    Используется handler'ами, которые могут несколько раз поднимать клиент
    к тому же BMC за один task (ipmi_rotate_password apply + verify-loop).
    Первая встреча `(host, from, to)` эмитит audit как обычно, повторные —
    тихо пропускаются. Выход из контекста очищает дедуп-set, следующий task
    стартует с пустого.
    """
    token = _tls_downgrade_emitted.set(set())
    try:
        yield
    finally:
        _tls_downgrade_emitted.reset(token)


_REDFISH_PROBE_PATH = "/redfish/v1/"
_REDFISH_PROBE_TIMEOUT_SECONDS = 1.5


def _strip_host_port(host: str) -> str:
    """Отделить host от опционального port в строке вида `host` / `host:port` /
    `[v6]` / `[v6]:port`.

    Возвращает чистый host без квадратных скобок (готов для `ipaddress` и
    `getaddrinfo`). Пустая строка → пустая строка.
    """
    s = host.strip()
    if not s:
        return ""
    if s.startswith("["):
        end = s.find("]")
        if end != -1:
            return s[1:end]
        # Битый bracket — отдадим как есть, дальше getaddrinfo упадёт.
        return s
    # IPv4 / hostname: отрезаем последний `:port`, если он один.
    # Голый IPv6 без скобок (`::1`) содержит несколько двоеточий и обработан выше.
    if s.count(":") == 1:
        return s.split(":", 1)[0]
    return s


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Запрещённые для BMC диапазоны: loopback + link-local + unspecified.

    `link_local` для IPv4 покрывает `169.254.0.0/16` (включая cloud-metadata
    `169.254.169.254`); для IPv6 — `fe80::/10`. `loopback` — `127.0.0.0/8`
    для IPv4 и `::1/128` для IPv6. `is_unspecified` блокирует `0.0.0.0` и
    `::` — на ряде платформ kernel роутит их в loopback, мимо отдельных
    ip_is_loopback-проверок, и SSRF в localhost проходит.
    """
    return ip.is_loopback or ip.is_link_local or ip.is_unspecified


def _blocked_reason(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str:
    """Категория, под которую попал IP — для `reason` в audit/exception."""
    if ip.is_loopback:
        return "loopback"
    if ip.is_unspecified:
        return "unspecified"
    return "link_local"


async def _emit_bmc_endpoint_blocked_audit(host: str, *, reason: str) -> None:
    """WARNING-event о попытке достучаться до запрещённого endpoint'а.

    Best-effort: если outbox недоступен — только лог. Идёт через тот же
    transactional outbox, что и `bmc.tls_downgrade`.
    """
    try:
        from src.db.session import AsyncSessionLocal
        from src.repositories import task as task_repo
    except Exception:  # noqa: BLE001
        logger.debug("bmc.endpoint_blocked: audit infra unavailable, host=%s", host)
        return

    payload = {
        "action": "bmc.endpoint_blocked",
        "status": "failure",
        "allowed": False,
        "actor_type": "service",
        "target_type": "ipmi_controller",
        "target_id": host,
        "severity": "WARNING",
        "details": {
            "host": host,
            "reason": reason,
        },
    }
    try:
        async with AsyncSessionLocal() as session:
            await task_repo.enqueue_audit(session, task_id=None, payload=payload)
            await session.commit()
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.debug(
            "bmc.endpoint_blocked audit emit failed host=%s: %s",
            host, exc.__class__.__name__,
        )


async def ensure_bmc_host_allowed(host: str) -> None:
    """SSRF-guard: резолвить `host` и отвергнуть, если попадает в локальный
    диапазон (loopback / link-local IPv4 и IPv6).

    `host` — то, что вернул `extract_bmc_host` (host либо host:port,
    с возможной IPv6-bracket-нотацией). Если резолв не удаётся —
    тоже отвергаем: не пытаемся работать с именами, которые мы не можем
    проверить (защита от DNS rebinding на этом уровне всё равно неполная,
    но хотя бы блокирует тривиальные мисконфиги).

    Эмитит audit `bmc.endpoint_blocked` (WARNING) и поднимает
    `BmcEndpointBlockedError(BMC_ENDPOINT_BLOCKED)`.

    Список запрещённых диапазонов фиксированный, без env-toggle: BMC
    физически живут в management-сети, локальные адреса там не нужны,
    а единственное оперативное применение — атака.
    """
    bare = _strip_host_port(host)
    if not bare:
        await _emit_bmc_endpoint_blocked_audit(host, reason="empty_host")
        raise BmcEndpointBlockedError(
            error_code="BMC_ENDPOINT_BLOCKED",
            message="empty BMC host",
            details={"host": host, "reason": "empty_host"},
        )

    # Если уже валидный IP — проверяем сразу, без DNS.
    try:
        ip_obj = ipaddress.ip_address(bare)
    except ValueError:
        ip_obj = None

    if ip_obj is not None:
        if _is_blocked_ip(ip_obj):
            reason = _blocked_reason(ip_obj)
            await _emit_bmc_endpoint_blocked_audit(host, reason=reason)
            raise BmcEndpointBlockedError(
                error_code="BMC_ENDPOINT_BLOCKED",
                message=f"BMC endpoint {bare} is in blocked range ({reason})",
                details={"host": host, "resolved": bare, "reason": reason},
            )
        return

    # Hostname: резолвим в IP, проверяем каждую запись.
    try:
        infos = socket.getaddrinfo(bare, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        await _emit_bmc_endpoint_blocked_audit(host, reason="resolve_failed")
        raise BmcEndpointBlockedError(
            error_code="BMC_ENDPOINT_BLOCKED",
            message=f"cannot resolve BMC host {bare!r}: {exc}",
            details={"host": host, "reason": "resolve_failed"},
        ) from exc

    for info in infos:
        sockaddr = info[4]
        addr = sockaddr[0]
        # IPv6 scope-id: `fe80::1%eth0` → отрезаем суффикс перед ip_address.
        if "%" in addr:
            addr = addr.split("%", 1)[0]
        try:
            resolved = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if _is_blocked_ip(resolved):
            reason = _blocked_reason(resolved)
            await _emit_bmc_endpoint_blocked_audit(host, reason=reason)
            raise BmcEndpointBlockedError(
                error_code="BMC_ENDPOINT_BLOCKED",
                message=(
                    f"BMC hostname {bare!r} resolves to blocked address "
                    f"{addr} ({reason})"
                ),
                details={
                    "host": host,
                    "resolved": addr,
                    "reason": reason,
                },
            )


class ProbeResult(NamedTuple):
    """Исход каскадного probe'а: ступень, на которой BMC ответил.

    * `reachable=False` — ни один уровень не ответил, caller уходит на ipmitool.
    * `reachable=True` — Redfish доступен; `scheme` (`https`/`http`) и
      `verify_tls` показывают, как именно достучались. RedfishClient нужно
      собрать с теми же параметрами, иначе реальные operations пойдут не туда,
      куда отвечал probe (например, BMC только http, а клиент идёт по https).
    """

    reachable: bool
    scheme: Literal["https", "http"]
    verify_tls: bool


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
    # Дедуп в рамках task-scope'а: если handler обернулся в
    # `tls_downgrade_audit_dedup()` и тот же downgrade уже эмитили —
    # повторно не пишем. SIEM/operator увидят одно событие на ротацию,
    # а не по одному на apply/verify/verify-retry.
    emitted = _tls_downgrade_emitted.get()
    if emitted is not None:
        key = (host, from_level, to_level)
        if key in emitted:
            return
        emitted.add(key)

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


async def _probe_redfish_cascade(host: str) -> ProbeResult:
    """Каскадный probe BMC: https-verify → https-no-verify → http.

    Архитектурный порядок: сначала самый защищённый канал (TLS + verify),
    при fail'е спускаемся на следующий уровень. На каждой ступени
    отдельный HEAD-запрос, БЕЗ кеширования: следующий вызов опять начнёт
    с верхней ступени. Это сознательно — BMC может ответить иначе через
    минуту (apply сертификата, обновление firmware, network-route change),
    и кэш бы залип на прошлом ответе.

    Возвращает `ProbeResult(reachable, scheme, verify_tls)`: caller строит
    RedfishClient с тем же scheme/verify, на котором отозвался BMC. Если бы
    мы возвращали голый bool, клиент уходил бы на default `https + verify`
    даже когда BMC отвечает только через http (или только без verify) —
    реальные операции упали бы на TLS/connect, а ipmitool fallback не
    сработал бы (probe уже сказал «доступен»).

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
        return ProbeResult(reachable=True, scheme="https", verify_tls=verify)

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
                return ProbeResult(reachable=True, scheme="https", verify_tls=False)
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
        return ProbeResult(reachable=True, scheme="http", verify_tls=True)

    return ProbeResult(reachable=False, scheme="https", verify_tls=verify)


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
    (`idrac`/`ilo`/`ipmi`/`redfish`). Используется для подбора Manager-id и
    System-id Redfish-пути (у iDRAC система лежит под `System.Embedded.1`, у
    iLO — под `1`). Если не передан — RedfishClient берёт default (`1`),
    подходящий большинству one-node iLO/generic-серверов.

    До любого сетевого вызова `host` проходит SSRF-guard
    (`ensure_bmc_host_allowed`): резолв в IP + hard-block loopback /
    link-local. Это применяется к обоим транспортам (redfish и ipmitool):
    оператор всё равно не должен иметь возможность направить worker в
    `127.0.0.1` или `169.254.169.254`.
    """
    await ensure_bmc_host_allowed(host)
    if prefer == "ipmitool":
        return IpmitoolClient(
            host=host,
            username=username,
            password=password,
            port=ipmitool_port,
            interface=ipmitool_interface,
        )

    probe = await _probe_redfish_cascade(host)
    if probe.reachable:
        settings = get_settings()
        # Берём scheme/verify ровно с той ступени, на которой BMC ответил
        # probe'у. Иначе клиент уходил бы на settings.redfish_verify_tls
        # дефолт и реальные операции пошли бы не туда: BMC ответил по http —
        # клиент стучится в https; BMC ответил без verify — клиент валидирует
        # cert и падает на TLS. Transport разделён по verify-уровню (https-
        # verify, https-no-verify), для http transport не нужен (httpx
        # сам поднимет plain-HTTP-pool на standalone-клиенте).
        host_with_scheme = f"{probe.scheme}://{host}"
        kwargs: dict = {
            "host": host_with_scheme,
            "username": username,
            "password": password,
            "verify_tls": probe.verify_tls,
            "timeout": settings.redfish_timeout_seconds,
        }
        if probe.scheme == "https":
            kwargs["transport"] = get_bmc_redfish_transport(verify=probe.verify_tls)
        if kind:
            # Пустой '' для generic-kind (ipmi/redfish) или неизвестного kind
            # → discovery через коллекцию внутри клиента; явный non-empty
            # (idrac/ilo) — прямой path. Manager-id и System-id резолвятся
            # симметрично: у iDRAC система лежит под `System.Embedded.1`, а не
            # под `1`, иначе power-операции отдают 404.
            kwargs["manager_id"] = resolve_manager_id(kind)
            kwargs["system_id"] = resolve_system_id(kind)
        return RedfishClient(**kwargs)

    logger.info("Redfish unavailable on %s; falling back to ipmitool", host)
    return IpmitoolClient(
        host=host,
        username=username,
        password=password,
        port=ipmitool_port,
        interface=ipmitool_interface,
    )

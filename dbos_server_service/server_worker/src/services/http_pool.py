"""Долгоживущие httpx.AsyncClient'ы под исходящие worker-каналы.

Раньше каждый `audit_client.emit` и каждый `server_service_client.*` зов
создавал свежий `httpx.AsyncClient` через `async with`. Под нагрузкой это
давало TCP/TLS handshake на каждый call, лишний расход FD и плавающую
латентность. Здесь — пулы под три горячих исходящих канала: loging_service,
server_service и BMC (Redfish-transport + scheme probe). Поднимаются лениво,
закрываются на WORKER_SHUTDOWN.

Контракт:

  * `get_audit_client()` — пул под emit'ы в loging_service.
  * `get_server_service_client()` — пул под internal-callback'и server_service.
  * `get_bmc_probe_client(scheme, verify)` — пул под HEAD `/redfish/v1/` probe.
    Три комбинации `(scheme, verify)`: `("https", True)`, `("https", False)`,
    `("http", True)` — клиенты переиспользуются между host'ами, потому что
    probe stateless и не несёт auth.
  * `get_bmc_redfish_transport(verify)` — shared httpx-транспорт под
    per-host `RedfishClient`. Сам клиент остаётся per-host (свой
    `base_url` и `auth=`), но connection pool — общий через transport,
    разделённый по `verify`. Два транспорта: `verify=True`/`verify=False`.
  * `aclose_all()` — закрывает все пулы, вызывается из `WORKER_SHUTDOWN`.
    После закрытия следующий `get_*` снова создаст новый клиент (нужно для
    тестового цикла «startup → shutdown → startup»).
  * `reset_for_tests()` — синхронный сброс кеш-слотов без `aclose`; тесты,
    которые подменяют `httpx.AsyncClient` через monkeypatch, должны звать
    его в фикстуре, иначе закешированный экземпляр переживёт patch.

Тесты, которым нужно перехватить отдельный запрос, могут патчить
`get_audit_client` / `get_server_service_client` / `get_bmc_probe_client`
напрямую — это проще, чем вязаться к httpx-конструктору.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

from src.core.config import get_settings
from src.utils.redaction import redact_error_message

logger = logging.getLogger(__name__)

_audit_client: Optional[httpx.AsyncClient] = None
_server_service_client: Optional[httpx.AsyncClient] = None
# (scheme, verify) → клиент под HEAD-probe. Три валидные комбинации:
# ('https', True), ('https', False), ('http', True). 'http' игнорирует verify.
_bmc_probe_clients: dict[tuple[str, bool], httpx.AsyncClient] = {}
# verify (bool) → shared AsyncHTTPTransport. Используется per-host
# RedfishClient'ами как общий connection pool — auth/base_url остаются
# у клиента, сокеты переиспользуются между BMC.
_bmc_redfish_transports: dict[bool, httpx.AsyncHTTPTransport] = {}


def _build_client(*, max_connections: int, max_keepalive: int, timeout: float) -> httpx.AsyncClient:
    """Собрать httpx.AsyncClient с пулом и общим таймаутом.

    `Limits.max_connections` ограничивает total concurrent sockets,
    `max_keepalive_connections` — сколько idle держать для reuse.
    Таймаут общий для connect+read+write — symmetрично per-call
    варианту, который тут заменяется.
    """
    return httpx.AsyncClient(
        limits=httpx.Limits(
            max_connections=max_connections,
            max_keepalive_connections=max_keepalive,
        ),
        timeout=timeout,
    )


def get_audit_client() -> httpx.AsyncClient:
    """Вернуть пул под loging_service ingest.

    Лениво создаётся на первом вызове. Размер пула — из
    `AUDIT_POOL_MAX_CONNECTIONS` / `AUDIT_POOL_MAX_KEEPALIVE_CONNECTIONS`.
    Таймаут — `AUDIT_REQUEST_TIMEOUT_SECONDS` (default 5.0): emit'ы
    короткие, ingest должен отвечать быстро.
    """
    global _audit_client
    if _audit_client is None or _audit_client.is_closed:
        settings = get_settings()
        _audit_client = _build_client(
            max_connections=settings.audit_pool_max_connections,
            max_keepalive=settings.audit_pool_max_keepalive_connections,
            timeout=settings.audit_request_timeout_seconds,
        )
    return _audit_client


def get_server_service_client() -> httpx.AsyncClient:
    """Вернуть пул под server_service internal-эндпоинты.

    Лениво создаётся на первом вызове. Размер — из
    `SERVER_SERVICE_POOL_MAX_CONNECTIONS` /
    `SERVER_SERVICE_POOL_MAX_KEEPALIVE_CONNECTIONS`.
    Таймаут — `SERVER_SERVICE_REQUEST_TIMEOUT_SECONDS` (default 15.0):
    internal-callback'и тянут крипто-операции, batch'ам нужно больше
    окна чем audit-emit'у.
    """
    global _server_service_client
    if _server_service_client is None or _server_service_client.is_closed:
        settings = get_settings()
        _server_service_client = _build_client(
            max_connections=settings.server_service_pool_max_connections,
            max_keepalive=settings.server_service_pool_max_keepalive_connections,
            timeout=settings.server_service_request_timeout_seconds,
        )
    return _server_service_client


def get_bmc_probe_client(*, scheme: str, verify: bool) -> httpx.AsyncClient:
    """Вернуть pooled HEAD-probe-клиент по `(scheme, verify)`.

    Probe — HEAD `/redfish/v1/` без auth, поэтому можно держать один клиент
    на все BMC: меняется только URL запроса, не клиент. Три валидные
    комбинации:

      * `('https', True)`  — TLS с verify (prod / внутренний CA).
      * `('https', False)` — TLS без verify (dev / self-signed iDRAC).
      * `('http',  True)`  — plain HTTP; `verify` для http no-op, оставлен
        для единообразия ключа (см. ниже).

    `('http', False)` склеивается в `('http', True)` — для plain-HTTP
    флаг verify ничего не значит, отдельный клиент держать смысла нет.

    Таймаут — короткий `_REDFISH_PROBE_TIMEOUT_SECONDS` (см. caller'а
    в `src/clients/__init__.py`), здесь задаём через settings, чтобы пул
    не зависел от import-order.
    """
    if scheme not in {"http", "https"}:
        raise ValueError(f"unsupported probe scheme: {scheme!r}")
    # Нормализуем ключ: для http verify ни на что не влияет.
    key_verify = verify if scheme == "https" else True
    key = (scheme, key_verify)
    client = _bmc_probe_clients.get(key)
    if client is None or client.is_closed:
        settings = get_settings()
        client = httpx.AsyncClient(
            verify=key_verify if scheme == "https" else True,
            timeout=settings.bmc_probe_timeout_seconds,
            limits=httpx.Limits(
                max_connections=settings.bmc_pool_max_connections,
                max_keepalive_connections=settings.bmc_pool_max_keepalive_connections,
            ),
        )
        _bmc_probe_clients[key] = client
    return client


def get_bmc_redfish_transport(*, verify: bool) -> httpx.AsyncHTTPTransport:
    """Вернуть shared httpx-transport под per-host `RedfishClient`.

    Два транспорта: `verify=True` / `verify=False`. RedfishClient остаётся
    per-host (свой `base_url`, свой `auth=(u, p)`), но connection pool —
    общий: при работе с пачкой BMC за один тик worker'а сокеты до одного и
    того же контроллера держатся открытыми параллельно.

    Keep-alive (переиспользование idle-соединений) намеренно выключен:
    `max_keepalive_connections=0`. HPE iLO5 закрывает переиспользованное
    соединение на своей стороне, и каждый второй запрос по тому же сокету
    падает `httpx.RemoteProtocolError` («server disconnected without sending
    a response»). Цепочка power_status → action → power_status по одному
    клиенту из-за этого интермиттентно рвалась. С отключённым keep-alive
    каждый запрос идёт по свежему соединению — handshake-overhead приемлем
    (power-операции редкие и не batch'евые), а интермиттентные обрывы
    пропадают. `max_connections` оставляем под env, чтобы параллельная
    работа с большим стендом упиралась в осознанный лимит, а не в дефолт.
    """
    transport = _bmc_redfish_transports.get(verify)
    if transport is None:
        settings = get_settings()
        transport = httpx.AsyncHTTPTransport(
            verify=verify,
            limits=httpx.Limits(
                max_connections=settings.bmc_pool_max_connections,
                max_keepalive_connections=0,
            ),
        )
        _bmc_redfish_transports[verify] = transport
    return transport


async def aclose_all() -> None:
    """Закрыть все пулы на graceful shutdown.

    Идемпотентно — повторный вызов проходит без ошибок. После закрытия
    очищаем слоты, чтобы следующий `get_*` создал свежий клиент (это нужно
    для test-цикла и для случая, когда worker внутри одного процесса
    рестартует broker).

    `CancelledError` на первом `aclose()` не должен оставлять остальные
    слоты неосвобождёнными — ловим как warning и продолжаем; в конце,
    если cancel был, переотправляем после полного прохода. Без этого
    SIGTERM посреди `aclose_all` приводил бы к утечке socket'ов остальных
    клиентов (drain не успевал бы их закрыть).
    """
    import asyncio as _asyncio

    cancelled = False

    def _is_cancelled(exc: BaseException) -> bool:
        return isinstance(exc, _asyncio.CancelledError)

    global _audit_client, _server_service_client
    for slot_name in ("audit", "server_service"):
        client = _audit_client if slot_name == "audit" else _server_service_client
        if client is None:
            continue
        try:
            await client.aclose()
        except BaseException as exc:  # noqa: BLE001 — shutdown best-effort
            if _is_cancelled(exc):
                cancelled = True
                logger.warning(
                    "http_pool: cancelled while closing %s pool — continuing drain",
                    slot_name,
                )
            else:
                logger.warning(
                    "http_pool: failed to close %s pool: %s",
                    slot_name,
                    redact_error_message(f"{type(exc).__name__}: {exc}"),
                )
    _audit_client = None
    _server_service_client = None

    # BMC probe clients
    for key, client in list(_bmc_probe_clients.items()):
        try:
            await client.aclose()
        except BaseException as exc:  # noqa: BLE001 — shutdown best-effort
            if _is_cancelled(exc):
                cancelled = True
                logger.warning(
                    "http_pool: cancelled while closing bmc probe pool %s — continuing drain",
                    key,
                )
            else:
                logger.warning(
                    "http_pool: failed to close bmc probe pool %s: %s",
                    key,
                    redact_error_message(f"{type(exc).__name__}: {exc}"),
                )
    _bmc_probe_clients.clear()

    # BMC redfish transports
    for verify, transport in list(_bmc_redfish_transports.items()):
        try:
            await transport.aclose()
        except BaseException as exc:  # noqa: BLE001
            if _is_cancelled(exc):
                cancelled = True
                logger.warning(
                    "http_pool: cancelled while closing bmc redfish transport verify=%s — continuing drain",
                    verify,
                )
            else:
                logger.warning(
                    "http_pool: failed to close bmc redfish transport verify=%s: %s",
                    verify,
                    redact_error_message(f"{type(exc).__name__}: {exc}"),
                )
    _bmc_redfish_transports.clear()

    if cancelled:
        raise _asyncio.CancelledError(
            "http_pool.aclose_all: cancelled mid-drain; all slots flushed"
        )


def reset_for_tests() -> None:
    """Сброс закешированных клиентов без `aclose`.

    Нужно, когда тест подменяет `httpx.AsyncClient` через monkeypatch:
    закешированный реальный клиент пережил бы patch и продолжил ходить в
    сеть. Синхронный — чтобы вызывать из обычной pytest-фикстуры без
    `pytest.mark.asyncio`.
    """
    global _audit_client, _server_service_client
    _audit_client = None
    _server_service_client = None
    _bmc_probe_clients.clear()
    _bmc_redfish_transports.clear()

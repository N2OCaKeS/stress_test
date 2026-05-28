"""Долгоживущие httpx.AsyncClient'ы под исходящие worker-каналы.

Раньше каждый `audit_client.emit` и каждый `server_service_client.*` зов
создавал свежий `httpx.AsyncClient` через `async with`. Под нагрузкой это
давало TCP/TLS handshake на каждый call, лишний расход FD и плавающую
латентность. Здесь — два пула (loging_service и server_service) с keepalive,
поднимаются лениво и закрываются на WORKER_SHUTDOWN.

Контракт:

  * `get_audit_client()` — пул под emit'ы в loging_service.
  * `get_server_service_client()` — пул под internal-callback'и server_service.
  * `aclose_all()` — закрывает оба, вызывается из `WORKER_SHUTDOWN`. После
    закрытия следующий `get_*` снова создаст новый клиент (нужно для
    тестового цикла «startup → shutdown → startup»).
  * `reset_for_tests()` — синхронный сброс кеш-слотов без `aclose`; тесты,
    которые подменяют `httpx.AsyncClient` через monkeypatch, должны звать
    его в фикстуре, иначе закешированный экземпляр переживёт patch.

Тесты, которым нужно перехватить отдельный запрос, могут патчить
`get_audit_client` / `get_server_service_client` напрямую — это проще, чем
вязаться к httpx-конструктору.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

from src.core.config import get_settings

logger = logging.getLogger(__name__)

_audit_client: Optional[httpx.AsyncClient] = None
_server_service_client: Optional[httpx.AsyncClient] = None


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
    """
    global _audit_client
    if _audit_client is None or _audit_client.is_closed:
        settings = get_settings()
        _audit_client = _build_client(
            max_connections=settings.audit_pool_max_connections,
            max_keepalive=settings.audit_pool_max_keepalive_connections,
            timeout=settings.http_request_timeout_seconds,
        )
    return _audit_client


def get_server_service_client() -> httpx.AsyncClient:
    """Вернуть пул под server_service internal-эндпоинты.

    Лениво создаётся на первом вызове. Размер — из
    `SERVER_SERVICE_POOL_MAX_CONNECTIONS` /
    `SERVER_SERVICE_POOL_MAX_KEEPALIVE_CONNECTIONS`.
    """
    global _server_service_client
    if _server_service_client is None or _server_service_client.is_closed:
        settings = get_settings()
        _server_service_client = _build_client(
            max_connections=settings.server_service_pool_max_connections,
            max_keepalive=settings.server_service_pool_max_keepalive_connections,
            timeout=settings.http_request_timeout_seconds,
        )
    return _server_service_client


async def aclose_all() -> None:
    """Закрыть оба пула на graceful shutdown.

    Идемпотентно — повторный вызов проходит без ошибок. После закрытия
    очищаем слот, чтобы следующий get_* создал свежий клиент (это нужно
    для test-цикла и для случая, когда worker внутри одного процесса
    рестартует broker).
    """
    global _audit_client, _server_service_client
    for slot_name in ("audit", "server_service"):
        client = _audit_client if slot_name == "audit" else _server_service_client
        if client is None:
            continue
        try:
            await client.aclose()
        except Exception as exc:  # noqa: BLE001 — shutdown best-effort
            logger.warning(
                "http_pool: failed to close %s pool: %s: %s",
                slot_name,
                type(exc).__name__,
                exc,
            )
    _audit_client = None
    _server_service_client = None


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

"""Долгоживущие httpx.AsyncClient'ы под исходящие каналы server_service.

server_service держит несколько pooled outbound-каналов, и каждый —
со своим module-level slot'ом в своём же модуле:

  * `dependencies/auth.py::_introspect_client` — POST в auth_service/introspect.
  * `services/audit_service.py::_audit_client` — POST в loging_service/events.
  * `core/http_clients.py::loging_read_client` — GET в loging_service для
    drift-summary / dashboard read'ов (отдельный пул от write-канала).
  * `services/acs_client.py::_acs_client` — вызовы в ACS (снимки дисков
    физических серверов). Без фиксированного `base_url` — адрес динамический,
    полный URL идёт per-call.

Здесь — единая точка для подъёма/закрытия и читалок (`get_*_client`).
Сами module-level slot'ы оставлены как source of truth — callers в
`dependencies/auth.py` / `services/audit_service.py` продолжают читать их
напрямую, а тесты продолжают monkeypatch'ить их по имени. Этот модуль
просто перестаёт дублировать конструирование клиента в lifespan.

Контракт:

  * `init_pools(settings)` — собрать клиенты и положить в их slot'ы.
    Идемпотентно: повторный вызов с уже живыми клиентами не пересоздаёт
    их. Если `logging_service_url` пуст — audit и loging-read остаются
    `None` (per-call fallback в caller'ах).
  * `get_audit_client()` / `get_introspect_client()` / `get_loging_read_client()`
    — вернуть текущий клиент из slot'а (или `None`, если lifespan не
    поднимал). Caller сам решает, что делать с None — у audit / introspect
    есть fallback на per-call client, у loging-read — нет (там читалка
    под одного caller'а, не критичный путь).
  * `aclose_all()` — закрыть все клиенты, обнулить slot'ы. Idempotent.
    Не дёргает audit-drain — это контракт lifespan'а в `main.py`, он
    знает про `_pending_audit_tasks`. Здесь только закрытие сокетов.
  * `reset_for_tests()` — синхронный сброс без `aclose`. Нужен тестам,
    которые подменяют `httpx.AsyncClient` через monkeypatch.

Зеркалит `server_worker/src/services/http_pool.py` по форме API.
"""

from __future__ import annotations

import logging

import httpx

from src.core import http_clients
from src.dependencies import auth as auth_deps
from src.services import acs_client, audit_service

logger = logging.getLogger(__name__)


def init_pools(settings) -> None:
    """Поднять pooled httpx-клиенты под все исходящие каналы.

    Вызывается из FastAPI `lifespan` на startup. Идемпотентно — повторный
    вызов оставит уже живых клиентов как есть; нужно для тестов, которые
    стартуют lifespan несколько раз через `app.router.lifespan_context`.
    """
    if auth_deps._introspect_client is None:
        auth_deps._introspect_client = httpx.AsyncClient(
            base_url=settings.auth_service_url.rstrip("/"),
            timeout=settings.auth_request_timeout_seconds,
            limits=httpx.Limits(
                max_connections=settings.introspect_pool_max_connections,
                max_keepalive_connections=settings.introspect_pool_max_keepalive,
            ),
        )

    logging_url = (settings.logging_service_url or "").rstrip("/")
    if logging_url:
        if audit_service._audit_client is None:
            audit_service._audit_client = httpx.AsyncClient(
                base_url=logging_url,
                timeout=settings.audit_pool_timeout_seconds,
                limits=httpx.Limits(
                    max_connections=settings.audit_pool_max_connections,
                    max_keepalive_connections=settings.audit_pool_max_keepalive,
                ),
            )
        if http_clients.loging_read_client is None:
            # Отдельный пул под read-канал к loging (drift-агрегация в
            # `GET /servers/{id}/drift`). Read и write держим раздельно,
            # чтобы дашборд-burst не выедал FD у audit-emit канала.
            http_clients.loging_read_client = httpx.AsyncClient(
                base_url=logging_url,
                timeout=settings.loging_read_timeout_seconds,
                limits=httpx.Limits(
                    max_connections=settings.loging_read_pool_max_connections,
                    max_keepalive_connections=settings.loging_read_pool_max_keepalive,
                ),
            )

    if acs_client._acs_client is None:
        # Без `base_url` — ACS-адрес динамический (хранится в `AcsSettings`,
        # меняется через `/settings/acs` без рестарта), полный URL передаётся
        # per-call. Пул нужен только ради connection reuse/лимитов.
        acs_client._acs_client = httpx.AsyncClient(
            timeout=settings.acs_request_timeout_seconds,
            limits=httpx.Limits(
                max_connections=settings.acs_pool_max_connections,
                max_keepalive_connections=settings.acs_pool_max_keepalive,
            ),
        )


def get_audit_client() -> httpx.AsyncClient | None:
    """Текущий pooled клиент под audit-emit в loging_service.

    Может вернуть None outside lifespan (ad-hoc тесты). Caller в
    `audit_service._send_to_logging_service` уходит на per-call fallback.
    """
    return audit_service._audit_client


def get_introspect_client() -> httpx.AsyncClient | None:
    """Текущий pooled клиент под introspect в auth_service.

    Может вернуть None outside lifespan. Caller в
    `dependencies/auth._introspect` уходит на per-call fallback.
    """
    return auth_deps._introspect_client


def get_loging_read_client() -> httpx.AsyncClient | None:
    """Текущий pooled клиент под read-канал в loging_service."""
    return http_clients.loging_read_client


def get_acs_client() -> httpx.AsyncClient | None:
    """Текущий pooled клиент под вызовы в ACS. Может вернуть None outside lifespan."""
    return acs_client._acs_client


async def aclose_all() -> None:
    """Закрыть все pooled-клиенты и обнулить slot'ы. Idempotent.

    Порядок: introspect → audit → loging-read. Drain pending audit-task'ов
    делает lifespan ДО вызова этого функции (audit_pool остаётся жив, пока
    in-flight task'и не доехали до сети) — здесь только закрытие сокетов.
    """
    introspect = auth_deps._introspect_client
    auth_deps._introspect_client = None
    if introspect is not None:
        try:
            await introspect.aclose()
        except Exception as exc:  # noqa: BLE001 — shutdown best-effort
            logger.warning("http_pool: failed to close introspect pool: %s", exc)

    audit_pool = audit_service._audit_client
    audit_service._audit_client = None
    if audit_pool is not None:
        try:
            await audit_pool.aclose()
        except Exception as exc:  # noqa: BLE001
            logger.warning("http_pool: failed to close audit pool: %s", exc)

    loging_read = http_clients.loging_read_client
    http_clients.loging_read_client = None
    if loging_read is not None:
        try:
            await loging_read.aclose()
        except Exception as exc:  # noqa: BLE001
            logger.warning("http_pool: failed to close loging-read pool: %s", exc)

    acs_pool = acs_client._acs_client
    acs_client._acs_client = None
    if acs_pool is not None:
        try:
            await acs_pool.aclose()
        except Exception as exc:  # noqa: BLE001
            logger.warning("http_pool: failed to close ACS pool: %s", exc)


def reset_for_tests() -> None:
    """Синхронный сброс slot'ов без `aclose`.

    Нужно, когда тест подменяет `httpx.AsyncClient` через monkeypatch:
    закешированный реальный клиент пережил бы patch и пошёл бы в сеть.
    """
    auth_deps._introspect_client = None
    audit_service._audit_client = None
    http_clients.loging_read_client = None
    acs_client._acs_client = None

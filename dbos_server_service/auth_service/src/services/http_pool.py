"""Долгоживущий httpx.AsyncClient для audit-emit в loging_service.

У auth_service ровно один исходящий HTTP-канал — POST в loging_service
`/api/logging/v1/events`. Раньше клиент конструировался прямо в lifespan'е
`main.py` и складывался в `audit_service._audit_client`; здесь — единая
точка инициализации/закрытия. Slot остаётся в `audit_service._audit_client`,
чтобы `_send_to_logging_service` продолжал читать его как раньше, а тесты
не пришлось переписывать.

Контракт:

  * `init_pools(settings)` — собрать клиент и положить в `_audit_client`.
    Идемпотентно. Если `logging_service_url` не задан, клиент не создаётся
    (caller уходит в локальный logger).
  * `get_audit_client()` — текущий клиент (или None outside lifespan).
  * `aclose_all()` — закрыть и обнулить. Drain in-flight emit-тасок —
    контракт lifespan'а в `main.py` (он знает про `_EMIT_TASKS`).
  * `reset_for_tests()` — синхронный сброс slot'а.

Зеркалит `server_worker/src/services/http_pool.py` по форме API.
"""

from __future__ import annotations

import logging

import httpx

from src.services import audit_service

logger = logging.getLogger(__name__)


def init_pools(settings) -> None:
    """Поднять pooled audit-client. Идемпотентно."""
    logging_url = (getattr(settings, "logging_service_url", None) or "").rstrip("/")
    if not logging_url:
        return
    if audit_service._audit_client is not None:
        return
    # Развести таймауты по стадиям: read берёт полный budget из настроек
    # (медленный loging под нагрузкой), connect/write/pool жёстко лимитим в
    # 2с — connect/handshake/pool checkout не должны зависеть от latency
    # самого loging'а.
    read_timeout = settings.audit_pool_timeout_seconds
    audit_service._audit_client = httpx.AsyncClient(
        base_url=logging_url,
        timeout=httpx.Timeout(read_timeout, connect=2.0, write=2.0, pool=2.0),
        limits=httpx.Limits(
            max_connections=settings.audit_pool_max_connections,
            max_keepalive_connections=settings.audit_pool_max_keepalive,
        ),
    )


def get_audit_client() -> httpx.AsyncClient | None:
    """Текущий pooled клиент под audit-emit. None outside lifespan."""
    return audit_service._audit_client


async def aclose_all() -> None:
    """Закрыть audit-client и обнулить slot. Idempotent."""
    client = audit_service._audit_client
    audit_service._audit_client = None
    if client is not None:
        try:
            await client.aclose()
        except Exception as exc:  # shutdown best-effort
            logger.warning("http_pool: failed to close audit pool: %s", exc)


def reset_for_tests() -> None:
    """Синхронный сброс slot'а без `aclose`. Для monkeypatch-фикстур."""
    audit_service._audit_client = None

"""Read-only клиент loging_service для drift-агрегации.

Используется `GET /servers/{id}/drift` — собирает событие
`server_account.drift_detected` из loging и отдаёт сводку. Запросы идут
через ту же `LOGING_SERVICE_API_KEY` shared-secret, что и POST events,
и тем же `X-Service-Identity: server_service` header'ом — на internal
read-канал loging (`/internal/events`), который аутентифицирует
service-to-service identity ровно как write-канал. Публичный `GET /events`
требует user-bearer с ролью `loging_admin`/`loging_reader` и сюда не
подходит.

Если loging недоступен или API-key пуст — поднимаем `ServiceUnavailableError`,
endpoint отдаст 503 (читать историю без upstream нельзя).

### Connection pool

`httpx.AsyncClient` — module-level (`core.http_clients.loging_read_client`),
поднимается в `main.lifespan` startup и закрывается в shutdown. До перехода
на пул каждый `GET /servers/{id}/drift` открывал свежий TCP+TLS до
loging_service — на дашборде с N серверами это N handshake'ов на refresh.
Outside the app lifecycle (unit-тесты до lifespan startup) — fall back to
per-call client.
"""

from __future__ import annotations

import logging
from datetime import datetime

import httpx

from src.core import http_clients
from src.core.config import get_settings
from src.core.exceptions import ServiceUnavailableError
from src.core.http import bearer_header

logger = logging.getLogger("server_service.loging_client")

# Cap страницы при GET /events. loging валидирует `limit ≤ _MAX_LIMIT` сам,
# у нас тут хватает на сутки drift'ов на крупном сервере (event'ы — INFO/WARNING,
# но при шумной инвентаризации могут идти десятками за каждый прогон).
_DRIFT_PAGE_LIMIT = 1000


async def fetch_drift_events(
    *,
    server_id: str,
    since: datetime,
) -> tuple[list[dict], bool]:
    """Запросить у loging события `server_account.drift_detected` для сервера.

    Возвращает `(events, truncated)`:

    * `events` — список raw event-dict'ов (то, что отдаст loging).
    * `truncated` — True, если loging вернул ровно `_DRIFT_PAGE_LIMIT` записей;
      возможны более старые drift'ы за окном, клиент должен сузить `since`.

    Фильтр по `target_id == server_id` уходит на сторону loging
    (internal `/internal/events` поддерживает query-параметр `target_id`),
    плюс дублируется локально после fetch'а как защита от случайного
    расширения выборки.

    Сетевые сбои → `ServiceUnavailableError(LOGING_SERVICE_UNAVAILABLE)`.
    """
    settings = get_settings()
    base = (settings.logging_service_url or "").rstrip("/")
    api_key = settings.logging_service_api_key
    if not base or not api_key:
        raise ServiceUnavailableError(
            error_code="LOGING_SERVICE_NOT_CONFIGURED",
            message="loging_service URL or API key is not configured",
        )

    events_path = "/api/logging/v1/internal/events"
    params = {
        "action": "server_account.drift_detected",
        "target_id": server_id,
        "from_time": since.isoformat(),
        "limit": _DRIFT_PAGE_LIMIT,
        "include_total": False,
    }
    headers = {**bearer_header(api_key), "X-Service-Identity": "server_service"}

    pooled = http_clients.loging_read_client
    try:
        if pooled is not None:
            # Pooled путь: base_url уже на клиенте, дёргаем relative path.
            resp = await pooled.get(events_path, params=params, headers=headers)
        else:
            # Lifespan ещё не поднялся (unit-тест без TestClient) —
            # эфемерный AsyncClient ровно на один GET. Производственный
            # путь всегда идёт через пул. Limits явные, чтобы тесты под
            # многопоточной нагрузкой не плодили connection'ы — fallback
            # обслуживает максимум один in-flight запрос.
            fallback_limits = httpx.Limits(max_connections=5, max_keepalive_connections=0)
            async with httpx.AsyncClient(timeout=5.0, limits=fallback_limits) as client:
                resp = await client.get(
                    f"{base}{events_path}", params=params, headers=headers,
                )
    except httpx.HTTPError as exc:
        logger.warning("loging GET /events failed: %s: %s", type(exc).__name__, exc)
        raise ServiceUnavailableError(
            error_code="LOGING_SERVICE_UNAVAILABLE",
            message="loging_service is unreachable",
        ) from exc

    if resp.status_code >= 500:
        raise ServiceUnavailableError(
            error_code="LOGING_SERVICE_UNAVAILABLE",
            message=f"loging_service returned {resp.status_code}",
        )
    if resp.status_code in (401, 403):
        # 401/403 — это operator-config error (misconfigured api-key либо
        # роль). Под общим WARNING + LOGING_SERVICE_UNAVAILABLE такой случай
        # неотличим от транзиентной 5xx-недоступности upstream'а. Поднимаем
        # уровень до ERROR и отдельный error_code, чтобы алёрт триаж'ился
        # в сторону config'а, а не в сторону health check'а loging'а.
        logger.error(
            "loging GET /events rejected as auth-failure %s: %s",
            resp.status_code, resp.text[:300],
        )
        raise ServiceUnavailableError(
            error_code="LOGING_SERVICE_AUTH_FAILED",
            message="loging_service rejected drift query: auth failed",
        )
    if resp.status_code >= 400:
        # Прочий 4xx — это контракт-mismatch (валидация / unknown action),
        # но не наш штатный путь; прячем за 503 чтобы не светить детали
        # upstream'а, оставляем WARNING.
        logger.warning(
            "loging GET /events rejected with %s: %s",
            resp.status_code, resp.text[:300],
        )
        raise ServiceUnavailableError(
            error_code="LOGING_SERVICE_UNAVAILABLE",
            message="loging_service rejected drift query",
        )

    body = resp.json() or {}
    items = body.get("items", []) or []
    # loging уже отфильтровал по target_id (query-параметр в params),
    # но повторяем локально как защиту от расширения выборки.
    filtered = [
        item for item in items
        if item.get("target_id") == server_id
    ]
    truncated = len(items) >= _DRIFT_PAGE_LIMIT
    return filtered, truncated

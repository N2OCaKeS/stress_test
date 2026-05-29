"""Read-only клиент loging_service для drift-агрегации.

Используется `GET /servers/{id}/drift` — собирает событие
`server_account.drift_detected` из loging и отдаёт сводку. Запросы идут
через ту же `LOGING_SERVICE_API_KEY` shared-secret, что и POST events;
loging принимает service-to-service identity ровно как на write-канале.

Если loging недоступен или API-key пуст — поднимаем `ServiceUnavailableError`,
endpoint отдаст 503 (читать историю без upstream нельзя).
"""

from __future__ import annotations

import logging
from datetime import datetime

import httpx

from src.core.config import get_settings
from src.core.exceptions import ServiceUnavailableError

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

    На стороне loging фильтра по `target_id` нет (см. `loging_service/api/v1/
    endpoints/events.py::list_events`), поэтому фильтруем по `target_id ==
    server_id` локально после fetch'а. Это compromise до расширения GET /events
    в loging (см. TODO).

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

    url = f"{base}/api/logging/v1/events"
    params = {
        "action": "server_account.drift_detected",
        "from_time": since.isoformat(),
        "limit": _DRIFT_PAGE_LIMIT,
        "include_total": False,
    }
    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url, params=params, headers=headers)
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
    if resp.status_code >= 400:
        # 401/403 = misconfigured api-key или роль; 4xx наружу — не наш case,
        # но прячем за 503 чтобы не светить детали upstream'а.
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
    # Локальный фильтр по target_id — loging пока не поддерживает.
    filtered = [
        item for item in items
        if item.get("target_id") == server_id
    ]
    truncated = len(items) >= _DRIFT_PAGE_LIMIT
    return filtered, truncated

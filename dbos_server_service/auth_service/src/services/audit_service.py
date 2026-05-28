"""Публикация audit-событий в loging_service.

Плотная интеграция:
- Любой `emit(action, actor_id=None, ...)` автоматически подхватывает из
  `audit_context`: username, department_id, request_id, ip_address, user_agent.
  Явно переданный параметр имеет приоритет.
- `details` всегда проходит через `redaction.redact()` — пароли, токены, секреты
  и хэши превращаются в типизированные плейсхолдеры (`<PASSWORD>`, `<TOKEN>`, …).
- Если в контексте есть ip_address/user_agent — они автоматически попадают
  в details (если уже не указаны явно).

### Connection pool

`httpx.AsyncClient` — **module-level** (`_audit_client`), управляется
FastAPI `lifespan` в `src/main.py`:

* startup → создаётся один `AsyncClient` с `base_url=logging_service_url`,
  shared timeout и bounded pool limits (`max_connections=20`,
  `max_keepalive_connections=10`);
* shutdown → `await _audit_client.aclose()`.

Почему это важно: каждый authenticated request может породить audit-emission
(login success/fail, refresh, ban, http.client_error в middleware и т.д.).
Создание `httpx.AsyncClient` per-call под slowloris-burst (или просто под
login flood) быстро исчерпает FD-пул и заставит TLS-handshake'и происходить
на каждый emit. Pooled client кладёт жёсткий потолок на исходящие подключения и амортизирует
TLS-handshake между emissions. Outside the app lifecycle (unit-тесты, импорт
модуля до lifespan startup) — fall back to per-call client.

Симметричен `server_service/src/services/audit_service.py`.
"""

import asyncio
import logging
from datetime import datetime, timezone

import httpx

from src.core.config import get_settings
from src.services import audit_context
from src.services.redaction import redact

logger = logging.getLogger("audit")

# Module-level pooled client. Инициализируется в `main.lifespan` startup,
# закрывается в shutdown. Остаётся `None` outside the app lifecycle
# (например, при ранних импортах в тестах) — `_send_to_logging_service`
# falls back to per-call client.
_audit_client: httpx.AsyncClient | None = None

_EVENTS_PATH = "/api/logging/v1/events"

# Сильные ссылки на in-flight emit-таски. `asyncio.create_task` сам по себе
# держит на task только weakref через event loop — под нагрузкой GC может
# собрать корутину до того, как она отправит payload в loging_service. Кладём
# handle сюда на время жизни, снимаем по done-callback.
_EMIT_TASKS: set[asyncio.Task] = set()


async def _send_to_logging_service(payload: dict, url: str, api_key: str) -> None:
    """Async-отправка одного payload'а в loging_service.

    Pooled path (`_audit_client is not None`) — переиспользует TCP-сессию,
    amortize'ит TLS-handshake. Fallback path (None) — per-call client, нужен
    для unit-тестов, которые импортят модуль до lifespan startup.
    Все ошибки глушим в WARNING (best-effort, audit не должен ломать main-flow).
    """
    headers = {"Authorization": f"Bearer {api_key}"}
    client = _audit_client
    try:
        if client is not None:
            # Pooled path: `base_url` уже выставлен на клиенте → relative path.
            await client.post(_EVENTS_PATH, json=payload, headers=headers)
        else:
            # Fallback path: per-call client (unit-тесты outside lifespan).
            async with httpx.AsyncClient(timeout=2.0) as fallback:
                await fallback.post(
                    f"{url.rstrip('/')}{_EVENTS_PATH}",
                    json=payload,
                    headers=headers,
                )
    except httpx.HTTPError as exc:
        logger.warning("audit_service: failed to send event: %s", exc)
        logger.info("audit_event_fallback %s", payload)
    except Exception as exc:  # noqa: BLE001 — best-effort, не должно падать в hot path
        logger.warning("audit_service: unexpected error sending event: %s", exc)
        logger.info("audit_event_fallback %s", payload)


def _enrich_details(details: dict | None) -> dict:
    """Добавить ip/user_agent из контекста в details, если их там нет."""
    ctx = audit_context.get_context()
    merged: dict = dict(details) if details else {}
    if ctx.ip_address and "ip" not in merged and "ip_address" not in merged:
        merged["ip"] = ctx.ip_address
    if ctx.user_agent and "user_agent" not in merged and "ua" not in merged:
        merged["user_agent"] = ctx.user_agent
    for k, v in ctx.extra.items():
        merged.setdefault(k, v)
    return merged


def emit(
    action: str,
    actor_id: str | None = None,
    actor_type: str | None = None,
    target_id: str | None = None,
    target_type: str | None = None,
    service: str = "auth_service",
    status: str = "success",
    allowed: bool = True,
    details: dict | None = None,
    request_id: str | None = None,
    department_id: str | None = None,
    username: str | None = None,
) -> None:
    """Эмит audit-события в loging_service (или fallback в локальный лог).

    Action именуется как `<object>.<verb>` (`user.login`, `bot.token_create`).
    Стабильные ключи — каталог в `audit_events.py`. Любой `emit` подхватывает
    контекст из `audit_context` (actor/username/request_id/ip/UA).

    `actor_type` подхватывается из `audit_context.subject_type` (его выставляет
    middleware через `get_current_identity`), если caller не задал явно.
    Раньше всё писалось как `actor_type="user"` — SIEM не отличал
    PAT/bot/oauth_client от живых юзеров.
    """
    settings = get_settings()
    ctx = audit_context.get_context()

    # Приоритет: явный параметр > контекст. Для actor_type fallback на "user"
    # если ни caller, ни identity-middleware ничего не выставили — backward-
    # compat для anonymous / health-paths / service.started.
    resolved_actor_id = actor_id if actor_id is not None else ctx.actor_id
    resolved_actor_type = actor_type if actor_type is not None else (ctx.subject_type or "user")
    resolved_username = username if username is not None else ctx.username
    resolved_department = department_id if department_id is not None else ctx.department_id
    resolved_request_id = request_id if request_id is not None else ctx.request_id

    # Маскировка details + автозаполнение ip/ua
    enriched = _enrich_details(details)
    sanitized = redact(enriched)

    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service": service,
        "action": action,
        "actor_id": resolved_actor_id,
        "actor_type": resolved_actor_type,
        "username": resolved_username,
        "department_id": resolved_department,
        "target_id": target_id,
        "target_type": target_type,
        "status": status,
        "allowed": allowed,
        "request_id": resolved_request_id,
        "details": sanitized,
    }

    logging_url = getattr(settings, "logging_service_url", None)
    api_key = getattr(settings, "logging_service_api_key", None)

    if logging_url and api_key:
        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(_send_to_logging_service(payload, logging_url, api_key))
            _EMIT_TASKS.add(task)
            task.add_done_callback(_EMIT_TASKS.discard)
        except RuntimeError:
            # Sync-контекст (worker-поток через asyncio.to_thread, скрипт без
            # loop'а, ранний import). Блокирующий httpx.post тут опасен —
            # под недоступным loging_service каждый emit подвешивает поток
            # asyncio thread pool на timeout, и FastAPI быстро затыкается.
            # Audit best-effort: лучше потерять одно событие, чем съесть пул.
            logger.warning(
                "audit_service: no running event loop, dropping http delivery "
                "(action=%s, status=%s)", action, status,
            )
            logger.info("audit_event_fallback %s", payload)
    else:
        logger.info("audit_event %s", payload)

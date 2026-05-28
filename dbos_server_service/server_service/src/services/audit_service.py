"""Публикация audit-событий в loging_service.

Плотная интеграция:
- Любой вызов `emit(action, actor_id=None, ...)` автоматически подхватывает
  из `audit_context`: username, department_id, request_id, ip_address, user_agent.
  Если параметр передан явно — он имеет приоритет.
- `details` всегда проходит через `redaction.redact()` — пароли, токены,
  секреты и хэши превращаются в типизированные плейсхолдеры
  (`<PASSWORD>`, `<TOKEN>`, …).
- При наличии в контексте ip_address/user_agent они добавляются в details
  автоматически (если уже не присутствуют).
- Best-effort: ошибки httpx не пропагируются вызывающему коду — операция
  основного запроса не должна падать из-за недоступного loging_service.

### Connection pool

`httpx.AsyncClient` — **module-level** (`_audit_client`), управляется
FastAPI `lifespan` в `src/main.py`:

* startup → создаётся один `AsyncClient` с `base_url=logging_service_url`,
  shared timeout и bounded pool limits (`max_connections=20`,
  `max_keepalive_connections=10`);
* shutdown → `await _audit_client.aclose()`.

Почему это важно: каждый authenticated request может породить
audit-emission (grant, ban, power-on, http.client_error в middleware и т.д.).
Создание `httpx.AsyncClient` per-call под slowloris-burst быстро исчерпает
FD-пул. Pooled client кладёт жёсткий потолок на исходящие подключения и
амортизирует TLS-handshake между emissions. Outside the app lifecycle
(unit-тесты, которые импортят модуль до lifespan startup) — fall back to
per-call client.
"""

import asyncio
import logging
import random
from datetime import datetime, timezone

import httpx

from src.core.config import get_settings
from src.core.constants import SERVICE_NAME as _SERVICE_NAME
from src.services import audit_context
from src.services.redaction import redact

logger = logging.getLogger("audit")

# Module-level pooled client. Инициализируется в `main.lifespan` startup,
# закрывается в shutdown. Остаётся `None` outside the app lifecycle
# (например, при ранних импортах в тестах) — `_send_to_logging_service`
# falls back to per-call client.
_audit_client: httpx.AsyncClient | None = None

# Set in-flight audit-emit task'ов. Заполняется в `emit()` через
# `loop.create_task(...)` + `add_done_callback(_pending_audit_tasks.discard)`.
# `main._drain_pending_audit_tasks()` ждёт его опустошения на shutdown'е —
# закрыть `_audit_client` пока тут есть живые task'и нельзя, иначе они
# упадут с `httpx.ClientClosedError` и event'ы потеряются.
#
# Тип — set, потому что remove'аем через done-callback по идентичности task'а.
# WeakSet не подходит — task'и могут не иметь strong-reference'ов снаружи
# (`loop.create_task` сам держит ссылку только до завершения), и WeakSet бы их
# подметал раньше времени.
_pending_audit_tasks: "set[asyncio.Task]" = set()

_EVENTS_PATH = "/api/logging/v1/events"

# Бэкоффы между попытками при 429 от loging_service. Длина списка задаёт
# число дополнительных попыток сверх первой; итого 3 попытки.
_RETRY_DELAYS_ON_429 = (0.5, 1.5)

# Сколько событий отброшено после исчерпания retry-бюджета на 429.
# Монотонный счётчик за время жизни процесса. Ненулевое значение в проде
# сигналит, что loging_service режет rate-limit'ом наши audit-emit'ы.
_audit_dropped_429: int = 0


def get_dropped_429_total() -> int:
    """Сколько audit-событий было отброшено после трёх подряд 429."""
    return _audit_dropped_429


def _reset_dropped_429_for_tests() -> None:
    """Test helper — сбросить счётчик между прогонами."""
    global _audit_dropped_429
    _audit_dropped_429 = 0


async def _post_once(
    client: httpx.AsyncClient | None,
    url: str,
    payload: dict,
    headers: dict,
) -> httpx.Response:
    """Один POST attempt. Pooled при наличии клиента, иначе per-call."""
    if client is not None:
        return await client.post(_EVENTS_PATH, json=payload, headers=headers)
    async with httpx.AsyncClient(timeout=2.0) as fallback:
        return await fallback.post(
            f"{url.rstrip('/')}{_EVENTS_PATH}",
            json=payload,
            headers=headers,
        )


async def _send_to_logging_service(payload: dict, url: str, api_key: str) -> None:
    """Async-отправка одного payload'а в loging_service. Все ошибки глушим в WARNING.

    На 429 от loging_service делаем до двух дополнительных попыток с
    exponential backoff (0.5s, 1.5s) ± jitter. После трёх подряд 429 —
    drop в WARNING + инкремент `_audit_dropped_429`. Любая транспортная
    ошибка → drop сразу (best-effort, не блокируем main-flow).
    """
    headers = {"Authorization": f"Bearer {api_key}"}
    client = _audit_client
    try:
        for attempt in range(len(_RETRY_DELAYS_ON_429) + 1):
            response = await _post_once(client, url, payload, headers)
            if response.status_code != 429:
                return
            if attempt < len(_RETRY_DELAYS_ON_429):
                delay = _RETRY_DELAYS_ON_429[attempt] * random.uniform(0.8, 1.2)
                await asyncio.sleep(delay)
        global _audit_dropped_429
        _audit_dropped_429 += 1
        logger.warning(
            "audit_service: drop after 3x429 (action=%s)", payload.get("action"),
        )
        logger.info("audit_event_fallback %s", payload)
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
    service: str = _SERVICE_NAME,
    status: str = "success",
    allowed: bool = True,
    details: dict | None = None,
    request_id: str | None = None,
    department_id: str | None = None,
    username: str | None = None,
) -> None:
    """Best-effort emit. Ловит httpx.HTTPError, не пробрасывает наружу.

    Любые не переданные параметры берутся из `audit_context` — username,
    department_id, request_id, ip_address, user_agent, subject_type.
    `actor_type` подхватывается из `audit_context.subject_type` (его
    выставляет `get_current_identity` после introspect), если caller не
    задал явно. Без этого fallback worker_bot PAT и OAuth-клиенты
    смешивались бы с человеческими действиями (всё писалось как `user`).
    Action-key — обязателен (catalogue в `audit_events.SERVICE_EVENTS`).
    """
    settings = get_settings()
    ctx = audit_context.get_context()

    # Приоритет: явный параметр > контекст. Для actor_type fallback на "user"
    # если ни caller, ни identity-middleware ничего не выставили (anonymous /
    # health / service.started lifecycle).
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

    if not (logging_url and api_key):
        logger.info("audit_event %s", payload)
        return

    try:
        loop = asyncio.get_running_loop()
        # Track для shutdown-drain'а. discard в done-callback не даёт
        # set'у расти безгранично.
        task = loop.create_task(_send_to_logging_service(payload, logging_url, api_key))
        _pending_audit_tasks.add(task)
        task.add_done_callback(_pending_audit_tasks.discard)
    except RuntimeError:
        # Sync-контекст (asyncio.to_thread в middleware / startup hook).
        try:
            httpx.post(
                f"{logging_url}/api/logging/v1/events",
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=2.0,
            )
        except httpx.HTTPError as exc:
            logger.warning("audit_service: failed to send event (sync): %s", exc)
            logger.info("audit_event_fallback %s", payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning("audit_service: unexpected error (sync): %s", exc)
            logger.info("audit_event_fallback %s", payload)

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
import secrets
from datetime import datetime, timezone

import httpx

from src.core.config import get_settings
from src.core.http import bearer_header
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

# Потолок на одновременно живущие emit-таски. Под sustained 429 от loging
# каждый emit держит коро в памяти 0.5+1.5s + jitter ≈ 2.4s до drop'а; при
# 1000 req/s login-шторме это 2400 живых tasks и связанные с ними payload'ы.
# При превышении отменяем произвольную in-flight task (set без порядка;
# конкретный кандидат — hash-зависимый) и инкрементим counter, чтобы факт
# срезки был виден через метрики.
_EMIT_TASKS_MAX = 1000
_audit_emit_tasks_overflow: int = 0


def get_emit_tasks_overflow_total() -> int:
    """Сколько emit-task'ов было отменено из-за переполнения in-flight set'а.

    Per-process snapshot для diagnostic-payload'ов и тестов. Prometheus-экспорт
    через `/metrics` не подключён — внешний scraper суммирует сам, либо счётчик
    включается в heartbeat-событие (см. server_worker `system.heartbeat`).
    """
    return _audit_emit_tasks_overflow


def _reset_emit_tasks_overflow_for_tests() -> None:
    global _audit_emit_tasks_overflow
    _audit_emit_tasks_overflow = 0

# Бэкоффы между попытками при 429 от loging_service. Длина списка задаёт
# число дополнительных попыток сверх первой; итого 3 попытки.
_RETRY_DELAYS_ON_429 = (0.5, 1.5)

# Cap на Retry-After, чтобы broken upstream не подвесил emit на минуты.
_RETRY_AFTER_CAP_SECONDS = 30.0


def _parse_retry_after_seconds(value: str | None) -> float | None:
    """Распарсить `Retry-After` (только целочисленный delta-seconds).

    HTTP-date форма (RFC 7231) намеренно игнорируется: некоторые прокси
    отдают сломанный формат, парсинг с учётом часовых поясов добавит
    отдельный класс ошибок в hot-path аудита. Cap'имся на 30s.
    """
    if not value:
        return None
    v = value.strip()
    if not v:
        return None
    try:
        seconds = float(v)
    except ValueError:
        return None
    if seconds < 0:
        return None
    return min(seconds, _RETRY_AFTER_CAP_SECONDS)

# Сколько событий отброшено после исчерпания retry-бюджета на 429.
# Монотонный счётчик за время жизни процесса. Ненулевое значение в проде
# сигналит, что loging_service режет rate-limit'ом наши audit-emit'ы.
_audit_dropped_429: int = 0


def get_dropped_429_total() -> int:
    """Сколько audit-событий было отброшено после трёх подряд 429.

    Per-process snapshot. Ненулевое значение — сигнал, что loging_service режет
    нас rate-limit'ом; внешний наблюдатель снимает счётчик через diagnostic-payload
    либо heartbeat-событие. Prometheus-эндпоинт пока не вешаем (см. ops-sweep TODO).
    """
    return _audit_dropped_429


def _reset_dropped_429_for_tests() -> None:
    """Test helper — сбросить счётчик между прогонами."""
    global _audit_dropped_429
    _audit_dropped_429 = 0


# Локальный fast-signal счётчик для critical-action'а token.refresh_reuse.
# Эмитим в audit-канал (CRITICAL), но при перегруженном/лежащем loging внешний
# SIEM-сигнал теряется; per-process counter позволяет alert'ить по ad-hoc
# diagnostic-payload'у даже при flapping'е audit-канала.
_refresh_reuse_total: int = 0


def get_refresh_reuse_total() -> int:
    """Сколько раз был обнаружен reuse refresh-token'а за жизнь процесса."""
    return _refresh_reuse_total


def incr_refresh_reuse_total() -> None:
    """Инкремент счётчика token.refresh_reuse (вызывается из auth_service.refresh)."""
    global _refresh_reuse_total
    _refresh_reuse_total += 1


def _reset_refresh_reuse_for_tests() -> None:
    global _refresh_reuse_total
    _refresh_reuse_total = 0


def _fallback_summary(payload: dict) -> dict:
    """Минимальное представление audit-payload'а для stdout-fallback'а.

    Полный sanitized payload содержит username/department_id/allowed_services/
    platform_role — даже после `redact()` это PII-плотная связка, которую SIEM
    повторно увидит из stdout-инжеста, если loging_service временно недоступен.
    Для расследования инцидента (что пытались сделать и кто) достаточно
    `action/actor_id/status` — остальные поля найдутся в DB-логах после того,
    как loging встанет.
    """
    return {
        "action": payload.get("action"),
        "actor_id": payload.get("actor_id"),
        "status": payload.get("status"),
    }


async def _post_once(
    client: httpx.AsyncClient | None,
    url: str,
    payload: dict,
    headers: dict,
) -> httpx.Response:
    """Один POST attempt. Pooled при наличии клиента, иначе per-call.

    Fallback тянет таймаут из settings (`audit_pool_timeout_seconds`), чтобы
    pooled-path и fallback-path не расходились на одинаковом канале при
    нестандартном `AUDIT_POOL_TIMEOUT_SECONDS`.
    """
    if client is not None:
        return await client.post(_EVENTS_PATH, json=payload, headers=headers)
    fallback_timeout = getattr(get_settings(), "audit_pool_timeout_seconds", 3.0)
    async with httpx.AsyncClient(timeout=fallback_timeout) as fallback:
        return await fallback.post(
            f"{url.rstrip('/')}{_EVENTS_PATH}",
            json=payload,
            headers=headers,
        )


async def _send_to_logging_service(payload: dict, url: str, api_key: str) -> None:
    """Async-отправка одного payload'а в loging_service.

    Pooled path (`_audit_client is not None`) — переиспользует TCP-сессию,
    amortize'ит TLS-handshake. Fallback path (None) — per-call client, нужен
    для unit-тестов, которые импортят модуль до lifespan startup.

    На 429 от loging_service делаем до двух дополнительных попыток с
    exponential backoff (0.5s, 1.5s) ± jitter. Если в ответе есть числовой
    `Retry-After` (delta-seconds) — берём `max(retry_after, backoff)`,
    HTTP-date форма игнорируется. После трёх подряд 429 — drop в WARNING +
    инкремент `_audit_dropped_429`. Любая транспортная ошибка → drop сразу
    (best-effort, не блокируем main-flow).
    """
    headers = bearer_header(api_key)
    client = _audit_client
    try:
        for attempt in range(len(_RETRY_DELAYS_ON_429) + 1):
            response = await _post_once(client, url, payload, headers)
            if response.status_code != 429:
                return
            if attempt < len(_RETRY_DELAYS_ON_429):
                base_delay = _RETRY_DELAYS_ON_429[attempt] * secrets.SystemRandom().uniform(0.8, 1.2)
                hinted = _parse_retry_after_seconds(response.headers.get("Retry-After"))
                delay = max(base_delay, hinted) if hinted is not None else base_delay
                await asyncio.sleep(delay)
        global _audit_dropped_429
        _audit_dropped_429 += 1
        logger.warning(
            "audit_service: drop after 3x429 (action=%s)", payload.get("action"),
        )
        logger.info("audit_event_fallback %s", _fallback_summary(payload))
    except httpx.HTTPError as exc:
        logger.warning("audit_service: failed to send event: %s", exc)
        logger.info("audit_event_fallback %s", _fallback_summary(payload))
    except Exception as exc:  # best-effort, не должно падать в hot path
        logger.warning("audit_service: unexpected error sending event: %s", exc)
        logger.info("audit_event_fallback %s", _fallback_summary(payload))


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

    # Приоритет: явный параметр > контекст. Если actor_id не резолвится
    # (anonymous endpoint / health-path / service.started без identity),
    # actor_type ставим в "anonymous" — иначе SIEM-фильтр по actor_type
    # смешает аноним с реальным user-флоу.
    resolved_actor_id = actor_id if actor_id is not None else ctx.actor_id
    if actor_type is not None:
        resolved_actor_type = actor_type
    elif ctx.subject_type:
        resolved_actor_type = ctx.subject_type
    elif resolved_actor_id is None:
        resolved_actor_type = "anonymous"
    else:
        resolved_actor_type = "user"
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
            # Cap на in-flight set. Если loging лежит и retry-задачи копятся
            # быстрее, чем drop'аются — режем произвольную in-flight task.
            # `set` не упорядочен по вставке, поэтому конкретный кандидат на
            # cancel выбирается hash-порядком, не FIFO; в overflow-сценарии
            # это приемлемо (любая backoff-зависшая task годится). Без cap'а
            # под sustained 429 set растёт неограниченно.
            if len(_EMIT_TASKS) >= _EMIT_TASKS_MAX:
                global _audit_emit_tasks_overflow
                try:
                    victim = next(iter(_EMIT_TASKS))
                except StopIteration:
                    victim = None
                if victim is not None:
                    victim.cancel()
                    _EMIT_TASKS.discard(victim)
                _audit_emit_tasks_overflow += 1
                logger.warning(
                    "audit_service: _EMIT_TASKS overflow >= %d, drop arbitrary in-flight",
                    _EMIT_TASKS_MAX,
                )
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

"""Сборка audit-событий и доставка их в loging_service.

`emit()` собирает payload и кладёт его в durable outbox
(`services/audit_outbox.py` → таблица `audit_outbox`), а не стреляет
HTTP'ом сам. Доставкой занимается фоновый drain-loop
(`services/audit_outbox_publisher.py`): недоступный loging_service больше
не означает потерянное событие — строка лежит в очереди и переотправится.

Любой не переданный параметр подтягивается из `audit_context` (username,
department_id, request_id, ip_address, user_agent, subject_type).
`details` всегда проходит через `redact_payload` — пароли, токены,
секреты становятся типизированными плейсхолдерами.

Сигнатура `emit()` намеренно не менялась: её зовут из сотни мест, в том
числе из except-веток. Как и раньше, она синхронная, ничего не ждёт и
никогда не бросает наружу.

`deliver()` — единственный сетевой путь наружу, им пользуется только
publisher. На не-2xx и на транспортную ошибку бросает
`AuditDeliveryError`, где `permanent=True` значит «retry не поможет»
(4xx кроме 429).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import httpx

from src.core.config import get_settings
from src.core.constants import SERVICE_NAME as _SERVICE_NAME
from src.core.http import bearer_header
from src.services import audit_context, audit_outbox
from src.services.audit_events import default_severity
from src.services.redaction import redact_payload

logger = logging.getLogger("audit")

_audit_client: httpx.AsyncClient | None = None

_EVENTS_PATH = "/api/logging/v1/events"


class AuditDeliveryError(Exception):
    """Событие не доставлено в loging_service.

    `status_code` — HTTP-код ответа (None для транспортной ошибки),
    `permanent=True` — loging_service ответил 4xx (кроме 429): payload
    ему не годится, повторять с тем же телом бессмысленно.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        permanent: bool = False,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.permanent = permanent
        # Значение `Retry-After` с 429, если loging его прислал. Publisher
        # берёт его как нижнюю границу собственного backoff'а.
        self.retry_after = retry_after


def get_dropped_429_total() -> int:
    """Счётчик для `/ready`. Историческое имя ключа, новая семантика.

    До появления outbox'а тут считались события, выброшенные после трёх
    429 подряд. Теперь 429 — обычный transient: строка получает backoff и
    уезжает на следующий тик drain-loop'а, терять её незачем. Считаем
    события, которые не доехали даже до таблицы (БД недоступна,
    sync-контекст без event loop'а), — это единственный оставшийся способ
    потерять audit-запись на нашей стороне.
    """
    return audit_outbox.get_not_persisted_total()


def _parse_retry_after_seconds(value: str | None) -> float | None:
    """Распарсить `Retry-After`. Поддерживается только целочисленный delta-seconds."""
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
    return min(seconds, 30.0)


def _fallback_stub(payload: dict) -> dict:
    """Sanitized stub для лог-fallback'а: только action+actor+status, без details."""
    return {
        "action": payload.get("action"),
        "actor_id": payload.get("actor_id"),
        "actor_type": payload.get("actor_type"),
        "status": payload.get("status"),
        "request_id": payload.get("request_id"),
    }


def _json_safe(payload: dict) -> dict:
    """Гарантировать, что payload ляжет в JSONB.

    Раньше несериализуемое значение в `details` всплывало на `json=` в
    httpx и тихо гасло в best-effort обёртке. Теперь payload едет в БД,
    и такой сюрприз оборвал бы запись всего буфера запроса, поэтому
    прогоняем через `json.dumps(default=str)` здесь.
    """
    try:
        return json.loads(json.dumps(payload))
    except (TypeError, ValueError):
        return json.loads(json.dumps(payload, default=str))


async def deliver(payload: dict) -> None:
    """Отправить одно событие в loging_service. Бросает `AuditDeliveryError`.

    Pooled-клиент используется, если lifespan успел его поднять; иначе
    per-call клиент (фоновые/тестовые пути без lifespan'а).
    """
    settings = get_settings()
    logging_url = getattr(settings, "logging_service_url", None)
    api_key = getattr(settings, "logging_service_api_key", None)
    if not (logging_url and api_key):
        raise AuditDeliveryError("logging service is not configured")

    headers = {**bearer_header(api_key), "X-Service-Identity": _SERVICE_NAME}
    rid = payload.get("request_id")
    if rid:
        headers["X-Request-ID"] = str(rid)

    try:
        client = _audit_client
        if client is not None:
            response = await client.post(_EVENTS_PATH, json=payload, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=2.0) as fallback:
                response = await fallback.post(
                    f"{logging_url.rstrip('/')}{_EVENTS_PATH}",
                    json=payload,
                    headers=headers,
                )
    except httpx.HTTPError as exc:
        raise AuditDeliveryError(f"{type(exc).__name__}: {exc}") from exc

    if 200 <= response.status_code < 300:
        return

    # 429 — transient: loging просит подождать. Retry-After уважаем как
    # нижнюю границу backoff'а, дальше решает publisher.
    permanent = 400 <= response.status_code < 500 and response.status_code != 429
    raise AuditDeliveryError(
        f"loging_service responded {response.status_code}",
        status_code=response.status_code,
        permanent=permanent,
        retry_after=_parse_retry_after_seconds(response.headers.get("Retry-After")),
    )


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
    *,
    actor_type: str | None = None,
    target_id: str | None = None,
    target_type: str | None = None,
    service: str = _SERVICE_NAME,
    status: str = "success",
    allowed: bool = True,
    details: dict | None = None,
    request_id: str | None = None,
    department_id: str | None = None,
    department_name: str | None = None,
    username: str | None = None,
    severity: str | None = None,
) -> None:
    """Собрать событие и поставить его в outbox. Наружу не бросает."""
    try:
        settings = get_settings()
        ctx = audit_context.get_context()

        resolved_actor_id = actor_id if actor_id is not None else ctx.actor_id
        resolved_actor_type = actor_type if actor_type is not None else (ctx.subject_type or "user")
        resolved_username = username if username is not None else ctx.username
        resolved_department = department_id if department_id is not None else ctx.department_id
        resolved_department_name = (
            department_name if department_name is not None else ctx.department_name
        )
        resolved_request_id = request_id if request_id is not None else ctx.request_id
        resolved_severity = severity if severity is not None else default_severity(action, status)

        enriched = _enrich_details(details)
        sanitized = redact_payload(enriched)

        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "service": service,
            "action": action,
            "actor_id": resolved_actor_id,
            "actor_type": resolved_actor_type,
            "username": resolved_username,
            "department_id": resolved_department,
            "department_name": resolved_department_name,
            "target_id": target_id,
            "target_type": target_type,
            "status": status,
            "allowed": allowed,
            "severity": resolved_severity,
            "request_id": resolved_request_id,
            "actor_ip": ctx.ip_address,
            "user_agent": ctx.user_agent,
            "details": sanitized,
        }

        logging_url = getattr(settings, "logging_service_url", None)
        api_key = getattr(settings, "logging_service_api_key", None)

        # Удалённый аудит выключен (dev/test без loging_service) — писать в
        # outbox нечего, никто эти строки не вычерпает. Остаётся лог.
        if not (logging_url and api_key):
            logger.info("audit_event %s", _fallback_stub(payload))
            return

        audit_outbox.stage(_json_safe(payload))
    except Exception as exc:  # noqa: BLE001 — аудит не роняет бизнес-операцию
        logger.warning("audit_service: failed to enqueue event %s: %s", action, exc)

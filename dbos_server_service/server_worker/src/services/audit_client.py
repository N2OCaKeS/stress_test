"""Audit publisher — POST'им события в loging_service.

Failure semantics (важно для outbox-инварианта):

  * HTTP 4xx → `AuditEmitError(status_code=4xx)`. Publisher трактует как
    permanent-fatal: плохой payload, dead key, missing claim — retry'ить
    бесполезно, событие уходит в DLQ (`published_at=now()`+counter+ERROR-log).
  * HTTP 5xx → `AuditEmitError(status_code=5xx)`. Publisher оставляет
    row unpublished — следующий проход повторит. Это transient: загрузка,
    rolling-restart loging_service и т.п.
  * `httpx.HTTPError` (Timeout / ConnectError / прочие транспортные) →
    `AuditEmitError(status_code=None)`. Trate'ится publisher'ом как
    transient (retry через outbox) — ответа не было, мы не можем знать,
    «получил ли loging_service» событие, дедуп на их стороне рассчитан
    на at-least-once.
  * Только при успешном 2xx — функция возвращает None без исключения.

Раньше эти ветки были «log-and-swallow»: `audit_outbox_publisher._publish_one`
после успешного return помечал outbox-row `published_at=now()`, даже если
loging_service отверг событие (404/422/500/timeout) → silent loss of audit-
event. Теперь publisher ловит `AuditEmitError`, классифицирует по
`status_code` и принимает решение retry vs DLQ.
"""

import logging
from datetime import datetime, timezone

import httpx

from src.core.config import get_settings

logger = logging.getLogger(__name__)

_INGEST_PATH = "/api/logging/v1/events"


class AuditEmitError(Exception):
    """loging_service не подтвердил доставку события.

    Бросается из `emit()` при non-2xx или транспортной ошибке. Поле
    `error_message` — короткое описание для логов и `audit_outbox.last_error`
    (publisher уже прогоняет его через `redact_error_message`, поэтому
    здесь redaction делать не нужно — это сделает caller).

    `status_code` — HTTP статус, если ошибка пришла из ответа loging_service
    (4xx или 5xx). `None` — для транспортных ошибок (timeout/connect/DNS),
    где ответа не было. Publisher использует это поле, чтобы отличить
    permanent-fatal 4xx (плохой payload, dead key — retry'ить бесполезно,
    сразу в DLQ) от transient 5xx/transport (retry через outbox).
    """

    def __init__(self, error_message: str, *, status_code: int | None = None):
        super().__init__(error_message)
        self.error_message = error_message
        self.status_code = status_code


async def emit(
    action: str,
    *,
    status: str = "success",
    allowed: bool = True,
    actor_id: str | None = None,
    actor_type: str = "service",
    department_id: str | None = None,
    target_id: str | None = None,
    target_type: str | None = None,
    severity: str | None = None,
    details: dict | None = None,
    request_id: str | None = None,
) -> None:
    """Отправить audit-событие в loging_service.

    Параметры — стандартный набор audit-payload'а (action + actor + target +
    severity + details). Пустые опциональные поля в payload не пишутся,
    чтобы не раздувать JSON.

    Возвращает: None при успешном 2xx. На любую другую ветку бросает
    `AuditEmitError` (см. module docstring failure semantics).

    Возможные ошибки: `AuditEmitError` — caller (`audit_outbox_publisher`)
    инкрементит attempts и держит row unpublished для следующего захода.

    Связано с: `audit_outbox_publisher._publish_one` — единственный
    легальный caller; не дёргать напрямую из task-handler'ов (нарушит
    outbox-инвариант).
    """
    settings = get_settings()
    payload: dict = {
        "action": action,
        "status": status,
        "allowed": allowed,
        "actor_type": actor_type,
        "service": "server_worker",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if actor_id is not None:
        payload["actor_id"] = actor_id
    if department_id is not None:
        payload["department_id"] = department_id
    if target_id is not None:
        payload["target_id"] = target_id
    if target_type is not None:
        payload["target_type"] = target_type
    if severity is not None:
        payload["severity"] = severity
    if request_id is not None:
        payload["request_id"] = request_id
    if details:
        payload["details"] = details

    url = f"{settings.logging_service_url.rstrip('/')}{_INGEST_PATH}"
    # loging_service /events использует общий SERVICE_API_KEY. НЕ должны
    # fallback'аться на `worker_bot_token` — это PAT scoped в server_service
    # с global admin'ом, и любой `audit_client.emit()` смог бы выдать
    # событие от любого `service=` (audit-trail poisoning). Если
    # выделенный ключ не выставлен — лучше drop event, чем мисъюзить PAT.
    #
    # NB: это единственная ветка с «тихим» возвратом из emit. Outbox-row
    # при этом помечается published — корректно, т.к. без API-key мы
    # НИКОГДА не доставим событие, retry бесполезен. Operator должен
    # заметить по ERROR-логу + по тому, что outbox растёт быстрее, чем
    # drain'ится (отдельный health-check, ещё не реализован).
    api_key = settings.logging_service_api_key
    if not api_key:
        logger.error(
            "LOGGING_SERVICE_API_KEY is not set; audit event dropped for %s",
            action,
        )
        return
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=settings.http_request_timeout_seconds) as client:
            response = await client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        # Transport-level ошибка (timeout, connect-refused, DNS, TLS, ...).
        # Поднимаем — outbox publisher решит, что делать (retry, attempts++).
        logger.warning("audit emit transport failed for %s: %s", action, type(exc).__name__)
        raise AuditEmitError(f"{type(exc).__name__}: {exc}") from exc

    if response.status_code >= 400:
        # loging_service отверг событие. И 4xx, и 5xx уходят через
        # `AuditEmitError`, но с заполненным `status_code` — publisher
        # сам решит, что делать: 4xx (плохой payload, dead key, missing
        # claim) — permanent failure, в DLQ сразу; 5xx — transient,
        # ретраим через outbox.
        logger.warning(
            "audit emit rejected by loging_service: status=%s action=%s",
            response.status_code,
            action,
        )
        raise AuditEmitError(
            f"loging_service returned HTTP {response.status_code} for {action}",
            status_code=response.status_code,
        )

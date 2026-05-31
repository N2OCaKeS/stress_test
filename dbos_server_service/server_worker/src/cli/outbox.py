"""CLI-команда `outbox-reattempt` — ручной re-queue DLQ-row'а.

Сценарий: publisher (`audit_outbox_publisher`) отбраковал row в DLQ
(`published_at = now()`, причина в `last_error` — `attempts_cap` /
`permanent_4xx` / `missing_action`). Оператор починил root cause
(например, развернул payload-схему loging_service) и хочет повторить
доставку без рестарта worker'а.

Альтернатива — kiq-ать taskiq-таску `internal.outbox_re_attempt` через
broker. CLI удобнее в инцидент-режиме: можно зайти `kubectl exec` в pod
и ткнуть конкретный row без живого scheduler'а / Redis-консоли.

Команда:

  python -m src.cli outbox-reattempt <row_id> [--reason ...] [--actor-id ...]

После успешного reset'а кладёт в `audit_outbox` событие
`audit.outbox_reattempt_manual` (severity=WARNING) — оно поедет в
loging_service вместе с обычными audit-row'ами. WARNING сознательно:
ручной re-attempt — это нештатное вмешательство в очередь, оператор
SIEM должен его заметить.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from src.db.session import AsyncSessionLocal
from src.repositories import task as task_repo
from src.services import audit_outbox_publisher
from src.utils.redaction import redact_error_message

logger = logging.getLogger(__name__)


async def cmd_outbox_reattempt(
    *,
    row_id: int,
    reason: str | None = None,
    actor_id: str | None = None,
) -> bool:
    """Сбросить row из DLQ и зафиксировать audit-событие.

    Шаги:
      1. `re_attempt_row` — основной reset
         (`published_at`/`attempts`/`next_retry_at`/`last_error` → NULL/0).
      2. Если reset удался — `enqueue_audit` с `severity=WARNING`,
         payload включает `target_id=row_id` и `details` с reason'ом.
      3. Best-effort `flush_outbox()` — пробуем доставить audit-событие
         сразу, не дожидаясь background loop'а; если loging лежит —
         publisher разгребёт.

    Возвращает True/False по итогу шага 1; audit-эмит идёт через
    outbox, его потеря не должна валить exit-code команды.
    """
    ok = await audit_outbox_publisher.re_attempt_row(row_id)
    if not ok:
        print(
            f"outbox-reattempt: row={row_id} не найдена либо уже unpublished",
            flush=True,
        )
        return False

    audit_payload = {
        "action": "audit.outbox_reattempt_manual",
        "status": "success",
        "allowed": True,
        "actor_id": actor_id,
        "actor_type": "operator",
        "target_id": str(row_id),
        "target_type": "audit_outbox",
        "severity": "WARNING",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "details": {
            "row_id": row_id,
            "reason": reason,
            "source": "cli",
        },
    }

    try:
        async with AsyncSessionLocal() as session:
            await task_repo.enqueue_audit(
                session, task_id=None, payload=audit_payload,
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001 — audit best-effort, не валим reset
        # CLI вызывается через `kubectl exec`; репра клиента или DSN из
        # `.env` могут вшить в exception URL c basic-auth/Bearer. Прогоняем
        # через тот же sanitizer, что и остальные worker-call-site'ы.
        logger.warning(
            "outbox-reattempt: audit enqueue failed row=%s: %s",
            row_id, redact_error_message(f"{type(exc).__name__}: {exc}"),
        )

    try:
        await audit_outbox_publisher.flush_outbox()
    except Exception as exc:  # noqa: BLE001 — happy-path push, не критично
        logger.debug(
            "outbox-reattempt: audit flush failed row=%s: %s",
            row_id, redact_error_message(f"{type(exc).__name__}: {exc}"),
        )

    print(
        f"outbox-reattempt: row={row_id} re-queued (audit emitted)",
        flush=True,
    )
    return True

"""Канонические test-хелперы loging_service.

Чтобы не плодить копии _env / _insert_rule / _wait_for_event по тестам,
держим единственную версию здесь. Импорт:

    from tests._helpers import make_env, insert_rule, wait_for_event

Сигнатуры подобраны так, чтобы покрыть все известные локальные вариации:
у `make_env` дефолт `action` совпадает с большинством исходных копий,
позиционный/keyword вызов даёт ту же семантику.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from sqlalchemy import select

from src.models.audit_event import AuditEvent
from src.models.audit_rule import AuditRule
from src.services.audit_outbox import AuditEnvelope, make_envelope
from src.utils.ids import audit_rule_id


def make_env(action: str = "user.login") -> AuditEnvelope:
    """Минимальный AuditEnvelope для outbox/drain-тестов."""
    return make_envelope(
        action=action,
        actor_id=None,
        actor_type=None,
        username=None,
        emit_status="success",
        allowed=True,
        request_id=None,
        details={},
    )


def insert_rule(db, name: str = "rule-test") -> AuditRule:
    """Создаёт минимальное активное правило ALLOW priority=100."""
    now = datetime.now(timezone.utc)
    rule = AuditRule(
        id=audit_rule_id(),
        name=name,
        description="",
        is_active=True,
        priority=100,
        effect="ALLOW",
        created_at=now,
        updated_at=now,
    )
    db.add(rule)
    db.commit()
    return rule


def wait_for_event(db, action: str, status_code: int, timeout: float = 1.5):
    """Опросом ждём, пока drain выгребет событие из outbox в audit_events."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        rows = db.execute(
            select(AuditEvent).where(AuditEvent.action == action)
        ).scalars().all()
        for r in rows:
            details = r.details or {}
            if details.get("status_code") == status_code:
                return r
        time.sleep(0.05)
    return None

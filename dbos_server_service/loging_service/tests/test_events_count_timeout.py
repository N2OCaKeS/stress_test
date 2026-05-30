"""COUNT в `events.query(include_total=True)` ограничен statement_timeout.

Гард в `repositories/events.py` оборачивает COUNT в SAVEPOINT и ставит
сессионный `SET LOCAL statement_timeout = :ms`. При срабатывании Postgres
поднимает `57014 query_canceled`, репо возвращает `total=None` и не
ломает выпуск страницы.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text

from src.core.config import get_settings
from src.models.audit_event import AuditEvent
from src.repositories import events as events_repo
from src.utils.ids import audit_event_id


def _insert_row(db):
    row = AuditEvent(
        id=audit_event_id(),
        timestamp=datetime.now(timezone.utc),
        service="auth_service",
        action="user.login",
        actor_type="user",
        status="success",
        allowed=True,
        severity="INFO",
    )
    db.add(row)
    db.commit()
    return row


def test_count_set_local_statement_timeout_applies_configured_value(db, monkeypatch):
    """`SET LOCAL statement_timeout` отрабатывает с значением из настроек.

    Sticky-нечислом — внутри SAVEPOINT'а `SHOW statement_timeout` должен
    вернуть ровно сконфигурированный bound. После выхода — обратно дефолт.
    """
    monkeypatch.setenv("AUDIT_COUNT_STATEMENT_TIMEOUT_MS", "7531")
    get_settings.cache_clear()
    try:
        _insert_row(db)

        captured: dict[str, str] = {}
        original_execute = db.execute

        def spy(clause, *args, **kwargs):
            result = original_execute(clause, *args, **kwargs)
            sql_text = str(getattr(clause, "text", clause))
            if "set local statement_timeout" in sql_text.lower():
                captured["value"] = original_execute(
                    text("SHOW statement_timeout")
                ).scalar_one()
            return result

        monkeypatch.setattr(db, "execute", spy)

        events, total, has_more = events_repo.query(db, include_total=True)

        assert captured.get("value") == "7531ms"
        assert total == 1
        assert len(events) == 1
        assert has_more is False
    finally:
        get_settings.cache_clear()

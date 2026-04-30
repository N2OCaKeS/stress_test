"""ServiceEvent repository — upsert and query registered service events."""

import re
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from src.models.service_event import ServiceEvent
from src.utils.ids import service_event_id


def upsert_events(
    db: Session,
    service: str,
    events: list[dict],
) -> tuple[int, int]:
    """Upsert a batch of event definitions for *service*.

    Returns (added, updated) counts.
    """
    if not events:
        return 0, 0

    now = datetime.now(timezone.utc)
    existing = {
        row.action: row
        for row in db.execute(
            select(ServiceEvent).where(ServiceEvent.service == service)
        ).scalars()
    }

    added = updated = 0
    for ev in events:
        action = ev["action"]
        if action in existing:
            row = existing[action]
            row.description = ev.get("description", row.description)
            row.default_severity = ev.get("default_severity", row.default_severity)
            row.updated_at = now
            updated += 1
        else:
            db.add(ServiceEvent(
                id=service_event_id(),
                service=service,
                action=action,
                description=ev.get("description"),
                default_severity=ev.get("default_severity"),
                registered_at=now,
                updated_at=now,
            ))
            added += 1

    db.commit()
    return added, updated


def list_for_service(
    db: Session,
    service: str,
    limit: int = 1000,
    offset: int = 0,
) -> tuple[list[ServiceEvent], int]:
    from sqlalchemy import func
    total = db.execute(
        select(func.count()).select_from(ServiceEvent).where(ServiceEvent.service == service)
    ).scalar_one()
    rows = list(
        db.execute(
            select(ServiceEvent)
            .where(ServiceEvent.service == service)
            .order_by(ServiceEvent.action)
            .offset(offset)
            .limit(limit)
        ).scalars()
    )
    return rows, total


def list_all(db: Session) -> list[ServiceEvent]:
    return list(
        db.execute(select(ServiceEvent).order_by(ServiceEvent.service, ServiceEvent.action)).scalars()
    )


def action_is_registered(db: Session, match_action: str) -> bool:
    """Return True if *match_action* matches at least one registered event.

    Exact strings require an exact match.
    Patterns containing '*' are matched via fnmatch against all registered actions.
    """
    if "*" in match_action:
        from src.services.rule_service import action_matches_pattern
        all_actions = list(db.execute(select(ServiceEvent.action)).scalars())
        return any(action_matches_pattern(a, match_action) for a in all_actions)
    exists = db.execute(
        select(ServiceEvent.id).where(ServiceEvent.action == match_action).limit(1)
    ).scalar()
    return exists is not None

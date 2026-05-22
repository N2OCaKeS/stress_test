"""Репозиторий `ServiceEvent` — upsert и query реестра событий сервисов."""

from datetime import datetime, timezone

from sqlalchemy import literal_column, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from src.models.service_event import ServiceEvent
from src.utils.ids import service_event_id


def upsert_events(
    db: Session,
    service: str,
    events: list[dict],
) -> tuple[int, int]:
    """Upsert батча определений событий для *service*.

    Возвращает (added, updated). Атомарен на уровне БД: каждый row идёт через
    `INSERT … ON CONFLICT (service, action) DO UPDATE`, поэтому параллельные
    upsert'ы одной (service, action) не падают `IntegrityError`. RETURNING
    `xmax = 0` отличает свежий INSERT (xmax = 0) от UPDATE (xmax = xid) —
    дёшево и не просит лишний SELECT для подсчёта.
    """
    if not events:
        return 0, 0

    now = datetime.now(timezone.utc)
    added = updated = 0

    for ev in events:
        stmt = (
            pg_insert(ServiceEvent)
            .values(
                id=service_event_id(),
                service=service,
                action=ev["action"],
                description=ev.get("description"),
                default_severity=ev.get("default_severity"),
                registered_at=now,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=["service", "action"],
                set_={
                    "description": ev.get("description"),
                    "default_severity": ev.get("default_severity"),
                    "updated_at": now,
                },
            )
            .returning(literal_column("(xmax = 0)").label("was_inserted"))
        )
        was_inserted = db.execute(stmt).scalar_one()
        if was_inserted:
            added += 1
        else:
            updated += 1

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
    """True, если *match_action* совпадает хотя бы с одним зарегистрированным событием.

    Точные строки требуют точного совпадения. Паттерны со `*` проверяются
    через `action_matches_pattern` против всех зарегистрированных action'ов.
    """
    if "*" in match_action:
        from src.services.rule_service import action_matches_pattern
        all_actions = list(db.execute(select(ServiceEvent.action)).scalars())
        return any(action_matches_pattern(a, match_action) for a in all_actions)
    exists = db.execute(
        select(ServiceEvent.id).where(ServiceEvent.action == match_action).limit(1)
    ).scalar()
    return exists is not None

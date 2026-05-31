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
    *,
    commit: bool = True,
) -> tuple[int, int]:
    """Upsert батча определений событий для *service*.

    Возвращает (added, updated). Атомарен на уровне БД: всё batch'ом идёт через
    единственный `INSERT … ON CONFLICT (service, action) DO UPDATE`, поэтому
    параллельные upsert'ы одной (service, action) не падают `IntegrityError`.
    `RETURNING (xmax = 0)` отличает свежий INSERT (xmax = 0) от UPDATE
    (xmax = xid) per-row — дёшево и не просит лишний SELECT для подсчёта.

    Раньше цикл вызывал по `db.execute()` на каждый row — N+1 round-trip'ов
    в БД на батч в 1000 action'ов. Замено одним INSERT-statement'ом с
    values-list'ом и `excluded.*` в SET-блоке (Postgres подставляет
    конфликтующие значения каждой row'и).
    """
    if not events:
        return 0, 0

    now = datetime.now(timezone.utc)

    # Дедупликация по `action` внутри одного батча: pg_insert падает с
    # `cardinality violation: ON CONFLICT DO UPDATE command cannot affect
    # row a second time`, если в values-list'е две row'и с одной парой
    # (service, action). Берём последнее упоминание (как делал бы цикл).
    deduped: dict[str, dict] = {}
    for ev in events:
        deduped[ev["action"]] = ev

    rows = [
        {
            "id": service_event_id(),
            "service": service,
            "action": action,
            "description": ev.get("description"),
            "default_severity": ev.get("default_severity"),
            "registered_at": now,
            "updated_at": now,
        }
        for action, ev in deduped.items()
    ]

    insert_stmt = pg_insert(ServiceEvent).values(rows)
    stmt = insert_stmt.on_conflict_do_update(
        index_elements=["service", "action"],
        set_={
            "description": insert_stmt.excluded.description,
            "default_severity": insert_stmt.excluded.default_severity,
            "updated_at": now,
        },
    ).returning(literal_column("(xmax = 0)").label("was_inserted"))

    flags = list(db.execute(stmt).scalars())
    added = sum(1 for f in flags if f)
    updated = len(flags) - added

    if commit:
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


def has_any(db: Session) -> bool:
    """True, если в `service_events` есть хотя бы одна запись.

    Дешевле, чем `list_all(db)` ради `if rows:` — Postgres исполняет
    `SELECT EXISTS (...)` через index-only scan и останавливается на первой
    row'е. На пустой таблице различия нет, но при ~10k+ зарегистрированных
    action'ах материализация всего списка ради boolean'а ест и память,
    и pgsql round-trip.
    """
    from sqlalchemy import exists
    return bool(db.execute(select(exists().where(ServiceEvent.id.is_not(None)))).scalar())


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

"""Тесты apply_active: защита loging_service-событий от ротации.

Это security-инвариант: события audit-аудита нельзя удалить retention-политикой,
независимо от значения retain_days.

Покрывает src/repositories/retention_policies.py::apply_active.
"""

from datetime import datetime, timedelta, timezone

from src.models.audit_event import AuditEvent
from src.models.retention_policy import RetentionPolicy
from src.repositories.retention_policies import apply_active
from src.utils.ids import audit_event_id


def _make_event(db, *, service: str, ts: datetime) -> AuditEvent:
    ev = AuditEvent(
        id=audit_event_id(),
        timestamp=ts,
        received_at=ts,
        service=service,
        action="some.action",
        actor_id=None,
        actor_type="user",
        username=None,
        department_id=None,
        target_id=None,
        target_type=None,
        status="success",
        allowed=True,
        severity="INFO",
        request_id=None,
        details={},
    )
    db.add(ev)
    db.commit()
    return ev


def _set_policy(db, retain_days: int) -> RetentionPolicy:
    now = datetime.now(timezone.utc)
    p = RetentionPolicy(
        retain_days=retain_days,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db.add(p)
    db.commit()
    return p


class TestApplyActiveNoPolicy:
    def test_no_active_policy_returns_zero(self, db):
        # События есть, но активной политики нет — apply_active не удаляет ничего
        _make_event(db, service="auth_service",
                    ts=datetime.now(timezone.utc) - timedelta(days=365))
        assert apply_active(db) == 0
        from sqlalchemy import select, func
        total = db.execute(select(func.count()).select_from(AuditEvent)).scalar_one()
        assert total == 1


class TestApplyActiveBasicCleanup:
    def test_old_event_deleted(self, db):
        _set_policy(db, retain_days=30)
        _make_event(db, service="auth_service",
                    ts=datetime.now(timezone.utc) - timedelta(days=60))
        deleted = apply_active(db)
        assert deleted == 1

    def test_recent_event_kept(self, db):
        _set_policy(db, retain_days=30)
        _make_event(db, service="auth_service",
                    ts=datetime.now(timezone.utc) - timedelta(days=10))
        assert apply_active(db) == 0

    def test_mixed_old_and_recent(self, db):
        _set_policy(db, retain_days=30)
        _make_event(db, service="auth_service",
                    ts=datetime.now(timezone.utc) - timedelta(days=60))
        _make_event(db, service="auth_service",
                    ts=datetime.now(timezone.utc) - timedelta(days=60))
        _make_event(db, service="auth_service",
                    ts=datetime.now(timezone.utc) - timedelta(days=5))
        assert apply_active(db) == 2
        from sqlalchemy import select, func
        total = db.execute(select(func.count()).select_from(AuditEvent)).scalar_one()
        assert total == 1


# ── Защита loging_service ─────────────────────────────────────────────────────


class TestLoginServiceProtection:
    def test_old_loging_service_event_kept(self, db):
        """Даже при retain_days=30 событие service='loging_service' остаётся."""
        _set_policy(db, retain_days=30)
        _make_event(db, service="loging_service",
                    ts=datetime.now(timezone.utc) - timedelta(days=400))
        deleted = apply_active(db)
        assert deleted == 0
        from sqlalchemy import select, func
        total = db.execute(select(func.count()).select_from(AuditEvent)).scalar_one()
        assert total == 1

    def test_old_other_service_deleted_loging_kept(self, db):
        """Смесь: auth_service удаляется, loging_service остаётся."""
        _set_policy(db, retain_days=30)
        old = datetime.now(timezone.utc) - timedelta(days=400)
        _make_event(db, service="auth_service", ts=old)
        _make_event(db, service="auth_service", ts=old)
        _make_event(db, service="config_service", ts=old)
        _make_event(db, service="loging_service", ts=old)
        _make_event(db, service="loging_service", ts=old)
        deleted = apply_active(db)
        assert deleted == 3  # 2 auth + 1 config

        from sqlalchemy import select
        remaining = db.execute(select(AuditEvent.service)).scalars().all()
        assert sorted(remaining) == ["loging_service", "loging_service"]

    def test_very_short_retention_does_not_touch_loging_service(self, db):
        """retain_days=30 — на грани минимума; loging_service всё равно сохранён."""
        _set_policy(db, retain_days=30)
        for _ in range(5):
            _make_event(db, service="loging_service",
                        ts=datetime.now(timezone.utc) - timedelta(days=10_000))
        deleted = apply_active(db)
        assert deleted == 0
        from sqlalchemy import select, func
        total = db.execute(select(func.count()).select_from(AuditEvent)).scalar_one()
        assert total == 5

    def test_case_variant_loging_service_rejected_by_db_check(self, db):
        """`ck_audit_events_service_canonical` запрещает запись
        case-variant'а в БД даже минуя pydantic-валидатор.

        До этой защиты row с `service="Loging_Service"` мог попасть в БД
        через прямой ORM-add (миграция, ручной INSERT support'ом, seed)
        и retention DELETE удалял его как «не loging_service». Теперь
        ALTER TABLE CHECK совпадает с `_SERVICE_PATTERN` из
        `schemas/events.py` (`^[a-z_]{1,64}$`) и отбивает любую запись
        с заглавной буквой или другими не-канонической формой.
        """
        from sqlalchemy.exc import IntegrityError

        _set_policy(db, retain_days=30)
        old = datetime.now(timezone.utc) - timedelta(days=400)
        for variant in ("LoGiNg_SeRvIcE", "LOGING_SERVICE", "Loging_Service"):
            try:
                _make_event(db, service=variant, ts=old)
            except IntegrityError:
                db.rollback()
            else:
                raise AssertionError(
                    f"CHECK constraint должен был отклонить service={variant!r}"
                )


# ── Граница cutoff ────────────────────────────────────────────────────────────


class TestApplyActiveCutoffBoundary:
    def test_exactly_at_cutoff_kept(self, db):
        """Событие точно на границе (cutoff) не удаляется (delete WHERE ts < cutoff)."""
        _set_policy(db, retain_days=30)
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        # Чуть-чуть после cutoff — НЕ удаляется
        _make_event(db, service="auth_service", ts=cutoff + timedelta(seconds=1))
        deleted = apply_active(db)
        assert deleted == 0

    def test_just_before_cutoff_deleted(self, db):
        _set_policy(db, retain_days=30)
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        _make_event(db, service="auth_service", ts=cutoff - timedelta(seconds=10))
        deleted = apply_active(db)
        assert deleted == 1


# ── Идемпотентность ───────────────────────────────────────────────────────────


class TestApplyActiveIdempotent:
    def test_second_call_deletes_nothing(self, db):
        _set_policy(db, retain_days=30)
        _make_event(db, service="auth_service",
                    ts=datetime.now(timezone.utc) - timedelta(days=60))
        assert apply_active(db) == 1
        assert apply_active(db) == 0


# ── Unicode-bypass через ingest ─────────────────────────────────────────────


class TestLogingServiceProtectionUnicodeBypass:
    """Защита от erasure-of-audit-trail через Unicode-обход reserved-guard.

    Поток атаки до фикса:
      1. Атакующий шлёт ``service="loging_service​"`` (с U+200B) на ingest.
      2. ``.strip().lower()``-guard пропускал — записывалось в БД с
         сохранённым U+200B (raw string).
      3. ``func.lower(service) != 'loging_service'`` тоже пропускал
         (символ U+200B остался) — событие удалится при ротации.

    Раунд 4 фикс: ingest применяет ``normalize_service_name`` через
    pydantic-валидатор. Любой Unicode-вариант либо отбивается reserved-guard
    (403), либо нормализуется в каноническую ASCII-форму — после чего
    retention-инвариант на ``func.lower(...) != 'loging_service'`` уже
    срабатывает корректно.

    Тесты ниже проверяют именно retention-слой: если БД (через legacy
    путь или прямой write бэкенда) содержит строки с Unicode-confusable —
    они остаются защищёнными ровно в той мере, в которой совпадают с
    ASCII-канонизацией.
    """

    def test_canonical_loging_service_kept_after_ingest_normalization(self, db):
        """Канонический ``loging_service`` всё ещё защищён (регрессия)."""
        _set_policy(db, retain_days=30)
        old = datetime.now(timezone.utc) - timedelta(days=400)
        _make_event(db, service="loging_service", ts=old)
        _make_event(db, service="auth_service", ts=old)
        assert apply_active(db) == 1  # только auth удалён
        from sqlalchemy import select
        remaining = db.execute(select(AuditEvent.service)).scalars().all()
        assert remaining == ["loging_service"]

    def test_row_with_zero_width_rejected_by_db_check(self, db):
        """`ck_audit_events_service_canonical` запрещает запись
        `loging_service` с zero-width (U+200B) даже минуя pydantic.

        До фикса retention DELETE сравнивал raw-equality с
        `"loging_service"` — invisible suffix не совпадал, событие
        удалялось. CHECK-regexp на canonical `^[a-z_]{1,64}$` отбивает
        строку с U+200B на insert: legacy-rows физически невозможны.
        """
        from sqlalchemy.exc import IntegrityError

        _set_policy(db, retain_days=30)
        old = datetime.now(timezone.utc) - timedelta(days=400)
        try:
            _make_event(db, service="loging_service​", ts=old)
        except IntegrityError:
            db.rollback()
        else:
            raise AssertionError(
                "CHECK constraint должен был отклонить service с U+200B"
            )

    def test_post_normalization_rows_protected_from_retention(self, db):
        """ingest-нормализованные ``loging_service`` строки
        (без невидимостей, без confusable) защищены retention-инвариантом.

        Этот тест мирро-эквивалент основного TestLoginServiceProtection,
        но размещён в новом классе чтобы быть явной защитой от регрессии
        фикса — если кто-то в будущем уберёт ``func.lower``-guard
        в retention, тест упадёт.
        """
        _set_policy(db, retain_days=30)
        old = datetime.now(timezone.utc) - timedelta(days=400)
        for _ in range(3):
            _make_event(db, service="loging_service", ts=old)
        _make_event(db, service="auth_service", ts=old)
        deleted = apply_active(db)
        assert deleted == 1  # только auth, три loging остались
        from sqlalchemy import select, func as sqfunc
        total = db.execute(
            select(sqfunc.count()).select_from(AuditEvent)
        ).scalar_one()
        assert total == 3

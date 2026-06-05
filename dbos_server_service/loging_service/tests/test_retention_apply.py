"""Тесты `repositories.retention_policies.apply_active` — собственно ротация.

Базовые сценарии retention/протекция loging_service уже покрыты в
test_retention.py / test_retention_protection.py. Здесь — пограничные:

* `apply_active` без активной политики → 0 удалений;
* events на границе `cutoff` остаются;
* возвращаемое значение = реально удалённые строки (rowcount);
* `_PROTECTED_SERVICE='loging_service'` корректно защищает.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

from src.models.audit_event import AuditEvent
from src.models.retention_policy import RetentionPolicy
from src.repositories.retention_policies import _PROTECTED_SERVICE, apply_active
from src.schemas.retention import RetentionPolicyCreate
from src.repositories import retention_policies as repo


@pytest.fixture(autouse=True)
def _clear_retention(db):
    """retention_policies не входит в общий TRUNCATE — чистим точечно."""
    db.execute(text("DELETE FROM retention_policies"))
    db.commit()
    yield
    db.execute(text("DELETE FROM retention_policies"))
    db.commit()


def _add_event(
    db,
    *,
    service: str,
    days_ago: int,
    action: str = "x.y",
    severity: str = "INFO",
) -> str:
    import uuid
    ev = AuditEvent(
        id=f"log_{uuid.uuid4().hex[:16]}",
        timestamp=datetime.now(timezone.utc) - timedelta(days=days_ago),
        service=service,
        action=action,
        actor_id=None,
        actor_type="service",
        status="success",
        allowed=True,
        severity=severity,
        details={},
    )
    db.add(ev)
    db.flush()
    return ev.id


def _ids(db):
    return {r[0] for r in db.execute(select(AuditEvent.id))}


# ── No-op без политики ───────────────────────────────────────────────────────

class TestNoPolicy:
    def test_no_active_policy_returns_zero(self, db):
        eid = _add_event(db, service="auth_service", days_ago=400)
        db.commit()
        assert apply_active(db) == 0
        # событие осталось
        assert eid in _ids(db)


# ── Cutoff boundary ──────────────────────────────────────────────────────────

class TestCutoffBoundary:
    def test_event_strictly_older_deleted(self, db):
        repo.create(db, RetentionPolicyCreate(retain_days=30))
        old = _add_event(db, service="auth_service", days_ago=31)
        fresh = _add_event(db, service="auth_service", days_ago=29)
        db.commit()
        deleted = apply_active(db)
        assert deleted == 1
        remaining = _ids(db)
        assert old not in remaining
        assert fresh in remaining

    def test_event_at_exact_cutoff_kept(self, db):
        """Граница: events с timestamp ровно cutoff не удаляются (`<`, не `<=`)."""
        # 90 days policy, событие 90 дней назад — точно на границе
        repo.create(db, RetentionPolicyCreate(retain_days=90))
        # Мы не можем точно совпасть в миллисекундах, но 89.99 < cutoff → должен остаться,
        # а 90.01 → удалится. Берём близкое-но-меньшее значение.
        ev_within = _add_event(db, service="auth_service", days_ago=89)
        ev_beyond = _add_event(db, service="auth_service", days_ago=91)
        db.commit()
        deleted = apply_active(db)
        assert deleted == 1
        remaining = _ids(db)
        assert ev_within in remaining
        assert ev_beyond not in remaining

    def test_rowcount_matches_deletions(self, db):
        repo.create(db, RetentionPolicyCreate(retain_days=30))
        for i in range(5):
            _add_event(db, service="auth_service", days_ago=40 + i)
        # одно молодое
        _add_event(db, service="auth_service", days_ago=10)
        db.commit()
        assert apply_active(db) == 5


# ── Protection ───────────────────────────────────────────────────────────────

class TestProtectedService:
    def test_loging_service_events_never_deleted(self, db):
        repo.create(db, RetentionPolicyCreate(retain_days=30))
        protected = _add_event(db, service=_PROTECTED_SERVICE, days_ago=400)
        regular = _add_event(db, service="auth_service", days_ago=400)
        db.commit()
        deleted = apply_active(db)
        assert deleted == 1
        remaining = _ids(db)
        assert protected in remaining
        assert regular not in remaining

    def test_protected_service_name_is_loging_service(self):
        """Снэпшот константы — изменение требует sync с документацией."""
        assert _PROTECTED_SERVICE == "loging_service"

    def test_only_loging_service_protected_not_others(self, db):
        repo.create(db, RetentionPolicyCreate(retain_days=30))
        loging = _add_event(db, service="loging_service", days_ago=400)
        auth = _add_event(db, service="auth_service", days_ago=400)
        server = _add_event(db, service="server_service", days_ago=400)
        worker = _add_event(db, service="server_worker", days_ago=400)
        db.commit()
        apply_active(db)
        remaining = _ids(db)
        assert remaining == {loging}, "только loging_service защищён"


# ── Inactive policy ──────────────────────────────────────────────────────────

class TestInactivePolicy:
    def test_inactive_policy_is_skipped(self, db):
        repo.create(db, RetentionPolicyCreate(retain_days=30, is_active=False))
        eid = _add_event(db, service="auth_service", days_ago=400)
        db.commit()
        assert apply_active(db) == 0
        assert eid in _ids(db)

    def test_most_recent_active_policy_used(self, db):
        """`get_active` берёт самую новую активную политику."""
        # старая активная: 365 дней (мягкая)
        repo.create(db, RetentionPolicyCreate(retain_days=365))
        # новая активная: 30 дней (строгая) — должна выиграть
        repo.create(db, RetentionPolicyCreate(retain_days=30))
        old = _add_event(db, service="auth_service", days_ago=200)
        # 200 дней < 365 (не удалит по первой), > 30 (удалит по второй)
        db.commit()
        deleted = apply_active(db)
        assert deleted == 1
        assert old not in _ids(db)


# ── Empty DB ─────────────────────────────────────────────────────────────────

class TestEmptyDb:
    def test_apply_on_empty_events(self, db):
        repo.create(db, RetentionPolicyCreate(retain_days=30))
        db.commit()
        assert apply_active(db) == 0


# ── Chunked DELETE ───────────────────────────────────────────────────────────

class TestChunkedSweep:
    """Sweep удаляет всё за несколько чанков, не одним мега-DELETE'ом.

    Маленький chunk_size заставляет цикл крутиться > 1 раза — проверяем, что
    суммарно удаляется всё подходящее и ничего лишнего не остаётся.
    """

    def test_multiple_chunks_delete_everything(self, db):
        repo.create(db, RetentionPolicyCreate(retain_days=30))
        for _ in range(25):
            _add_event(db, service="auth_service", days_ago=100)
        # одно молодое, чтобы убедиться, что чанкинг не задевает свежие
        fresh = _add_event(db, service="auth_service", days_ago=5)
        db.commit()

        deleted = apply_active(db, chunk_size=10)
        assert deleted == 25
        remaining = _ids(db)
        assert remaining == {fresh}

    def test_chunk_boundary_exact_multiple(self, db):
        """Когда число строк кратно chunk_size — последний чанк удаляет 0 и
        цикл корректно завершается (не зацикливается)."""
        repo.create(db, RetentionPolicyCreate(retain_days=30))
        for _ in range(20):
            _add_event(db, service="auth_service", days_ago=100)
        db.commit()

        deleted = apply_active(db, chunk_size=10)
        assert deleted == 20
        assert _ids(db) == set()

    def test_chunked_respects_protection(self, db):
        """Чанкинг не должен задеть loging_service-события."""
        repo.create(db, RetentionPolicyCreate(retain_days=30))
        protected = [
            _add_event(db, service="loging_service", days_ago=100)
            for _ in range(5)
        ]
        for _ in range(15):
            _add_event(db, service="auth_service", days_ago=100)
        db.commit()

        deleted = apply_active(db, chunk_size=4)
        assert deleted == 15
        assert _ids(db) == set(protected)


# ── Overlapping policies — счёт уникальных удалений ──────────────────────────


class TestOverlappingPolicies:
    """Когда событие подпадает под несколько политик — total = число
    УНИКАЛЬНЫХ удалённых строк, а не сумма policy-rowcount'ов."""

    def test_global_plus_filtered_does_not_double_count(self, db):
        # Global: всё, retain_days=30. Filtered: CRITICAL+auth_service, retain_days=30.
        # CRITICAL-события auth_service подпадают под обе политики; total
        # должен считать их один раз.
        repo.create(db, RetentionPolicyCreate(retain_days=30))
        repo.create_policy(
            db,
            RetentionPolicyCreate(
                retain_days=30,
                severity_filter=["CRITICAL"],
                service_filter=["auth_service"],
            ),
        )
        # 3 CRITICAL auth_service events (под обе политики)
        for _ in range(3):
            _add_event(db, service="auth_service", severity="CRITICAL", days_ago=100)
        # 2 INFO auth_service event (только под global)
        for _ in range(2):
            _add_event(db, service="auth_service", severity="INFO", days_ago=100)
        db.commit()

        deleted = apply_active(db)
        # Уникально удалено ровно 5 строк, не 5+3=8.
        assert deleted == 5
        assert _ids(db) == set()

    def test_overlapping_with_different_retain_days(self, db):
        # Filtered short: retain=30 для CRITICAL/auth_service.
        # Filtered long: retain=365 для всех CRITICAL.
        # Событие 100 дней назад CRITICAL/auth_service подпадает под первую
        # (deleteable), но НЕ под вторую — должно быть удалено по первой,
        # и счётчик его учитывает один раз.
        repo.create_policy(
            db,
            RetentionPolicyCreate(
                retain_days=30,
                severity_filter=["CRITICAL"],
                service_filter=["auth_service"],
            ),
        )
        repo.create_policy(
            db,
            RetentionPolicyCreate(
                retain_days=365,
                severity_filter=["CRITICAL"],
            ),
        )
        target = _add_event(
            db, service="auth_service", severity="CRITICAL", days_ago=100
        )
        # CRITICAL/other_service @100d — под вторую не попадает (нужно 365d),
        # под первую тоже нет (service mismatch) → не удаляется.
        kept = _add_event(
            db, service="server_service", severity="CRITICAL", days_ago=100
        )
        db.commit()

        deleted = apply_active(db)
        assert deleted == 1
        assert target not in _ids(db)
        assert kept in _ids(db)

    def test_self_audit_details_filtered_set_honest(self, db):
        """Self-audit под filter-режимом — массив `policies` + min/max
        `retain_days`. НЕ единый «представительский» `retain_days`,
        который врал под filtered-set."""
        from src.main import _build_retention_sweep_details
        from src.repositories.retention_policies import list_active

        repo.create_policy(
            db,
            RetentionPolicyCreate(
                retain_days=30,
                severity_filter=["CRITICAL"],
            ),
        )
        repo.create_policy(
            db,
            RetentionPolicyCreate(
                retain_days=60,
                severity_filter=["INFO"],
            ),
        )
        snapshot = list_active(db)
        today_iso = date.today().isoformat()
        details = _build_retention_sweep_details(
            deleted=7, snapshot=snapshot, run_date_msk=today_iso
        )
        assert details["deleted_count"] == 7
        assert details["run_date_msk"] == today_iso
        # legacy «representative» поле отсутствует — иначе SOC увидит одно
        # число при двух разных политиках.
        assert "retain_days" not in details
        assert details["min_retain_days"] == 30
        assert details["max_retain_days"] == 60
        retain_days_in_array = sorted(p["retain_days"] for p in details["policies"])
        assert retain_days_in_array == [30, 60]
        severities = sorted(p["severity"] for p in details["policies"])
        assert severities == ["CRITICAL", "INFO"]

    def test_self_audit_details_empty_snapshot(self, db):
        """Race: snapshot пуст, deleted=0 — детали не должны падать и не
        несут min/max (некорректно было бы выдать min(пустого) → exception)."""
        from src.main import _build_retention_sweep_details

        today_iso = date.today().isoformat()
        details = _build_retention_sweep_details(
            deleted=0, snapshot=[], run_date_msk=today_iso
        )
        assert details == {
            "deleted_count": 0,
            "run_date_msk": today_iso,
            "policies": [],
        }

    def test_self_audit_details_single_global_policy(self, db):
        """Один global-row: массив `policies` всё равно есть, min==max."""
        from src.main import _build_retention_sweep_details
        from src.repositories.retention_policies import list_active

        repo.create(db, RetentionPolicyCreate(retain_days=90))
        snapshot = list_active(db)
        details = _build_retention_sweep_details(
            deleted=3, snapshot=snapshot, run_date_msk=date.today().isoformat()
        )
        assert details["deleted_count"] == 3
        assert details["min_retain_days"] == 90
        assert details["max_retain_days"] == 90
        assert len(details["policies"]) == 1
        assert details["policies"][0]["retain_days"] == 90
        assert details["policies"][0]["severity"] is None
        assert details["policies"][0]["service"] is None

    def test_overlap_unique_count_with_chunking(self, db):
        """Чанкование сохраняется при OR-комбинации: маленький chunk_size →
        несколько проходов, total = уникальные удалённые."""
        repo.create(db, RetentionPolicyCreate(retain_days=30))
        repo.create_policy(
            db,
            RetentionPolicyCreate(
                retain_days=30,
                severity_filter=["CRITICAL"],
                service_filter=["auth_service"],
            ),
        )
        # 25 CRITICAL/auth_service — под обе политики.
        for _ in range(25):
            _add_event(
                db, service="auth_service", severity="CRITICAL", days_ago=100
            )
        # 1 свежее, не должно тронуться.
        fresh = _add_event(
            db, service="auth_service", severity="INFO", days_ago=5
        )
        db.commit()

        deleted = apply_active(db, chunk_size=7)
        assert deleted == 25
        assert _ids(db) == {fresh}

"""Дефолтные severity-правила из БД (миграция #15).

Дефолты переехали из хардкод-таблицы `_DEFAULT_SEVERITY` в `audit_rules`
(`is_default=true`). Проверяем:
  * сид при пустой БД создаёт по правилу на каждую пару `_DEFAULT_SEVERITY`;
  * сид идемпотентен (маркер `seed_state`);
  * удаление дефолта → событие этой пары больше не логируется (drop);
  * повторный сид не воскрешает удалённый дефолт;
  * managed-правило перекрывает дефолт.
"""

from datetime import datetime, timezone

from sqlalchemy import text

from src.models.audit_rule import AuditRule
from src.models.seed_state import SeedState
from src.repositories import rules as rule_repo
from src.schemas.events import EventCreate
from src.schemas.rules import RuleCreate
from src.services import rule_service
from src.services.rule_service import (
    DEFAULT_RULES_SEED_KEY,
    _DEFAULT_SEVERITY,
    apply_rules,
    default_rule_name,
    seed_default_rules,
)


def _event(**kwargs) -> EventCreate:
    base = dict(
        timestamp=datetime.now(timezone.utc),
        service="auth_service",
        action="user.login",
        status="success",
        allowed=True,
        actor_type="user",
        severity=None,
    )
    base.update(kwargs)
    return EventCreate(**base)


def _purge_rules_and_marker(db):
    """Сбрасывает заселённые `db`-фикстурой дефолты — чистый старт под сид."""
    db.execute(text("TRUNCATE TABLE audit_rules, seed_state RESTART IDENTITY CASCADE"))
    db.commit()
    rule_service.invalidate_cache()


# ── Сид при пустой БД ─────────────────────────────────────────────────────────

class TestSeedOnEmptyDb:
    def test_seed_creates_rule_per_default_pair(self, db):
        _purge_rules_and_marker(db)
        created = seed_default_rules(db)
        assert created == len(_DEFAULT_SEVERITY)

        rows = db.execute(
            text("SELECT count(*) FROM audit_rules WHERE is_default = true")
        ).scalar()
        assert rows == len(_DEFAULT_SEVERITY)

    def test_seed_sets_marker(self, db):
        _purge_rules_and_marker(db)
        seed_default_rules(db)
        marker = db.get(SeedState, DEFAULT_RULES_SEED_KEY)
        assert marker is not None

    def test_seeded_rule_severity_matches_table(self, db):
        _purge_rules_and_marker(db)
        seed_default_rules(db)
        # Точечно сверяем пару user.login/failure → CRITICAL.
        row = db.execute(
            text(
                "SELECT effect_severity FROM audit_rules "
                "WHERE match_action = :a AND match_status = :s AND is_default = true"
            ),
            {"a": "user.login", "s": "failure"},
        ).scalar()
        assert row == _DEFAULT_SEVERITY[("user.login", "failure")]


# ── Идемпотентность ───────────────────────────────────────────────────────────

class TestSeedIdempotent:
    def test_second_seed_is_noop(self, db):
        _purge_rules_and_marker(db)
        first = seed_default_rules(db)
        second = seed_default_rules(db)
        assert first == len(_DEFAULT_SEVERITY)
        assert second == 0
        rows = db.execute(
            text("SELECT count(*) FROM audit_rules WHERE is_default = true")
        ).scalar()
        assert rows == len(_DEFAULT_SEVERITY)


# ── Удаление дефолта → drop ───────────────────────────────────────────────────

class TestDeletedDefaultDrops:
    def test_event_logged_while_default_present(self, seeded_db):
        db = seeded_db
        out = apply_rules(db, _event(action="user.login", status="success"))
        assert out is not None
        assert out.severity == "INFO"

    def test_event_dropped_after_default_deleted(self, seeded_db):
        db = seeded_db
        # Удаляем дефолт для (user.login, success) штатным soft-delete.
        rule = (
            db.query(AuditRule)
            .filter(
                AuditRule.match_action == "user.login",
                AuditRule.match_status == "success",
                AuditRule.is_default.is_(True),
            )
            .one()
        )
        rule_repo.delete(db, rule)
        rule_service.invalidate_cache()
        # Нет дефолта, нет managed-правила, нет каталога → drop.
        assert apply_rules(db, _event(action="user.login", status="success")) is None

    def test_other_pairs_unaffected_by_delete(self, seeded_db):
        db = seeded_db
        rule = (
            db.query(AuditRule)
            .filter(
                AuditRule.match_action == "user.login",
                AuditRule.match_status == "success",
                AuditRule.is_default.is_(True),
            )
            .one()
        )
        rule_repo.delete(db, rule)
        rule_service.invalidate_cache()
        # (user.login, failure) дефолт не трогали — событие логируется.
        out = apply_rules(db, _event(action="user.login", status="failure", allowed=False))
        assert out is not None
        assert out.severity == "CRITICAL"


# ── Повторный старт не воскрешает удалённый дефолт ────────────────────────────

class TestReseedDoesNotResurrect:
    def test_reseed_after_delete_keeps_default_gone(self, seeded_db):
        db = seeded_db
        rule = (
            db.query(AuditRule)
            .filter(
                AuditRule.match_action == "user.login",
                AuditRule.match_status == "success",
                AuditRule.is_default.is_(True),
            )
            .one()
        )
        rule_repo.delete(db, rule)
        db.commit()
        rule_service.invalidate_cache()

        # Повторный старт сервиса — маркер `seed_state` стоит, сид no-op.
        created = seed_default_rules(db)
        assert created == 0
        rule_service.invalidate_cache()

        # Дефолт не вернулся → событие по-прежнему дропается.
        assert apply_rules(db, _event(action="user.login", status="success")) is None


# ── Managed-правило перекрывает дефолт ────────────────────────────────────────

class TestManagedOverridesDefault:
    def test_managed_override_wins(self, seeded_db):
        db = seeded_db
        rule_repo.create(
            db,
            RuleCreate(
                name="escalate-login",
                effect="OVERRIDE_SEVERITY",
                effect_severity="CRITICAL",
                priority=100,
                match_action="user.login",
                match_status="success",
            ),
        )
        rule_service.invalidate_cache()
        out = apply_rules(db, _event(action="user.login", status="success"))
        assert out is not None
        # Managed priority=100 перекрывает дефолт priority=0 (INFO).
        assert out.severity == "CRITICAL"

    def test_managed_suppress_over_default(self, seeded_db):
        db = seeded_db
        rule_repo.create(
            db,
            RuleCreate(
                name="suppress-login",
                effect="SUPPRESS",
                priority=100,
                match_action="user.login",
                match_status="success",
            ),
        )
        rule_service.invalidate_cache()
        assert apply_rules(db, _event(action="user.login", status="success")) is None


# ── default_rule_name ─────────────────────────────────────────────────────────

class TestDefaultRuleName:
    def test_deterministic(self):
        assert default_rule_name("user.login", "success") == "default:user.login:success"

    def test_capped_at_128(self):
        long_action = "a" * 200
        assert len(default_rule_name(long_action, "success")) <= 128

"""Coverage gaps W55 P2 — loging_service.

Покрывает три ветки, не задетые предыдущими тестами:

  1. retention.apply_active с chunk_size=1 — максимально частые коммиты,
     повторный цикл `while True` доезжает ровно по одной строке за итерацию.
  2. events.insert backward-compat ветка для row, записанного до миграции
     `j0e1f2a3b4c5` (без `idempotency_payload_hash`): повторный insert
     с тем же ключом должен молча вернуть existing, не падая в 409.
  3. rules.get_all при `AUDIT_QUERY_STATEMENT_TIMEOUT_MS=0` — обходит
     обёртку `_with_statement_timeout` и идёт прямой SELECT/COUNT без
     SAVEPOINT/SET LOCAL.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from src.core.config import get_settings
from src.models.audit_event import AuditEvent
from src.repositories import events as events_repo
from src.repositories import retention_policies as retention_repo
from src.repositories import rules as rules_repo
from src.schemas.events import EventCreate
from src.schemas.retention import RetentionPolicyCreate
from src.schemas.rules import RuleCreate
from src.utils.ids import audit_event_id


def _make_event_payload(
    *,
    service: str = "auth_service",
    action: str = "user.login",
    idempotency_key: str | None = None,
) -> EventCreate:
    return EventCreate(
        timestamp=datetime.now(timezone.utc),
        service=service,
        action=action,
        actor_id="usr_abc123",
        actor_type="user",
        status="success",
        allowed=True,
        severity="INFO",
        idempotency_key=idempotency_key,
    )


# ── 1. apply_active с chunk_size=1 ────────────────────────────────────────────

class TestRetentionChunkSizeOne:
    """`apply_active(chunk_size=1)` — самый дорогой режим: каждый чанк = одна
    строка + commit. Граничный кейс — `deleted < chunk_size` сразу же на
    последней итерации (deleted=0 после исчерпания matching row'ов).
    """

    def _clear_policies(self, db):
        db.execute(text("DELETE FROM retention_policies"))
        db.commit()

    def _add_old_event(self, db, *, service: str = "auth_service") -> str:
        ev = AuditEvent(
            id=audit_event_id(),
            timestamp=datetime.now(timezone.utc) - timedelta(days=400),
            service=service,
            action="user.login",
            actor_type="user",
            status="success",
            allowed=True,
            severity="INFO",
            details={},
        )
        db.add(ev)
        db.flush()
        return ev.id

    def test_chunk_size_one_deletes_each_row_in_separate_commit(self, db):
        self._clear_policies(db)
        try:
            retention_repo.create(db, RetentionPolicyCreate(retain_days=30))
            for _ in range(3):
                self._add_old_event(db)
            db.commit()

            deleted = retention_repo.apply_active(db, chunk_size=1)
            assert deleted == 3

            remaining = db.execute(
                text("SELECT COUNT(*) FROM audit_events WHERE service = 'auth_service'")
            ).scalar_one()
            assert remaining == 0
        finally:
            self._clear_policies(db)

    def test_chunk_size_one_single_matching_row(self, db):
        """Только один row под cutoff'ом — апплай должен сделать ровно
        одну итерацию: deleted=1 на первой, deleted<chunk_size → break.
        """
        self._clear_policies(db)
        try:
            retention_repo.create(db, RetentionPolicyCreate(retain_days=30))
            self._add_old_event(db)
            db.commit()

            deleted = retention_repo.apply_active(db, chunk_size=1)
            assert deleted == 1
        finally:
            self._clear_policies(db)

    def test_chunk_size_one_no_matches_returns_zero(self, db):
        """Активная политика есть, но под cutoff не попадает ни один row."""
        self._clear_policies(db)
        try:
            retention_repo.create(db, RetentionPolicyCreate(retain_days=30))
            # Свежее событие — под retention не попадает.
            ev = AuditEvent(
                id=audit_event_id(),
                timestamp=datetime.now(timezone.utc) - timedelta(days=1),
                service="auth_service",
                action="user.login",
                actor_type="user",
                status="success",
                allowed=True,
                severity="INFO",
                details={},
            )
            db.add(ev)
            db.commit()

            assert retention_repo.apply_active(db, chunk_size=1) == 0
        finally:
            self._clear_policies(db)


# ── 2. events.insert legacy row без hash ──────────────────────────────────────

class TestInsertLegacyRowWithoutPayloadHash:
    """Backward-compat ветка `existing.idempotency_payload_hash is None`.

    Сценарий: до миграции `j0e1f2a3b4c5` колонки `idempotency_payload_hash`
    не было. Старые row'ы, оставшиеся после апгрейда, имеют NULL hash.
    Повторный insert с тем же `(service, idempotency_key)` должен вернуть
    этот legacy row без 409, не сверяя hash.
    """

    def test_legacy_row_without_hash_returns_existing(self, db):
        # Имитируем legacy row через прямой ORM-add без hash.
        legacy = AuditEvent(
            id=audit_event_id(),
            timestamp=datetime.now(timezone.utc),
            service="auth_service",
            action="user.login",
            actor_type="user",
            status="success",
            allowed=True,
            severity="INFO",
            details={},
            idempotency_key="legacy-key-1",
            idempotency_payload_hash=None,  # legacy — до миграции
        )
        db.add(legacy)
        db.commit()
        legacy_id = legacy.id

        # Повторный insert с тем же ключом, но другим payload'ом (другой actor,
        # другой action) — нормально это вернуло бы 409 IDEMPOTENCY_KEY_CONFLICT,
        # но из-за NULL hash должно вернуться existing.
        replay_payload = _make_event_payload(
            service="auth_service",
            action="user.logout",
            idempotency_key="legacy-key-1",
        )
        # actor_id отличается от legacy
        replay = events_repo.insert(db, replay_payload)
        assert replay.id == legacy_id
        # Hash так и остался None — мы не апдейтили legacy row.
        assert replay.idempotency_payload_hash is None

    def test_legacy_row_then_third_replay_still_returns_existing(self, db):
        """Дважды повторённый replay на legacy row остаётся идемпотентным."""
        legacy = AuditEvent(
            id=audit_event_id(),
            timestamp=datetime.now(timezone.utc),
            service="server_service",
            action="server.create",
            actor_type="service",
            status="success",
            allowed=True,
            severity="INFO",
            details={},
            idempotency_key="legacy-key-2",
            idempotency_payload_hash=None,
        )
        db.add(legacy)
        db.commit()

        first = events_repo.insert(
            db,
            _make_event_payload(
                service="server_service",
                action="server.delete",
                idempotency_key="legacy-key-2",
            ),
        )
        second = events_repo.insert(
            db,
            _make_event_payload(
                service="server_service",
                action="server.update",
                idempotency_key="legacy-key-2",
            ),
        )
        assert first.id == legacy.id
        assert second.id == legacy.id


# ── 3. rules.get_all с timeout=0 ──────────────────────────────────────────────

class TestRulesGetAllTimeoutZero:
    """`AUDIT_QUERY_STATEMENT_TIMEOUT_MS=0` для rules.get_all идёт через
    else-ветку без savepoint/SET LOCAL — симметрично events.query.
    """

    def test_rules_timeout_zero_no_set_local(self, db, monkeypatch):
        monkeypatch.setenv("AUDIT_QUERY_STATEMENT_TIMEOUT_MS", "0")
        get_settings.cache_clear()
        try:
            rules_repo.create(
                db,
                RuleCreate(name="rule-no-timeout", effect="SUPPRESS", priority=50),
            )
            db.commit()

            set_local_calls: list[str] = []
            original_execute = db.execute

            def spy(clause, *args, **kwargs):
                sql_text = str(getattr(clause, "text", clause))
                if "set local statement_timeout" in sql_text.lower():
                    set_local_calls.append(sql_text)
                return original_execute(clause, *args, **kwargs)

            monkeypatch.setattr(db, "execute", spy)

            rules_list, total = rules_repo.get_all(db, limit=10, offset=0)
            assert total == 1
            assert len(rules_list) == 1
            assert rules_list[0].name == "rule-no-timeout"

            assert set_local_calls == [], (
                "rules.get_all с timeout=0 не должен выпускать SET LOCAL"
            )
        finally:
            get_settings.cache_clear()

    def test_rules_timeout_zero_returns_empty_page_on_empty_db(self, db, monkeypatch):
        """Граница: timeout=0, нет правил — count=0, list=[]."""
        monkeypatch.setenv("AUDIT_QUERY_STATEMENT_TIMEOUT_MS", "0")
        get_settings.cache_clear()
        try:
            rules_list, total = rules_repo.get_all(db, limit=10, offset=0)
            assert total == 0
            assert rules_list == []
        finally:
            get_settings.cache_clear()

    def test_rules_timeout_positive_does_emit_set_local(self, db, monkeypatch):
        """Симметричный sanity-check: при положительном timeout SET LOCAL
        присутствует (защита от регрессии в обе стороны).
        """
        monkeypatch.setenv("AUDIT_QUERY_STATEMENT_TIMEOUT_MS", "5000")
        get_settings.cache_clear()
        try:
            rules_repo.create(
                db,
                RuleCreate(name="rule-with-timeout", effect="SUPPRESS", priority=50),
            )
            db.commit()

            set_local_calls: list[str] = []
            original_execute = db.execute

            def spy(clause, *args, **kwargs):
                sql_text = str(getattr(clause, "text", clause))
                if "set local statement_timeout" in sql_text.lower():
                    set_local_calls.append(sql_text)
                return original_execute(clause, *args, **kwargs)

            monkeypatch.setattr(db, "execute", spy)

            rules_list, total = rules_repo.get_all(db, limit=10, offset=0)
            assert total == 1
            assert len(rules_list) == 1
            # COUNT + SELECT — два SET LOCAL.
            assert len(set_local_calls) >= 1
        finally:
            get_settings.cache_clear()


# ── 4. _build_retention_sweep_details с cutoff_at ─────────────────────────────

class TestRetentionSweepDetailsCutoffAt:
    """`_build_retention_sweep_details` теперь принимает `cutoff_at` и
    кладёт его в audit-details. Поле опциональное (backward-compat).
    """

    def test_cutoff_at_included_when_provided(self):
        from src.main import _build_retention_sweep_details

        cutoff = datetime(2026, 6, 5, 12, 30, 45, tzinfo=timezone.utc)
        details = _build_retention_sweep_details(
            deleted=5,
            snapshot=[],
            run_date_msk="2026-06-05",
            cutoff_at=cutoff,
        )
        assert details["cutoff_at"] == "2026-06-05T12:30:45+00:00"
        assert details["deleted_count"] == 5

    def test_cutoff_at_omitted_when_none(self):
        from src.main import _build_retention_sweep_details

        details = _build_retention_sweep_details(
            deleted=0,
            snapshot=[],
            run_date_msk="2026-06-05",
        )
        assert "cutoff_at" not in details

    def test_cutoff_at_with_multi_policy_snapshot(self):
        from src.main import _build_retention_sweep_details
        from src.models.retention_policy import RetentionPolicy

        cutoff = datetime(2026, 6, 5, 0, 0, 0, tzinfo=timezone.utc)
        snapshot = [
            RetentionPolicy(id="ret_1", retain_days=30, severity=None, service=None),
            RetentionPolicy(id="ret_2", retain_days=90, severity="WARNING", service=None),
        ]
        details = _build_retention_sweep_details(
            deleted=12,
            snapshot=snapshot,
            run_date_msk="2026-06-05",
            cutoff_at=cutoff,
        )
        assert details["cutoff_at"] == "2026-06-05T00:00:00+00:00"
        assert details["min_retain_days"] == 30
        assert details["max_retain_days"] == 90
        assert len(details["policies"]) == 2


# ── 5. _validate_match_action отбивает self-audit globs ───────────────────────

class TestValidateMatchActionSelfAuditGuard:
    """`_validate_match_action` теперь возвращает 422 на `logging.*`/`audit.*`
    и любые их варианты — self-audit loging_service всегда обходит rule
    engine, такие правила бесполезны и сбивают с толку.
    """

    def test_logging_glob_rejected(self, admin_client):
        from tests.conftest import make_rule

        r = admin_client.post(
            "/api/logging/v1/rules",
            json=make_rule(name="bad-logging-glob", match_action="logging.*"),
        )
        assert r.status_code == 422, r.text
        body = r.json()
        assert body["error_code"] == "UNKNOWN_MATCH_ACTION"
        assert "self-audit" in body["message"].lower()

    def test_audit_glob_rejected(self, admin_client):
        from tests.conftest import make_rule

        r = admin_client.post(
            "/api/logging/v1/rules",
            json=make_rule(name="bad-audit-glob", match_action="audit.*"),
        )
        assert r.status_code == 422
        assert r.json()["error_code"] == "UNKNOWN_MATCH_ACTION"

    def test_logging_exact_action_rejected(self, admin_client):
        """Точное имя `logging.retention_sweep` тоже отбивается."""
        from tests.conftest import make_rule

        r = admin_client.post(
            "/api/logging/v1/rules",
            json=make_rule(
                name="bad-logging-exact",
                match_action="logging.retention_sweep",
            ),
        )
        assert r.status_code == 422

    def test_logging_rule_glob_rejected(self, admin_client):
        """`logging_rule.*` тоже self-audit (см. _audit в rules.py)."""
        from tests.conftest import make_rule

        r = admin_client.post(
            "/api/logging/v1/rules",
            json=make_rule(name="bad-rule-glob", match_action="logging_rule.*"),
        )
        assert r.status_code == 422
        assert r.json()["error_code"] == "UNKNOWN_MATCH_ACTION"

    def test_user_glob_still_accepted(self, admin_client):
        """`user.*` — бизнес-action, проходит как раньше."""
        from tests.conftest import make_rule

        r = admin_client.post(
            "/api/logging/v1/rules",
            json=make_rule(name="user-glob-ok", match_action="user.*"),
        )
        assert r.status_code in (200, 201)

"""Точечные unit'ы под четыре фикса в loging_service.

1) `endpoints/events.py::list_events` — GET /events под `audit_query_rate_limit`,
   bursting reader-канал больше не выжимает pgsql-пул.
2) `endpoints/services.py::register_events` — self-audit
   `logging.service_events_registered` пишется в той же транзакции, что и
   upsert каталога.
3) `dependencies/auth.py::_fetch_identity` — на неожиданном исключении
   transport-layer'а сообщение наружу константное, имя класса остаётся в логе.
4) `endpoints/rules.py::update_rule` — IntegrityError мапится через общий
   `_is_unique_violation`-helper, симметрично `create_rule`; pgcode ≠ 23505 → 500.
"""

from __future__ import annotations

import httpx

from src.repositories import events as events_repo
from tests.conftest import make_event_def, make_rule


EVENTS_URL = "/api/logging/v1/events"
RULES_URL = "/api/logging/v1/rules"
SERVICES_URL = "/api/logging/v1/services"


# ── Fix 1: GET /events под audit_query_rate_limit ──────────────────────────


class TestListEventsRateLimit:
    def test_burst_triggers_429(self, admin_client, monkeypatch):
        """Override на 3/minute → 4-й GET /events отбивается 429.

        До фикса list_events не имел `@limiter.limit`, и читатель мог 60+
        широкими SELECT'ами выжать пул соединений.
        """
        monkeypatch.setenv("AUDIT_QUERY_RATE_LIMIT", "3/minute")
        from src.core.config import get_settings
        get_settings.cache_clear()
        from src.main import limiter
        limiter.reset()

        for i in range(3):
            r = admin_client.get(EVENTS_URL)
            assert r.status_code == 200, (
                f"GET /events #{i} got {r.status_code}: {r.text}"
            )

        r = admin_client.get(EVENTS_URL)
        assert r.status_code == 429, (
            f"expected 429, got {r.status_code}: {r.text}"
        )
        body = r.json()
        assert body["error_code"] == "RATE_LIMIT_EXCEEDED"


# ── Fix 2: register_events пишет self-audit ──────────────────────────────────


class TestRegisterEventsWritesAudit:
    def test_audit_row_created_with_service_actor(self, client, db, auth_headers):
        """POST /services/{svc}/events → audit-row `logging.service_events_registered`
        с `actor_type=service`, `actor_id=<X-Service-Identity>`,
        `target_id=<service>` и details-счётчиками.
        """
        headers = {**auth_headers, "X-Service-Identity": "auth_service"}
        payload = {
            "events": [
                make_event_def(action="user.login"),
                make_event_def(action="user.logout"),
            ]
        }
        r = client.post(
            f"{SERVICES_URL}/auth_service/events",
            json=payload,
            headers=headers,
        )
        assert r.status_code == 200, r.text

        # Ищем self-audit row напрямую через repo, минуя GET /events
        # (тот сам себя audit'ит через middleware и засорил бы выборку).
        rows, _, _ = events_repo.query(
            db,
            service="loging_service",
            action="logging.service_events_registered",
            include_total=False,
        )
        assert len(rows) == 1, (
            f"expected exactly one self-audit row, got {len(rows)}"
        )
        row = rows[0]
        assert row.actor_type == "service"
        assert row.actor_id == "auth_service"
        assert row.target_id == "auth_service"
        assert row.target_type == "service_event"
        assert row.status == "success"
        assert row.allowed is True
        # severity подтягивается из `_DEFAULT_SEVERITY` (INFO для local action).
        assert row.severity == "INFO"
        assert row.details["service"] == "auth_service"
        assert row.details["added"] == 2
        assert row.details["updated"] == 0
        assert row.details["total"] == 2

# ── Fix 3: introspect transport-exception не светит имя класса ─────────────


class TestIntrospectExceptionClassNotLeaked:
    def test_transport_error_message_is_constant(self, client, mock_introspect, caplog):
        """`httpx.RemoteProtocolError` (broken transport) → 503 с
        константной фразой `Authentication service error`. Имя класса в
        теле не утекает, попадает только в WARNING-лог.
        """
        import logging

        with caplog.at_level(logging.WARNING, logger="src.dependencies.auth"):
            with mock_introspect(side_effect=httpx.RemoteProtocolError("broken")):
                r = client.get(EVENTS_URL, headers={"Authorization": "Bearer x"})

        assert r.status_code == 503
        body = r.json()
        assert body["error_code"] == "AUTH_SERVICE_ERROR"
        assert body["message"] == "Authentication service error"
        # Никаких имён исключений в теле наружу.
        assert "RemoteProtocolError" not in body["message"]
        assert "Unexpected" not in body["message"]
        # Class name в лог попало.
        log_text = " ".join(rec.getMessage() for rec in caplog.records)
        assert "RemoteProtocolError" in log_text


# ── Fix 4: update_rule отличает UNIQUE от прочих IntegrityError ─────────────


class TestUpdateRuleIntegrityError:
    def test_non_unique_integrity_error_returns_500_internal(
        self, admin_client, monkeypatch
    ):
        """FK/NOT NULL/CHECK violation на PATCH → 500 INTERNAL_ERROR.

        Раньше эвристика `payload.name is not None` рулила выбор 409 vs 500;
        теперь pgcode — единственный сигнал. Шлём rename (payload.name='x'),
        но возвращаем pgcode 23502 → 500, а не 409.
        """
        from sqlalchemy.exc import IntegrityError

        from src.api.v1.endpoints import rules as rules_endpoint

        # Сначала создаём правило, которое будем апдейтить.
        created = admin_client.post(RULES_URL, json=make_rule(name="orig"))
        assert created.status_code == 201
        rule_id = created.json()["id"]

        class _Orig:
            pgcode = "23502"  # NOT NULL violation

        def _boom(db, rule, payload, *, commit=True):
            raise IntegrityError("simulated NOT NULL", params=None, orig=_Orig())

        monkeypatch.setattr(rules_endpoint.rule_repo, "update", _boom)

        r = admin_client.patch(
            f"{RULES_URL}/{rule_id}",
            json={"name": "renamed"},
        )
        assert r.status_code == 500, r.text
        body = r.json()
        assert body["error_code"] == "INTERNAL_ERROR"
        assert "already exists" not in body["message"].lower()

    def test_unique_violation_with_rename_returns_409(
        self, admin_client, monkeypatch
    ):
        """Настоящий UNIQUE-конфликт на rename → 409 RULE_NAME_CONFLICT
        с именем из payload.
        """
        from sqlalchemy.exc import IntegrityError

        from src.api.v1.endpoints import rules as rules_endpoint

        created = admin_client.post(RULES_URL, json=make_rule(name="src"))
        assert created.status_code == 201
        rule_id = created.json()["id"]

        class _Orig:
            pgcode = "23505"

        def _boom(db, rule, payload, *, commit=True):
            raise IntegrityError("simulated unique", params=None, orig=_Orig())

        monkeypatch.setattr(rules_endpoint.rule_repo, "update", _boom)

        r = admin_client.patch(
            f"{RULES_URL}/{rule_id}",
            json={"name": "dup"},
        )
        assert r.status_code == 409, r.text
        body = r.json()
        assert body["error_code"] == "RULE_NAME_CONFLICT"
        assert "dup" in body["message"]

    def test_unique_violation_without_rename_uses_existing_name(
        self, admin_client, monkeypatch
    ):
        """UNIQUE-конфликт при PATCH без rename — теоретически странный
        случай (UNIQUE только на `name`), но если приедет — 409 с именем
        из БД, а не падение в 500 на эвристике `payload.name is None`.
        """
        from sqlalchemy.exc import IntegrityError

        from src.api.v1.endpoints import rules as rules_endpoint

        created = admin_client.post(RULES_URL, json=make_rule(name="kept"))
        assert created.status_code == 201
        rule_id = created.json()["id"]

        class _Orig:
            pgcode = "23505"

        def _boom(db, rule, payload, *, commit=True):
            raise IntegrityError("simulated unique", params=None, orig=_Orig())

        monkeypatch.setattr(rules_endpoint.rule_repo, "update", _boom)

        r = admin_client.patch(
            f"{RULES_URL}/{rule_id}",
            json={"priority": 99},  # name НЕ в payload'е
        )
        assert r.status_code == 409, r.text
        body = r.json()
        assert body["error_code"] == "RULE_NAME_CONFLICT"
        # Имя берётся из БД, не из payload'а (которого нет).
        assert "kept" in body["message"]

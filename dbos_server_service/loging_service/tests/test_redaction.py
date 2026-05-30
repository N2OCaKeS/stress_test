"""Тесты defense-in-depth маскировки: `utils/redaction.py` + хук в event_service.record.

Сценарий: даже если отправитель забыл санитизировать payload, loging_service
сам заменит password/token/secret/hash на типизированные плейсхолдеры перед сохранением.
"""

import pytest

from src.models.audit_event import AuditEvent
from src.utils.redaction import redact

from tests.conftest import make_event

EVENTS_URL = "/api/logging/v1/events"


# ── Юнит-тесты redact() ───────────────────────────────────────────────────────


class TestRedactKeyBased:
    def test_password_key(self):
        assert redact({"password": "p"}) == {"password": "<PASSWORD>"}

    def test_token_keys(self):
        for k in ("token", "access_token", "refresh_token", "bearer", "jwt"):
            assert redact({k: "v"})[k] == "<TOKEN>"

    def test_secret_keys(self):
        for k in ("secret", "api_key", "client_secret", "private_key"):
            assert redact({k: "v"})[k] == "<SECRET>"

    def test_secret_keys_cover_s2s_ingest_names(self):
        # Auth/server-сервисы аудитят rotate-операции с полями
        # `service_api_key` / `service_key` / `introspect_key` в `details`;
        # без exact-set lookup'а secret уезжал бы в БД в plaintext.
        for k in ("service_api_key", "service_key", "introspect_key"):
            assert redact({k: "rotated-secret-value"})[k] == "<SECRET>"

    def test_hash_keys(self):
        for k in ("password_hash", "hash", "token_hash"):
            assert redact({k: "v"})[k] == "<HASH>"

    def test_credential_keys(self):
        for k in ("credential", "authorization", "auth"):
            assert redact({k: "v"})[k] == "<CREDENTIAL>"


class TestRedactValueBased:
    def test_jwt_value(self):
        out = redact({"info": "eyJabcdefgh.eyJabcdefgh.signature1234"})
        assert out["info"] == "<TOKEN>"

    def test_argon2_value(self):
        out = redact({"info": "$argon2id$v=19$m=65536,t=3,p=4$abc"})
        assert out["info"] == "<HASH>"

    def test_opaque_pat_token_masked(self):
        out = redact({"info": "dbos_pat_aBcD3fGhIjKlMnOp"})
        assert out == {"info": "<TOKEN>"}

    def test_opaque_bot_token_masked(self):
        out = redact({"info": "dbos_bot_xY9z8wV7uT6sR5qP"})
        assert out == {"info": "<TOKEN>"}

    def test_plain_string_passthrough(self):
        out = redact({"reason": "invalid"})
        assert out == {"reason": "invalid"}


class TestRedactRecursion:
    def test_nested(self):
        assert redact({"u": {"password": "x"}}) == {"u": {"password": "<PASSWORD>"}}

    def test_list_of_dicts(self):
        out = redact({"tokens": [{"token": "a"}, {"token": "b"}]})
        # ключ "tokens" — список, не подходящий к маскированию по имени → внутрь
        assert out == {"tokens": [{"token": "<TOKEN>"}, {"token": "<TOKEN>"}]}


class TestRedactLongStrings:
    def test_truncated(self):
        out = redact({"blob": "x" * 5000})
        assert out["blob"].endswith("<TRUNCATED>")
        assert len(out["blob"]) <= 2100


# ── Интеграция через POST /events ────────────────────────────────────────────


class TestRedactionViaIngest:
    def test_password_in_details_masked_on_ingest(self, client, auth_headers, db):
        r = client.post(EVENTS_URL,
                        json=make_event(details={"reason": "wrong", "password": "p@ss"}),
                        headers=auth_headers)
        assert r.status_code == 201
        stored = db.get(AuditEvent, r.json()["id"])
        assert stored.details["password"] == "<PASSWORD>"
        assert stored.details["reason"] == "wrong"

    def test_nested_token_masked(self, client, auth_headers, db):
        r = client.post(EVENTS_URL,
                        json=make_event(details={
                            "session": {"id": "s1", "refresh_token": "rt_xxx"}
                        }),
                        headers=auth_headers)
        assert r.status_code == 201
        stored = db.get(AuditEvent, r.json()["id"])
        assert stored.details["session"]["refresh_token"] == "<TOKEN>"
        assert stored.details["session"]["id"] == "s1"

    def test_jwt_shaped_value_masked(self, client, auth_headers, db):
        jwt_like = "eyJabcdefgh.eyJabcdefgh.signaturepart"
        r = client.post(EVENTS_URL,
                        json=make_event(details={"info": jwt_like}),
                        headers=auth_headers)
        stored = db.get(AuditEvent, r.json()["id"])
        assert stored.details["info"] == "<TOKEN>"

    def test_clean_details_pass_through(self, client, auth_headers, db):
        r = client.post(EVENTS_URL,
                        json=make_event(details={"reason": "ok", "attempts": 3}),
                        headers=auth_headers)
        stored = db.get(AuditEvent, r.json()["id"])
        assert stored.details == {"reason": "ok", "attempts": 3}

    def test_argon2_hash_in_value_masked(self, client, auth_headers, db):
        r = client.post(EVENTS_URL,
                        json=make_event(details={"leaked": "$argon2id$v=19$m=65536$abc"}),
                        headers=auth_headers)
        stored = db.get(AuditEvent, r.json()["id"])
        assert stored.details["leaked"] == "<HASH>"

    def test_credentials_dict_masked_entirely(self, client, auth_headers, db):
        r = client.post(EVENTS_URL,
                        json=make_event(details={
                            "credentials": {"user": "x", "pass": "y"},
                            "other": "ok",
                        }),
                        headers=auth_headers)
        stored = db.get(AuditEvent, r.json()["id"])
        assert stored.details["credentials"] == "<CREDENTIAL>"
        assert stored.details["other"] == "ok"


# ── record_admin_action тоже маскирует ───────────────────────────────────────


class TestRedactionAdminAction:
    def test_admin_action_masks_details(self, db):
        from datetime import datetime, timezone
        from src.schemas.events import EventCreate
        from src.services.event_service import record_admin_action
        ev = record_admin_action(db, EventCreate(
            timestamp=datetime.now(timezone.utc),
            service="loging_service",
            action="logging_rule.create",
            actor_id="usr_admin",
            actor_type="user",
            status="success",
            allowed=True,
            details={"new_password": "leaked", "rule_id": "rl_1"},
        ))
        assert ev.details["new_password"] == "<PASSWORD>"
        assert ev.details["rule_id"] == "rl_1"

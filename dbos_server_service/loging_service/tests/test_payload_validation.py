"""Тесты валидации payload событий: размер details, длины строк, actor_type enum,
сохранение всех опциональных полей ingest (username/target_id/target_type/request_id).

Покрывают src/schemas/events.py поля и валидаторы, которые ранее проверялись
лишь частично (action max_length, severity, status).
"""

import json

from tests.conftest import make_event

EVENTS_URL = "/api/logging/v1/events"


# ── details: лимит 64 KB ──────────────────────────────────────────────────────

class TestDetailsSizeLimit:
    def test_details_just_below_limit_accepted(self, client, auth_headers):
        # JSON-сериализованный размер < 65 536
        big_value = "a" * 60_000
        payload = make_event(details={"blob": big_value})
        assert len(json.dumps(payload["details"])) < 65_536
        r = client.post(EVENTS_URL, json=payload, headers=auth_headers)
        assert r.status_code == 201

    def test_details_above_limit_rejected(self, client, auth_headers):
        # > 64 KB после json.dumps → 422 от кастомного валидатора
        big_value = "a" * 70_000
        payload = make_event(details={"blob": big_value})
        assert len(json.dumps(payload["details"])) > 65_536
        r = client.post(EVENTS_URL, json=payload, headers=auth_headers)
        assert r.status_code == 422
        # Сообщение из валидатора попадает в details.errors[0].msg
        body = r.json()
        assert "details" in str(body).lower() or "64" in str(body)

    def test_details_default_empty_dict_accepted(self, client, auth_headers):
        payload = make_event()
        payload.pop("details", None)  # not present
        r = client.post(EVENTS_URL, json=payload, headers=auth_headers)
        assert r.status_code == 201

    def test_details_with_nested_structure(self, client, auth_headers):
        payload = make_event(details={
            "user": {"id": "u_1", "ip": "1.2.3.4"},
            "tags": ["a", "b", "c"],
            "count": 42,
            "ok": True,
            "ratio": 0.5,
        })
        r = client.post(EVENTS_URL, json=payload, headers=auth_headers)
        assert r.status_code == 201


# ── max_length: остальные строковые поля ─────────────────────────────────────

class TestStringLengthBounds:
    def test_service_max_length_64(self, client, auth_headers):
        r = client.post(EVENTS_URL,
                        json=make_event(service="s" * 64), headers=auth_headers)
        assert r.status_code == 201
        r = client.post(EVENTS_URL,
                        json=make_event(service="s" * 65), headers=auth_headers)
        assert r.status_code == 422

    def test_action_max_length_128(self, client, auth_headers):
        r = client.post(EVENTS_URL,
                        json=make_event(action="a" * 128), headers=auth_headers)
        assert r.status_code == 201
        r = client.post(EVENTS_URL,
                        json=make_event(action="a" * 129), headers=auth_headers)
        assert r.status_code == 422

    def test_actor_id_max_length_48(self, client, auth_headers):
        r = client.post(EVENTS_URL,
                        json=make_event(actor_id="u" * 48), headers=auth_headers)
        assert r.status_code == 201
        r = client.post(EVENTS_URL,
                        json=make_event(actor_id="u" * 49), headers=auth_headers)
        assert r.status_code == 422

    def test_username_max_length_128(self, client, auth_headers):
        r = client.post(EVENTS_URL,
                        json=make_event(username="n" * 128), headers=auth_headers)
        assert r.status_code == 201
        r = client.post(EVENTS_URL,
                        json=make_event(username="n" * 129), headers=auth_headers)
        assert r.status_code == 422

    def test_department_id_max_length_48(self, client, auth_headers):
        r = client.post(EVENTS_URL,
                        json=make_event(department_id="d" * 49), headers=auth_headers)
        assert r.status_code == 422

    def test_target_id_max_length_48(self, client, auth_headers):
        r = client.post(EVENTS_URL,
                        json=make_event(target_id="t" * 49), headers=auth_headers)
        assert r.status_code == 422

    def test_target_type_max_length_64(self, client, auth_headers):
        r = client.post(EVENTS_URL,
                        json=make_event(target_type="t" * 65), headers=auth_headers)
        assert r.status_code == 422

    def test_request_id_max_length_64(self, client, auth_headers):
        r = client.post(EVENTS_URL,
                        json=make_event(request_id="r" * 64), headers=auth_headers)
        assert r.status_code == 201
        r = client.post(EVENTS_URL,
                        json=make_event(request_id="r" * 65), headers=auth_headers)
        assert r.status_code == 422


# ── actor_type: все значения Literal ─────────────────────────────────────────

class TestActorTypeEnum:
    def test_user(self, client, auth_headers):
        r = client.post(EVENTS_URL,
                        json=make_event(actor_type="user"), headers=auth_headers)
        assert r.status_code == 201

    def test_bot(self, client, auth_headers):
        r = client.post(EVENTS_URL,
                        json=make_event(actor_type="bot"), headers=auth_headers)
        assert r.status_code == 201

    def test_service(self, client, auth_headers):
        r = client.post(EVENTS_URL,
                        json=make_event(actor_type="service"), headers=auth_headers)
        assert r.status_code == 201

    def test_anonymous(self, client, auth_headers):
        # Когда нет actor_id — actor_type=anonymous имеет смысл
        payload = make_event(actor_type="anonymous")
        payload.pop("actor_id", None)
        r = client.post(EVENTS_URL, json=payload, headers=auth_headers)
        assert r.status_code == 201

    def test_invalid_actor_type_rejected(self, client, auth_headers):
        r = client.post(EVENTS_URL,
                        json=make_event(actor_type="robot"), headers=auth_headers)
        assert r.status_code == 422


# ── Сохранение опциональных полей ─────────────────────────────────────────────

class TestIngestStoresAllFields:
    def test_username_stored(self, client, admin_client, auth_headers, db):
        r = client.post(
            EVENTS_URL,
            json=make_event(username="ivanov_ii"),
            headers=auth_headers,
        )
        event_id = r.json()["id"]
        from src.models.audit_event import AuditEvent
        stored = db.get(AuditEvent, event_id)
        assert stored.username == "ivanov_ii"

    def test_target_id_and_type_stored(self, client, auth_headers, db):
        r = client.post(
            EVENTS_URL,
            json=make_event(target_id="usr_target_42", target_type="user"),
            headers=auth_headers,
        )
        from src.models.audit_event import AuditEvent
        stored = db.get(AuditEvent, r.json()["id"])
        assert stored.target_id == "usr_target_42"
        assert stored.target_type == "user"

    def test_request_id_stored(self, client, auth_headers, db):
        r = client.post(
            EVENTS_URL,
            json=make_event(request_id="req_abc123"),
            headers=auth_headers,
        )
        from src.models.audit_event import AuditEvent
        stored = db.get(AuditEvent, r.json()["id"])
        assert stored.request_id == "req_abc123"

    def test_null_optional_fields_stored_as_null(self, client, auth_headers, db):
        # Без явных actor_id/username/target_*/request_id — в БД должно быть None
        payload = {
            "timestamp": "2026-04-19T10:00:00Z",
            "service": "svc",
            "action": "test.action",
            "status": "success",
            "allowed": True,
        }
        r = client.post(EVENTS_URL, json=payload, headers=auth_headers)
        from src.models.audit_event import AuditEvent
        stored = db.get(AuditEvent, r.json()["id"])
        assert stored.actor_id is None
        assert stored.username is None
        assert stored.target_id is None
        assert stored.target_type is None
        assert stored.request_id is None
        assert stored.department_id is None

"""Тесты: POST /api/logging/v1/events — приём и сохранение событий аудита."""

import pytest
from tests.conftest import make_event


class TestIngestAuth:
    def test_missing_token_returns_401(self, client):
        resp = client.post("/api/logging/v1/events", json=make_event())
        assert resp.status_code == 401

    def test_wrong_token_returns_401(self, client):
        resp = client.post(
            "/api/logging/v1/events",
            json=make_event(),
            headers={"Authorization": "Bearer wrong-key"},
        )
        assert resp.status_code == 401

    def test_valid_token_accepted(self, client, auth_headers):
        resp = client.post("/api/logging/v1/events", json=make_event(), headers=auth_headers)
        assert resp.status_code == 201


class TestIngestPayload:
    def test_returns_id_and_received_at(self, client, auth_headers):
        resp = client.post("/api/logging/v1/events", json=make_event(), headers=auth_headers)
        body = resp.json()
        assert body["id"].startswith("log_")
        assert "received_at" in body

    def test_minimal_event_defaults(self, client, auth_headers):
        payload = {
            "timestamp": "2026-04-19T10:00:00Z",
            "service": "config_service",
            "action": "secret.read",
            "status": "success",
            "allowed": True,
        }
        resp = client.post("/api/logging/v1/events", json=payload, headers=auth_headers)
        assert resp.status_code == 201

    def test_all_severity_levels_accepted(self, client, auth_headers):
        for severity in ("TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            resp = client.post(
                "/api/logging/v1/events",
                json=make_event(severity=severity),
                headers=auth_headers,
            )
            assert resp.status_code == 201, f"failed for severity={severity}"

    def test_invalid_severity_rejected(self, client, auth_headers):
        resp = client.post(
            "/api/logging/v1/events",
            json=make_event(severity="VERBOSE"),
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_invalid_status_rejected(self, client, auth_headers):
        resp = client.post(
            "/api/logging/v1/events",
            json=make_event(status="fail"),
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_denied_event(self, client, auth_headers):
        resp = client.post(
            "/api/logging/v1/events",
            json=make_event(status="denied", allowed=False, severity="WARNING"),
            headers=auth_headers,
        )
        assert resp.status_code == 201

    def test_details_stored(self, client, auth_headers, db):
        from src.repositories.events import query as repo_query
        payload = make_event(details={"reason": "invalid_password", "attempts": 3})
        resp = client.post("/api/logging/v1/events", json=payload, headers=auth_headers)
        assert resp.status_code == 201
        event_id = resp.json()["id"]
        from src.models.audit_event import AuditEvent
        stored = db.get(AuditEvent, event_id)
        assert stored.details["reason"] == "invalid_password"
        assert stored.details["attempts"] == 3


class TestHealthProbes:
    def test_health_no_auth(self, client):
        resp = client.get("/api/logging/v1/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_ready_no_auth(self, client):
        resp = client.get("/api/logging/v1/ready")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ready"

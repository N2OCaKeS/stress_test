"""Тесты: GET /api/logging/v1/internal/events — s2s read-канал.

Internal read-эндпоинт аутентифицирует по `SERVICE_API_KEYS` (как ingest),
а не по user-bearer'у с ролью `loging_admin`/`loging_reader`. Нужен, чтобы
доверенный сервис (server_service) читал свой срез аудита — например,
drift-события для `GET /servers/{id}/drift`.

Покрываем:

* happy-path: server_service-key + `X-Service-Identity` → 200 с событиями;
* фильтр по `action` + `target_id` сужает выборку;
* `service` и `action` обязательны — без любого из них → 422;
* нет токена → 401;
* неверный ключ → 401;
* user-роль (admin/reader) сюда не нужна — путь чисто service-to-service.
"""

from datetime import datetime, timedelta, timezone

from tests.conftest import headers_for, make_event

INTERNAL = "/api/logging/v1/internal/events"


def _ingest(client, service: str, **kwargs):
    payload = make_event(service=service, **kwargs)
    headers = headers_for(service)
    resp = client.post("/api/logging/v1/events", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


class TestInternalEventsAuth:
    def test_missing_token_returns_401(self, client):
        assert client.get(INTERNAL).status_code == 401

    def test_wrong_key_returns_401(self, client):
        resp = client.get(
            INTERNAL,
            headers={
                "Authorization": "Bearer wrong-key",
                "X-Service-Identity": "server_service",
            },
        )
        assert resp.status_code == 401

    def test_missing_identity_returns_401(self, client, auth_headers):
        resp = client.get(
            INTERNAL,
            headers={"Authorization": auth_headers["Authorization"]},
        )
        assert resp.status_code == 401

    def test_valid_service_key_accepted(self, client):
        resp = client.get(
            INTERNAL,
            params={"service": "server_service", "action": "server_account.drift_detected"},
            headers=headers_for("server_service"),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["items"] == []


class TestInternalEventsRequiredFilter:
    def test_no_filter_returns_422(self, client):
        resp = client.get(INTERNAL, headers=headers_for("server_service"))
        assert resp.status_code == 422
        assert resp.json()["error_code"] == "VALIDATION_ERROR"

    def test_missing_action_returns_422(self, client):
        resp = client.get(
            INTERNAL,
            params={"service": "server_service"},
            headers=headers_for("server_service"),
        )
        assert resp.status_code == 422
        assert resp.json()["error_code"] == "VALIDATION_ERROR"

    def test_missing_service_returns_422(self, client):
        resp = client.get(
            INTERNAL,
            params={"action": "server_account.drift_detected"},
            headers=headers_for("server_service"),
        )
        assert resp.status_code == 422
        assert resp.json()["error_code"] == "VALIDATION_ERROR"


class TestInternalEventsRead:
    def test_returns_ingested_events(self, client):
        _ingest(client, "server_service", action="server_account.drift_detected")
        _ingest(client, "server_service", action="server_account.drift_detected")
        body = client.get(
            INTERNAL,
            params={"service": "server_service", "action": "server_account.drift_detected"},
            headers=headers_for("server_service"),
        ).json()
        assert len(body["items"]) == 2

    def test_filter_by_action_and_target(self, client):
        _ingest(
            client, "server_service",
            action="server_account.drift_detected", target_id="srv_a",
        )
        _ingest(
            client, "server_service",
            action="server_account.drift_detected", target_id="srv_b",
        )
        _ingest(
            client, "server_service",
            action="server.power_on", target_id="srv_a",
        )
        body = client.get(
            INTERNAL,
            params={
                "service": "server_service",
                "action": "server_account.drift_detected",
                "target_id": "srv_a",
            },
            headers=headers_for("server_service"),
        ).json()
        assert len(body["items"]) == 1
        assert body["items"][0]["target_id"] == "srv_a"
        assert body["items"][0]["action"] == "server_account.drift_detected"

    def test_empty_window_returns_empty_list(self, client):
        _ingest(client, "server_service", action="server_account.drift_detected")
        future = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
        body = client.get(
            INTERNAL,
            params={
                "service": "server_service",
                "action": "server_account.drift_detected",
                "from_time": future,
            },
            headers=headers_for("server_service"),
        ).json()
        assert body["items"] == []

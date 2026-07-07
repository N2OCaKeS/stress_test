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


class TestInternalEventsIdentityScope:
    """`GET /internal/events` привязан к identity caller'а: сервис читает
    только собственный срез. Держатель любого internal-ключа не может
    прочитать аудит чужого сервиса, подставив `service=<чужой>` в query.
    """

    def test_own_service_read_allowed(self, client):
        _ingest(client, "server_service", action="server_account.drift_detected")
        resp = client.get(
            INTERNAL,
            params={"service": "server_service", "action": "server_account.drift_detected"},
            headers=headers_for("server_service"),
        )
        assert resp.status_code == 200
        assert len(resp.json()["items"]) == 1

    def test_cross_service_read_denied_403(self, client):
        # auth_service пишет свои login-события; server_service ими владеть не должен.
        _ingest(client, "auth_service", action="user.login")
        resp = client.get(
            INTERNAL,
            params={"service": "auth_service", "action": "user.login"},
            headers=headers_for("server_service"),
        )
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "SERVICE_IDENTITY_QUERY_MISMATCH"

    def test_cross_service_read_leaks_nothing(self, client):
        """Даже если у чужого сервиса есть события — 403 приходит ДО выборки,
        тело не содержит items."""
        _ingest(client, "auth_service", action="user.login")
        _ingest(client, "auth_service", action="user.login")
        resp = client.get(
            INTERNAL,
            params={"service": "auth_service", "action": "user.login"},
            headers=headers_for("server_service"),
        )
        assert resp.status_code == 403
        assert "items" not in resp.json()

    def test_confusable_service_query_denied(self, client):
        """`AUTH_SERVICE` нормализуется к `auth_service` и всё равно != identity
        server_service → 403 (обход casing'ом закрыт)."""
        resp = client.get(
            INTERNAL,
            params={"service": "AUTH_SERVICE", "action": "user.login"},
            headers=headers_for("server_service"),
        )
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "SERVICE_IDENTITY_QUERY_MISMATCH"


class TestInternalEventsRateLimit:
    """У `GET /internal/events` есть per-service-identity rate-limit — раньше
    это был единственный read-путь без лимита (burst COUNT/SELECT по журналу).
    """

    def test_burst_triggers_429(self, client, monkeypatch):
        monkeypatch.setenv("AUDIT_QUERY_RATE_LIMIT", "3/minute")
        from src.core.config import get_settings
        get_settings.cache_clear()
        from src.main import limiter
        limiter.reset()

        params = {"service": "server_service", "action": "server_account.drift_detected"}
        for i in range(3):
            r = client.get(INTERNAL, params=params, headers=headers_for("server_service"))
            assert r.status_code == 200, f"#{i} got {r.status_code}: {r.text}"

        r = client.get(INTERNAL, params=params, headers=headers_for("server_service"))
        assert r.status_code == 429, r.text
        assert r.json()["error_code"] == "RATE_LIMIT_EXCEEDED"

    def test_different_identities_independent_buckets(self, client, monkeypatch):
        monkeypatch.setenv("AUDIT_QUERY_RATE_LIMIT", "2/minute")
        from src.core.config import get_settings
        get_settings.cache_clear()
        from src.main import limiter
        limiter.reset()

        # Выжимаем бюджет server_service.
        srv_params = {"service": "server_service", "action": "server_account.drift_detected"}
        for _ in range(2):
            assert client.get(
                INTERNAL, params=srv_params, headers=headers_for("server_service")
            ).status_code == 200
        assert client.get(
            INTERNAL, params=srv_params, headers=headers_for("server_service")
        ).status_code == 429

        # config_service со своим bucket'ом ещё проходит (читает свой срез).
        cfg_params = {"service": "config_service", "action": "config.update"}
        assert client.get(
            INTERNAL, params=cfg_params, headers=headers_for("config_service")
        ).status_code == 200

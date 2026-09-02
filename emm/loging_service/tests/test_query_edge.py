"""Edge cases для `GET /api/logging/v1/events` и `/ready`.

Базовые happy лежат в test_query.py. Здесь:
* комбинация всех фильтров одновременно;
* `from_time > to_time` (логически пусто, но 200);
* `from_time == to_time` (включающее окно по 1 timestamp);
* `limit=0` / отрицательный → ge=1 422;
* `offset` отрицательный → 422;
* `severity` несуществующий → 422 (Literal whitelist);
* health/ready content type и shape.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tests.conftest import make_event


def _ts(offset_min: int = 0) -> str:
    """ISO-timestamp в окне ±1ч от now — внутри bounds валидатора timestamp'а."""
    return (datetime.now(timezone.utc) + timedelta(minutes=offset_min)).isoformat()


def _ingest(client, auth_headers, **kw):
    resp = client.post("/api/logging/v1/events", json=make_event(**kw), headers=auth_headers)
    assert resp.status_code == 201


# ── Time range edge ──────────────────────────────────────────────────────────

class TestTimeRangeEdge:
    def test_from_greater_than_to_returns_empty_200(self, client, admin_client, auth_headers):
        _ingest(client, auth_headers, timestamp=_ts(-30))
        resp = admin_client.get(
            "/api/logging/v1/events",
            params={
                "from_time": _ts(30),
                "to_time": _ts(-50),
            },
        )
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    def test_from_equals_to_empty_window(self, client, admin_client, auth_headers):
        # `to_time` exclusive: окно [t, t) логически пустое, события на
        # границе попадают только в окно, для которого граница — `from_time`.
        ts = _ts(-30)
        _ingest(client, auth_headers, timestamp=ts)
        resp = admin_client.get(
            "/api/logging/v1/events",
            params={"from_time": ts, "to_time": ts, "include_total": "true"},
        )
        assert resp.status_code == 200
        assert resp.json()["total"] == 0


# ── Combined filters ─────────────────────────────────────────────────────────

class TestCombinedFilters:
    def test_all_filters_together(self, client, admin_client, auth_headers):
        base = datetime.now(timezone.utc)
        ts_a = (base - timedelta(minutes=30)).isoformat()
        ts_b = (base - timedelta(minutes=15)).isoformat()
        _ingest(client, auth_headers,
                service="auth_service", action="user.login",
                status="success", severity="INFO", department_id="dep_a",
                timestamp=ts_a)
        _ingest(client, auth_headers,
                service="auth_service", action="user.login",
                status="failure", severity="CRITICAL", department_id="dep_a",
                timestamp=ts_b)
        from_t = (base - timedelta(minutes=45)).isoformat()
        to_t = (base - timedelta(minutes=5)).isoformat()
        # Только success+INFO+dep_a — попадает первое
        resp = admin_client.get(
            "/api/logging/v1/events",
            params={
                "service": "auth_service",
                "action": "user.login",
                "status": "success",
                "severity": "INFO",
                "department_id": "dep_a",
                "from_time": from_t,
                "to_time": to_t,
                "include_total": "true",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["status"] == "success"


# ── Pagination bounds ────────────────────────────────────────────────────────

class TestPaginationBounds:
    def test_limit_zero_rejected(self, admin_client):
        resp = admin_client.get("/api/logging/v1/events", params={"limit": 0})
        assert resp.status_code == 422

    def test_negative_limit_rejected(self, admin_client):
        resp = admin_client.get("/api/logging/v1/events", params={"limit": -10})
        assert resp.status_code == 422

    def test_negative_offset_rejected(self, admin_client):
        resp = admin_client.get("/api/logging/v1/events", params={"offset": -1})
        assert resp.status_code == 422

    def test_limit_one_works(self, client, admin_client, auth_headers):
        _ingest(client, auth_headers)
        _ingest(client, auth_headers)
        body = admin_client.get(
            "/api/logging/v1/events", params={"limit": 1, "include_total": "true"}
        ).json()
        assert len(body["items"]) == 1
        assert body["total"] >= 2
        assert body["has_more"] is True


# ── Invalid filter values ────────────────────────────────────────────────────

class TestInvalidFilterValues:
    def test_unknown_severity_rejected(self, admin_client):
        resp = admin_client.get("/api/logging/v1/events", params={"severity": "FATAL"})
        assert resp.status_code == 422

    def test_unknown_status_rejected(self, admin_client):
        """GET /events валидирует status по `Literal["success", "failure",
        "denied", "warning"]` — неизвестное значение возвращает 422.
        Симметрично `_unknown_severity_rejected` (Literal whitelist) и схеме
        `EventCreate.status` на ingest'е.
        """
        resp = admin_client.get("/api/logging/v1/events", params={"status": "ok"})
        assert resp.status_code == 422

    def test_malformed_from_time_rejected(self, admin_client):
        resp = admin_client.get("/api/logging/v1/events", params={"from_time": "yesterday"})
        assert resp.status_code == 422


# ── Health/Ready endpoints ───────────────────────────────────────────────────

class TestHealthReady:
    def test_health_200_and_shape(self, client):
        resp = client.get("/api/logging/v1/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("status") in ("ok", "healthy")

    def test_health_no_auth_required(self, client):
        # public endpoint
        resp = client.get("/api/logging/v1/health")
        assert resp.status_code == 200

    def test_ready_returns_200_when_db_healthy(self, client):
        resp = client.get("/api/logging/v1/ready")
        assert resp.status_code == 200

    def test_health_returns_json(self, client):
        resp = client.get("/api/logging/v1/health")
        assert resp.headers["content-type"].startswith("application/json")

"""Тесты: GET /api/logging/v1/events — запросы и фильтрация событий аудита.

GET /events требует platform_role=loging_admin → используем admin_client.
POST /events принимает SERVICE_API_KEY → используем client + auth_headers.
"""

from tests.conftest import make_event


def _ingest(client, auth_headers, **kwargs):
    resp = client.post("/api/logging/v1/events", json=make_event(**kwargs), headers=auth_headers)
    assert resp.status_code == 201
    return resp.json()["id"]


class TestQueryAll:
    def test_empty_db_returns_empty_list(self, admin_client):
        resp = admin_client.get("/api/logging/v1/events")
        assert resp.status_code == 200
        body = resp.json()
        assert body["items"] == []
        assert body["total"] == 0

    def test_returns_ingested_events(self, client, admin_client, auth_headers):
        _ingest(client, auth_headers)
        _ingest(client, auth_headers)
        body = admin_client.get("/api/logging/v1/events").json()
        assert body["total"] == 2
        assert len(body["items"]) == 2

    def test_events_sorted_newest_first(self, client, admin_client, auth_headers):
        _ingest(client, auth_headers, timestamp="2026-04-19T08:00:00Z")
        _ingest(client, auth_headers, timestamp="2026-04-19T10:00:00Z")
        _ingest(client, auth_headers, timestamp="2026-04-19T09:00:00Z")
        timestamps = [
            item["timestamp"]
            for item in admin_client.get("/api/logging/v1/events").json()["items"]
        ]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_requires_admin(self, client):
        assert client.get("/api/logging/v1/events").status_code == 401


class TestFilterByDepartment:
    def test_filters_by_department_id(self, client, admin_client, auth_headers):
        _ingest(client, auth_headers, department_id="dep_nt")
        _ingest(client, auth_headers, department_id="dep_nt")
        _ingest(client, auth_headers, department_id="dep_devops")
        body = admin_client.get(
            "/api/logging/v1/events", params={"department_id": "dep_nt"}
        ).json()
        assert body["total"] == 2
        assert all(i["department_id"] == "dep_nt" for i in body["items"])

    def test_unknown_department_returns_empty(self, client, admin_client, auth_headers):
        _ingest(client, auth_headers, department_id="dep_nt")
        assert admin_client.get(
            "/api/logging/v1/events", params={"department_id": "dep_unknown"}
        ).json()["total"] == 0


class TestFilterByService:
    def test_filters_by_service(self, client, admin_client, auth_headers):
        _ingest(client, auth_headers, service="auth_service")
        _ingest(client, auth_headers, service="auth_service")
        _ingest(client, auth_headers, service="config_service")
        body = admin_client.get(
            "/api/logging/v1/events", params={"service": "auth_service"}
        ).json()
        assert body["total"] == 2
        assert all(i["service"] == "auth_service" for i in body["items"])


class TestFilterBySeverity:
    def test_filters_by_all_six_levels(self, client, admin_client, auth_headers):
        for sev in ("TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            _ingest(client, auth_headers, severity=sev)
        for sev in ("TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            body = admin_client.get(
                "/api/logging/v1/events", params={"severity": sev}
            ).json()
            assert body["total"] == 1
            assert body["items"][0]["severity"] == sev

    def test_invalid_severity_returns_422(self, admin_client):
        assert admin_client.get(
            "/api/logging/v1/events", params={"severity": "VERBOSE"}
        ).status_code == 422


class TestFilterByAction:
    def test_filters_by_exact_action(self, client, admin_client, auth_headers):
        _ingest(client, auth_headers, action="user.login")
        _ingest(client, auth_headers, action="user.login")
        _ingest(client, auth_headers, action="token.refresh")
        body = admin_client.get(
            "/api/logging/v1/events", params={"action": "user.login"}
        ).json()
        assert body["total"] == 2
        assert all(i["action"] == "user.login" for i in body["items"])

    def test_action_is_exact_match_not_prefix(self, client, admin_client, auth_headers):
        _ingest(client, auth_headers, action="user.login")
        assert admin_client.get(
            "/api/logging/v1/events", params={"action": "user"}
        ).json()["total"] == 0


class TestFilterByTimeRange:
    def test_from_time_inclusive(self, client, admin_client, auth_headers):
        for ts in ("2026-04-19T08:00:00Z", "2026-04-19T10:00:00Z", "2026-04-19T12:00:00Z"):
            _ingest(client, auth_headers, timestamp=ts)
        assert admin_client.get(
            "/api/logging/v1/events", params={"from_time": "2026-04-19T10:00:00Z"}
        ).json()["total"] == 2

    def test_to_time_inclusive(self, client, admin_client, auth_headers):
        for ts in ("2026-04-19T08:00:00Z", "2026-04-19T10:00:00Z", "2026-04-19T12:00:00Z"):
            _ingest(client, auth_headers, timestamp=ts)
        assert admin_client.get(
            "/api/logging/v1/events", params={"to_time": "2026-04-19T10:00:00Z"}
        ).json()["total"] == 2

    def test_time_range_combined(self, client, admin_client, auth_headers):
        for ts in ("2026-04-19T08:00:00Z", "2026-04-19T10:00:00Z", "2026-04-19T12:00:00Z"):
            _ingest(client, auth_headers, timestamp=ts)
        assert admin_client.get(
            "/api/logging/v1/events",
            params={"from_time": "2026-04-19T09:00:00Z", "to_time": "2026-04-19T11:00:00Z"},
        ).json()["total"] == 1


class TestCombinedFilters:
    def test_department_and_severity(self, client, admin_client, auth_headers):
        _ingest(client, auth_headers, department_id="dep_nt", severity="WARNING")
        _ingest(client, auth_headers, department_id="dep_nt", severity="INFO")
        _ingest(client, auth_headers, department_id="dep_devops", severity="WARNING")
        body = admin_client.get(
            "/api/logging/v1/events",
            params={"department_id": "dep_nt", "severity": "WARNING"},
        ).json()
        assert body["total"] == 1
        assert body["items"][0]["department_id"] == "dep_nt"
        assert body["items"][0]["severity"] == "WARNING"


class TestPagination:
    def test_limit_restricts_results(self, client, admin_client, auth_headers):
        for _ in range(5):
            _ingest(client, auth_headers)
        body = admin_client.get("/api/logging/v1/events", params={"limit": 3}).json()
        assert len(body["items"]) == 3
        assert body["total"] == 5
        assert body["limit"] == 3
        assert body["offset"] == 0

    def test_offset_skips_events(self, client, admin_client, auth_headers):
        for _ in range(5):
            _ingest(client, auth_headers)
        body = admin_client.get(
            "/api/logging/v1/events", params={"limit": 3, "offset": 3}
        ).json()
        assert len(body["items"]) == 2
        assert body["total"] == 5
        assert body["offset"] == 3

    def test_limit_exceeds_max_returns_422(self, admin_client):
        assert admin_client.get(
            "/api/logging/v1/events", params={"limit": 9999}
        ).status_code == 422

    def test_negative_offset_returns_422(self, admin_client):
        assert admin_client.get(
            "/api/logging/v1/events", params={"offset": -1}
        ).status_code == 422

"""Тесты: GET /api/logging/v1/events/stats и /events/export.

stats  — агрегаты за окно (счётчики по severity/service/status, total).
export — CSV-выгрузка журнала за окно + cap по числу строк + RBAC.

Чтение требует platform_role=loging_admin/loging_reader → admin_client.
RBAC-отказ (403 для не-loging роли) проверяется через `client` + mock
introspect (как в test_reader_auth.py).
"""

import csv
import io
from datetime import datetime, timedelta, timezone

import httpx

from src.dependencies import auth as _auth_deps
from tests.conftest import make_event

STATS_URL = "/api/logging/v1/events/stats"
EXPORT_URL = "/api/logging/v1/events/export"


def _ts(offset_min: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=offset_min)).isoformat()


def _ingest(client, auth_headers, **kwargs):
    payload = make_event(**kwargs)
    headers = auth_headers | {"X-Service-Identity": payload["service"]}
    resp = client.post("/api/logging/v1/events", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _mock_reader(role: str | None, **extra):
    """Подменяет pooled introspect-клиент identity с заданной platform_role."""
    payload = {"active": True, "subject_type": "user", "sub": "u1",
               "username": "tester", "platform_role": role, "department_id": None}
    payload.update(extra)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    pooled = httpx.AsyncClient(
        base_url="http://auth-test:8000",
        transport=httpx.MockTransport(handler),
        timeout=5.0,
    )
    original = _auth_deps._introspect_client
    _auth_deps._introspect_client = pooled

    class _Ctx:
        def stop(self):
            _auth_deps._introspect_client = original
            import asyncio
            asyncio.run(pooled.aclose())

    return _Ctx()


# ── stats ─────────────────────────────────────────────────────────────────────


class TestStats:
    def test_empty_window_zero_total(self, admin_client):
        body = admin_client.get(STATS_URL).json()
        assert body["total"] == 0
        assert body["by_severity"] == {}
        assert body["by_service"] == {}
        assert body["by_status"] == {}

    def test_counts_by_severity_and_service(self, client, admin_client, auth_headers):
        # 2 INFO от auth_service, 1 ERROR от server_service, все в окне.
        _ingest(client, auth_headers, severity="INFO", service="auth_service")
        _ingest(client, auth_headers, severity="INFO", service="auth_service")
        _ingest(client, auth_headers, severity="ERROR", service="server_service",
                action="server.power_on", status="failure", allowed=False)

        body = admin_client.get(STATS_URL).json()
        assert body["total"] == 3
        assert body["by_severity"]["INFO"] == 2
        assert body["by_severity"]["ERROR"] == 1
        assert body["by_service"]["auth_service"] == 2
        assert body["by_service"]["server_service"] == 1
        assert body["by_status"]["success"] == 2
        assert body["by_status"]["failure"] == 1

    def test_window_excludes_old_events(self, client, admin_client, auth_headers):
        # Событие 40 минут назад — внутри дефолтного 24ч окна.
        _ingest(client, auth_headers, timestamp=_ts(-40))
        # window_hours=0.1 ≈ 6 минут → событие 40-минутной давности выпадает.
        # (window_hours имеет ge=1, поэтому окно режем явными from/to.)
        now = datetime.now(timezone.utc)
        params = {
            "from_time": (now - timedelta(minutes=5)).isoformat(),
            "to_time": now.isoformat(),
        }
        body = admin_client.get(STATS_URL, params=params).json()
        assert body["total"] == 0

        # Без сужения — попадает в дефолтное 24ч окно.
        assert admin_client.get(STATS_URL).json()["total"] == 1

    def test_severity_filter_narrows(self, client, admin_client, auth_headers):
        _ingest(client, auth_headers, severity="INFO")
        _ingest(client, auth_headers, severity="ERROR", action="server.power_on",
                service="server_service", status="failure", allowed=False)
        body = admin_client.get(STATS_URL, params={"severity": "ERROR"}).json()
        assert body["total"] == 1
        assert body["by_severity"] == {"ERROR": 1}

    def test_window_bounds_in_response(self, admin_client):
        body = admin_client.get(STATS_URL, params={"window_hours": 12}).json()
        win_from = datetime.fromisoformat(body["from_time"])
        win_to = datetime.fromisoformat(body["to_time"])
        assert abs((win_to - win_from) - timedelta(hours=12)) < timedelta(seconds=2)

    def test_requires_auth(self, client):
        assert client.get(STATS_URL).status_code == 401

    def test_non_loging_role_forbidden(self, client):
        ctx = _mock_reader("department_admin")
        try:
            r = client.get(STATS_URL, headers={"Authorization": "Bearer t"})
        finally:
            ctx.stop()
        assert r.status_code == 403
        assert r.json()["error_code"] == "INSUFFICIENT_ROLE"


class TestStatsDeptScope:
    def test_stats_scoped_to_own_department(self, client, auth_headers):
        """loging_reader_dep: stats считаются только по своему отделу."""
        _ingest(client, auth_headers, department_id="dep_a")
        _ingest(client, auth_headers, department_id="dep_a")
        _ingest(client, auth_headers, department_id="dep_b")
        ctx = _mock_reader("loging_reader_dep", department_id="dep_a")
        try:
            body = client.get(STATS_URL, headers={"Authorization": "Bearer t"}).json()
        finally:
            ctx.stop()
        assert body["total"] == 2

    def test_stats_ignores_foreign_department_query(self, client, auth_headers):
        """Передача чужого department_id перекрывается своим отделом."""
        _ingest(client, auth_headers, department_id="dep_a")
        _ingest(client, auth_headers, department_id="dep_b")
        ctx = _mock_reader("loging_reader_dep", department_id="dep_a")
        try:
            body = client.get(
                STATS_URL,
                headers={"Authorization": "Bearer t"},
                params={"department_id": "dep_b"},
            ).json()
        finally:
            ctx.stop()
        assert body["total"] == 1

    def test_stats_no_department_returns_403(self, client, auth_headers):
        _ingest(client, auth_headers, department_id="dep_a")
        ctx = _mock_reader("loging_reader_dep", department_id=None)
        try:
            r = client.get(STATS_URL, headers={"Authorization": "Bearer t"})
        finally:
            ctx.stop()
        assert r.status_code == 403
        assert r.json()["error_code"] == "INSUFFICIENT_ROLE"


# ── export ──────────────────────────────────────────────────────────────────


def _parse_csv(text: str) -> tuple[list[str], list[list[str]]]:
    reader = list(csv.reader(io.StringIO(text)))
    return reader[0], reader[1:]


class TestExport:
    def test_csv_header_and_rows(self, client, admin_client, auth_headers):
        _ingest(client, auth_headers, service="auth_service", action="user.login")
        _ingest(client, auth_headers, service="auth_service", action="user.logout")

        r = admin_client.get(EXPORT_URL)
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/csv")
        assert "attachment" in r.headers["content-disposition"]
        assert ".csv" in r.headers["content-disposition"]
        assert r.headers["x-export-truncated"] == "false"

        header, rows = _parse_csv(r.text)
        assert header[0] == "id"
        assert "action" in header and "severity" in header and "details" in header
        assert len(rows) == 2
        actions = {row[header.index("action")] for row in rows}
        assert actions == {"user.login", "user.logout"}

    def test_filters_applied(self, client, admin_client, auth_headers):
        _ingest(client, auth_headers, service="auth_service")
        _ingest(client, auth_headers, service="server_service",
                action="server.power_on")
        r = admin_client.get(EXPORT_URL, params={"service": "server_service"})
        header, rows = _parse_csv(r.text)
        assert len(rows) == 1
        assert rows[0][header.index("service")] == "server_service"

    def test_department_name_column(self, client, admin_client, auth_headers):
        _ingest(client, auth_headers, department_id="dep_a", department_name="Alpha")
        r = admin_client.get(EXPORT_URL)
        header, rows = _parse_csv(r.text)
        assert "department_name" in header
        assert rows[0][header.index("department_name")] == "Alpha"
        assert rows[0][header.index("department_id")] == "dep_a"

    def test_details_serialized_inline(self, client, admin_client, auth_headers):
        _ingest(client, auth_headers, details={"k": "v", "n": 1})
        r = admin_client.get(EXPORT_URL)
        header, rows = _parse_csv(r.text)
        assert len(rows) == 1
        details_cell = rows[0][header.index("details")]
        assert '"k":"v"' in details_cell

    def test_cap_truncates(self, client, admin_client, auth_headers, monkeypatch):
        import src.api.v1.endpoints.events as ep
        monkeypatch.setattr(ep, "MAX_EXPORT_ROWS", 2)
        for _ in range(3):
            _ingest(client, auth_headers)
        r = admin_client.get(EXPORT_URL)
        assert r.headers["x-export-truncated"] == "true"
        _, rows = _parse_csv(r.text)
        assert len(rows) == 2

    def test_rows_chronological(self, client, admin_client, auth_headers):
        _ingest(client, auth_headers, timestamp=_ts(-30), action="user.login")
        _ingest(client, auth_headers, timestamp=_ts(-10), action="user.logout")
        r = admin_client.get(EXPORT_URL)
        header, rows = _parse_csv(r.text)
        ts = [row[header.index("timestamp")] for row in rows]
        assert ts == sorted(ts)

    def test_requires_auth(self, client):
        assert client.get(EXPORT_URL).status_code == 401

    def test_non_loging_role_forbidden(self, client):
        ctx = _mock_reader("department_admin", department_id="dep_a")
        try:
            r = client.get(EXPORT_URL, headers={"Authorization": "Bearer t"})
        finally:
            ctx.stop()
        assert r.status_code == 403
        assert r.json()["error_code"] == "INSUFFICIENT_ROLE"

    def test_dept_scoped_export_only_own_department(self, client, auth_headers):
        """loging_reader_dep: экспорт содержит только события своего отдела."""
        _ingest(client, auth_headers, department_id="dep_a", action="user.login")
        _ingest(client, auth_headers, department_id="dep_b", action="user.logout")
        ctx = _mock_reader("loging_reader_dep", department_id="dep_a")
        try:
            r = client.get(EXPORT_URL, headers={"Authorization": "Bearer t"})
        finally:
            ctx.stop()
        assert r.status_code == 200
        header, rows = _parse_csv(r.text)
        assert len(rows) == 1
        assert rows[0][header.index("department_id")] == "dep_a"

    def test_dept_scoped_export_ignores_foreign_query(self, client, auth_headers):
        """Передача чужого department_id перекрывается своим отделом."""
        _ingest(client, auth_headers, department_id="dep_a", action="user.login")
        _ingest(client, auth_headers, department_id="dep_b", action="user.logout")
        ctx = _mock_reader("loging_reader_dep", department_id="dep_a")
        try:
            r = client.get(
                EXPORT_URL,
                headers={"Authorization": "Bearer t"},
                params={"department_id": "dep_b"},
            )
        finally:
            ctx.stop()
        assert r.status_code == 200
        header, rows = _parse_csv(r.text)
        assert len(rows) == 1
        assert rows[0][header.index("department_id")] == "dep_a"

    def test_dept_scoped_export_no_department_returns_403(self, client, auth_headers):
        _ingest(client, auth_headers, department_id="dep_a")
        ctx = _mock_reader("loging_reader_dep", department_id=None)
        try:
            r = client.get(EXPORT_URL, headers={"Authorization": "Bearer t"})
        finally:
            ctx.stop()
        assert r.status_code == 403
        assert r.json()["error_code"] == "INSUFFICIENT_ROLE"

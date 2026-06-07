"""Тесты: GET /api/logging/v1/events — запросы и фильтрация событий аудита.

GET /events требует platform_role=loging_admin → используем admin_client.
POST /events принимает SERVICE_API_KEY → используем client + auth_headers.
"""

from datetime import datetime, timedelta, timezone

from tests.conftest import make_event


def _ts(offset_min: int = 0) -> str:
    """ISO-timestamp в окне ±1ч от `datetime.now(UTC)` — внутри bounds валидатора."""
    return (datetime.now(timezone.utc) + timedelta(minutes=offset_min)).isoformat()


def _ingest(client, auth_headers, **kwargs):
    payload = make_event(**kwargs)
    # `SERVICE_IDENTITY_PAYLOAD_MISMATCH` guard требует, чтобы
    # X-Service-Identity совпадал с payload.service.
    headers = auth_headers | {"X-Service-Identity": payload["service"]}
    resp = client.post("/api/logging/v1/events", json=payload, headers=headers)
    assert resp.status_code == 201
    return resp.json()["id"]


class TestQueryAll:
    def test_empty_db_returns_empty_list(self, admin_client):
        resp = admin_client.get(
            "/api/logging/v1/events", params={"include_total": "true"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["items"] == []
        assert body["total"] == 0
        assert body["has_more"] is False

    def test_returns_ingested_events(self, client, admin_client, auth_headers):
        _ingest(client, auth_headers)
        _ingest(client, auth_headers)
        body = admin_client.get(
            "/api/logging/v1/events", params={"include_total": "true"}
        ).json()
        assert body["total"] == 2
        assert len(body["items"]) == 2

    def test_events_sorted_newest_first(self, client, admin_client, auth_headers):
        _ingest(client, auth_headers, timestamp=_ts(-40))
        _ingest(client, auth_headers, timestamp=_ts(-10))
        _ingest(client, auth_headers, timestamp=_ts(-25))
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
            "/api/logging/v1/events",
            params={"department_id": "dep_nt", "include_total": "true"},
        ).json()
        assert body["total"] == 2
        assert all(i["department_id"] == "dep_nt" for i in body["items"])

    def test_unknown_department_returns_empty(self, client, admin_client, auth_headers):
        _ingest(client, auth_headers, department_id="dep_nt")
        assert admin_client.get(
            "/api/logging/v1/events",
            params={"department_id": "dep_unknown", "include_total": "true"},
        ).json()["total"] == 0


class TestFilterByService:
    def test_filters_by_service(self, client, admin_client, auth_headers):
        _ingest(client, auth_headers, service="auth_service")
        _ingest(client, auth_headers, service="auth_service")
        _ingest(client, auth_headers, service="config_service")
        body = admin_client.get(
            "/api/logging/v1/events",
            params={"service": "auth_service", "include_total": "true"},
        ).json()
        assert body["total"] == 2
        assert all(i["service"] == "auth_service" for i in body["items"])


class TestFilterBySeverity:
    def test_filters_by_all_six_levels(self, client, admin_client, auth_headers):
        for sev in ("TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            _ingest(client, auth_headers, severity=sev)
        for sev in ("TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            body = admin_client.get(
                "/api/logging/v1/events",
                params={"severity": sev, "include_total": "true"},
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
            "/api/logging/v1/events",
            params={"action": "user.login", "include_total": "true"},
        ).json()
        assert body["total"] == 2
        assert all(i["action"] == "user.login" for i in body["items"])

    def test_action_is_exact_match_not_prefix(self, client, admin_client, auth_headers):
        _ingest(client, auth_headers, action="user.login")
        assert admin_client.get(
            "/api/logging/v1/events",
            params={"action": "user", "include_total": "true"},
        ).json()["total"] == 0


def _three_points_and_mid(offsets=(-50, -30, -10)):
    """Возвращает (timestamps_to_ingest, фиксированная середина для filter'а).

    Без зафиксированного base'а каждый вызов `_ts()` в filter'е отстаёт от
    ingest'а на миллисекунды, и inclusive-граница (`from_time=_ts(-30)`)
    может выбросить -30-ингест за пределы окна. Считаем все три точки от
    одного `now`, и для filter'а берём ровно ту же середину.
    """
    base = datetime.now(timezone.utc)
    ts_list = [(base + timedelta(minutes=o)).isoformat() for o in offsets]
    return ts_list


class TestFilterByTimeRange:
    def test_from_time_inclusive(self, client, admin_client, auth_headers):
        ts_list = _three_points_and_mid()
        for ts in ts_list:
            _ingest(client, auth_headers, timestamp=ts)
        # from_time=ts_list[1] → берём 2 точки: середина (inclusive) + последняя.
        assert admin_client.get(
            "/api/logging/v1/events",
            params={"from_time": ts_list[1], "include_total": "true"},
        ).json()["total"] == 2

    def test_to_time_exclusive(self, client, admin_client, auth_headers):
        # `to_time` exclusive: события с `timestamp == to_time` НЕ попадают
        # в выдачу. Это исключает дубли на стыке sliding-window запросов,
        # где caller передаёт `from_time=prev_to`.
        ts_list = _three_points_and_mid()
        for ts in ts_list:
            _ingest(client, auth_headers, timestamp=ts)
        # to_time = середина → берём только первую точку (раньше середины).
        assert admin_client.get(
            "/api/logging/v1/events",
            params={"to_time": ts_list[1], "include_total": "true"},
        ).json()["total"] == 1

    def test_to_time_sliding_window_no_duplicate(self, client, admin_client, auth_headers):
        # Окно [t0, t1) + окно [t1, t2): событие на t1 попадает ровно
        # во второе окно, не в оба.
        ts_list = _three_points_and_mid()
        for ts in ts_list:
            _ingest(client, auth_headers, timestamp=ts)
        first = admin_client.get(
            "/api/logging/v1/events",
            params={"from_time": ts_list[0], "to_time": ts_list[1], "include_total": "true"},
        ).json()
        second = admin_client.get(
            "/api/logging/v1/events",
            params={"from_time": ts_list[1], "to_time": ts_list[2], "include_total": "true"},
        ).json()
        ids_first = {i["id"] for i in first["items"]}
        ids_second = {i["id"] for i in second["items"]}
        assert ids_first.isdisjoint(ids_second)
        # Граничное событие (ts_list[1]) ушло именно во второе окно.
        assert first["total"] == 1
        assert second["total"] == 1

    def test_time_range_combined(self, client, admin_client, auth_headers):
        ts_list = _three_points_and_mid()
        for ts in ts_list:
            _ingest(client, auth_headers, timestamp=ts)
        # Окно вокруг середины — должно поймать только её.
        base = datetime.fromisoformat(ts_list[1])
        from_t = (base - timedelta(minutes=5)).isoformat()
        to_t = (base + timedelta(minutes=5)).isoformat()
        assert admin_client.get(
            "/api/logging/v1/events",
            params={
                "from_time": from_t,
                "to_time": to_t,
                "include_total": "true",
            },
        ).json()["total"] == 1


class TestCombinedFilters:
    def test_department_and_severity(self, client, admin_client, auth_headers):
        _ingest(client, auth_headers, department_id="dep_nt", severity="WARNING")
        _ingest(client, auth_headers, department_id="dep_nt", severity="INFO")
        _ingest(client, auth_headers, department_id="dep_devops", severity="WARNING")
        body = admin_client.get(
            "/api/logging/v1/events",
            params={
                "department_id": "dep_nt",
                "severity": "WARNING",
                "include_total": "true",
            },
        ).json()
        assert body["total"] == 1
        assert body["items"][0]["department_id"] == "dep_nt"
        assert body["items"][0]["severity"] == "WARNING"


class TestPagination:
    def test_limit_restricts_results(self, client, admin_client, auth_headers):
        for _ in range(5):
            _ingest(client, auth_headers)
        body = admin_client.get(
            "/api/logging/v1/events", params={"limit": 3, "include_total": "true"}
        ).json()
        assert len(body["items"]) == 3
        assert body["total"] == 5
        assert body["has_more"] is True
        assert body["limit"] == 3
        assert body["offset"] == 0

    def test_offset_skips_events(self, client, admin_client, auth_headers):
        for _ in range(5):
            _ingest(client, auth_headers)
        body = admin_client.get(
            "/api/logging/v1/events",
            params={"limit": 3, "offset": 3, "include_total": "true"},
        ).json()
        assert len(body["items"]) == 2
        assert body["total"] == 5
        assert body["has_more"] is False
        assert body["offset"] == 3

    def test_limit_exceeds_max_returns_422(self, admin_client):
        assert admin_client.get(
            "/api/logging/v1/events", params={"limit": 9999}
        ).status_code == 422

    def test_negative_offset_returns_422(self, admin_client):
        assert admin_client.get(
            "/api/logging/v1/events", params={"offset": -1}
        ).status_code == 422

    def test_offset_exceeds_max_returns_422(self, admin_client):
        # Верхняя граница 10_000_000 — выше PostgreSQL зря крутил бы
        # внутренний proскролл, statement_timeout рвал бы запрос.
        assert admin_client.get(
            "/api/logging/v1/events", params={"offset": 10_000_001}
        ).status_code == 422


class TestIncludeTotalAndHasMore:
    """total теперь опционален — COUNT не выполняется без `include_total=true`.

    По умолчанию `total=null`, а наличие следующей страницы отдаётся через
    `has_more` (вычисляется выборкой одной лишней строки, без второго прохода).
    """

    def test_default_does_not_compute_total(self, client, admin_client, auth_headers):
        for _ in range(3):
            _ingest(client, auth_headers)
        body = admin_client.get("/api/logging/v1/events").json()
        assert body["total"] is None
        assert len(body["items"]) == 3
        assert body["has_more"] is False

    def test_include_total_false_explicit(self, client, admin_client, auth_headers):
        _ingest(client, auth_headers)
        body = admin_client.get(
            "/api/logging/v1/events", params={"include_total": "false"}
        ).json()
        assert body["total"] is None

    def test_include_total_true_returns_count(self, client, admin_client, auth_headers):
        for _ in range(4):
            _ingest(client, auth_headers)
        body = admin_client.get(
            "/api/logging/v1/events", params={"include_total": "true"}
        ).json()
        assert body["total"] == 4

    def test_has_more_true_when_more_pages(self, client, admin_client, auth_headers):
        for _ in range(5):
            _ingest(client, auth_headers)
        body = admin_client.get(
            "/api/logging/v1/events", params={"limit": 2}
        ).json()
        assert len(body["items"]) == 2
        assert body["has_more"] is True
        assert body["total"] is None

    def test_has_more_false_on_last_page(self, client, admin_client, auth_headers):
        for _ in range(5):
            _ingest(client, auth_headers)
        body = admin_client.get(
            "/api/logging/v1/events", params={"limit": 2, "offset": 4}
        ).json()
        assert len(body["items"]) == 1
        assert body["has_more"] is False

    def test_has_more_false_when_exactly_limit(self, client, admin_client, auth_headers):
        for _ in range(3):
            _ingest(client, auth_headers)
        body = admin_client.get(
            "/api/logging/v1/events", params={"limit": 3}
        ).json()
        assert len(body["items"]) == 3
        assert body["has_more"] is False

    def test_extra_row_not_leaked_into_items(self, client, admin_client, auth_headers):
        """limit+1 fetch не должен отдавать лишнюю строку в items."""
        for _ in range(10):
            _ingest(client, auth_headers)
        body = admin_client.get(
            "/api/logging/v1/events", params={"limit": 4}
        ).json()
        assert len(body["items"]) == 4
        assert body["has_more"] is True

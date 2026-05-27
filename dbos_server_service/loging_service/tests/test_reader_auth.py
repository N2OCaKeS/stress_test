"""Тесты: require_reader — все варианты read-доступа к /events и dept-scoping."""

from unittest.mock import MagicMock, patch

from tests.conftest import make_event

EVENTS_URL = "/api/logging/v1/events"


def _mock_identity(client, identity_payload):
    """Хелпер: мок introspect-ответа от auth_service.

    Принимает короткий dict с полями user_id/username/... и автоматически
    оборачивает его в формат IntrospectResponse (active=True, sub=...).
    """
    payload = dict(identity_payload)
    payload.setdefault("active", True)
    payload.setdefault("subject_type", "user")
    if "user_id" in payload and "sub" not in payload:
        payload["sub"] = payload.pop("user_id")
    p = patch("src.dependencies.auth.httpx.post")
    m = p.start()
    m.return_value = MagicMock(status_code=200, json=lambda: payload)
    return p


def _ingest(client, auth_headers, **overrides):
    return client.post(EVENTS_URL, headers=auth_headers, json=make_event(**overrides))


# ── loging_admin / account_admin: dept_scope=None, видят всё ──────────────────


class TestUnscopedReaders:
    def test_account_admin_can_list_events(self, client, auth_headers):
        _ingest(client, auth_headers, department_id="dep_a")
        _ingest(client, auth_headers, department_id="dep_b")
        p = _mock_identity(client, {"user_id": "u1", "username": "aa",
                                     "platform_role": "account_admin", "department_id": None})
        try:
            r = client.get(
                EVENTS_URL,
                headers={"Authorization": "Bearer t"},
                params={"include_total": "true"},
            )
        finally:
            p.stop()
        assert r.status_code == 200
        # Видит события всех отделов
        assert r.json()["total"] == 2


# ── loging_reader / department_admin: dept_scope=<dept_id> ────────────────────


class TestDeptScopedReaders:
    def test_loging_reader_sees_only_own_dept(self, client, auth_headers):
        _ingest(client, auth_headers, department_id="dep_a")
        _ingest(client, auth_headers, department_id="dep_a")
        _ingest(client, auth_headers, department_id="dep_b")
        p = _mock_identity(client, {"user_id": "u1", "username": "lr",
                                     "platform_role": "loging_reader", "department_id": "dep_a"})
        try:
            r = client.get(
                EVENTS_URL,
                headers={"Authorization": "Bearer t"},
                params={"include_total": "true"},
            )
        finally:
            p.stop()
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 2
        for item in body["items"]:
            assert item["department_id"] == "dep_a"

    def test_department_admin_sees_only_own_dept(self, client, auth_headers):
        _ingest(client, auth_headers, department_id="dep_a")
        _ingest(client, auth_headers, department_id="dep_b")
        p = _mock_identity(client, {"user_id": "u1", "username": "da",
                                     "platform_role": "department_admin", "department_id": "dep_b"})
        try:
            r = client.get(
                EVENTS_URL,
                headers={"Authorization": "Bearer t"},
                params={"include_total": "true"},
            )
        finally:
            p.stop()
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        assert body["items"][0]["department_id"] == "dep_b"

    def test_scoped_user_explicit_cross_dept_query_returns_403(self, client, auth_headers):
        _ingest(client, auth_headers, department_id="dep_a")
        p = _mock_identity(client, {"user_id": "u1", "username": "lr",
                                     "platform_role": "loging_reader", "department_id": "dep_a"})
        try:
            r = client.get(EVENTS_URL,
                           headers={"Authorization": "Bearer t"},
                           params={"department_id": "dep_b"})
        finally:
            p.stop()
        # Department_admin/loging_reader не могут смотреть в чужой отдел явно
        assert r.status_code == 403

    def test_scoped_user_without_department_returns_403(self, client):
        """loging_reader / department_admin без department_id → 403 NO_DEPARTMENT."""
        p = _mock_identity(client, {"user_id": "u1", "username": "lr",
                                     "platform_role": "loging_reader", "department_id": None})
        try:
            r = client.get(EVENTS_URL, headers={"Authorization": "Bearer t"})
        finally:
            p.stop()
        assert r.status_code == 403
        assert r.json()["error_code"] == "NO_DEPARTMENT"


# ── Через service-role в loging_service ───────────────────────────────────────


class TestServiceRoleReaders:
    def test_user_with_loging_service_reader_role_sees_own_dept(self, client, auth_headers):
        _ingest(client, auth_headers, department_id="dep_a")
        _ingest(client, auth_headers, department_id="dep_b")
        p = _mock_identity(client, {
            "user_id": "u1", "username": "nt_senior",
            "platform_role": None,
            "department_id": "dep_a",
            "service_roles": {"loging_service": ["reader"]},
        })
        try:
            r = client.get(
                EVENTS_URL,
                headers={"Authorization": "Bearer t"},
                params={"include_total": "true"},
            )
        finally:
            p.stop()
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        assert body["items"][0]["department_id"] == "dep_a"

    def test_user_with_loging_service_operator_role_can_read(self, client, auth_headers):
        _ingest(client, auth_headers, department_id="dep_a")
        p = _mock_identity(client, {
            "user_id": "u1", "username": "op",
            "platform_role": None,
            "department_id": "dep_a",
            "service_roles": {"loging_service": ["operator"]},
        })
        try:
            r = client.get(EVENTS_URL, headers={"Authorization": "Bearer t"})
        finally:
            p.stop()
        assert r.status_code == 200


# ── Отказ в доступе ───────────────────────────────────────────────────────────


class TestReaderDenial:
    def test_user_without_any_relevant_role_returns_403(self, client):
        """platform_role=None, нет ролей в loging_service → 403 INSUFFICIENT_ROLE."""
        p = _mock_identity(client, {
            "user_id": "u1", "username": "nobody",
            "platform_role": None,
            "department_id": "dep_a",
            "service_roles": {"config_service": ["reader"]},  # не loging_service
        })
        try:
            r = client.get(EVENTS_URL, headers={"Authorization": "Bearer t"})
        finally:
            p.stop()
        assert r.status_code == 403
        assert r.json()["error_code"] == "INSUFFICIENT_ROLE"

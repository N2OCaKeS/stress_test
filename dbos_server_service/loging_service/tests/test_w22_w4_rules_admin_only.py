"""W22-W4 P3: `GET /rules` доступен только `loging_admin` / `account_admin`.

Owner-decision: `loging_reader` / `department_admin` / service-роли в
`loging_service` НЕ видят правила. Симметрично для `GET /rules/{id}` и для
POST/PATCH/DELETE.

Audit denied события на 401/403 эмитятся middleware'ом `audit_access` как
`http.access_denied` — отдельную аудит-эмиссию из dependency не вводим, чтобы
не дублировать запись в SIEM.
"""

import pytest

RULES_URL = "/api/logging/v1/rules"


# ── allowed roles ──────────────────────────────────────────────────────────


class TestAllowedRolesCanReadRules:
    def test_loging_admin_can_list_rules(self, client, mock_introspect):
        with mock_introspect(json_body={
            "active": True, "subject_type": "user", "sub": "usr_1",
            "username": "admin", "platform_role": "loging_admin",
        }):
            r = client.get(RULES_URL, headers={"Authorization": "Bearer jwt"})
        assert r.status_code == 200
        body = r.json()
        assert "items" in body

    def test_account_admin_can_list_rules(self, client, mock_introspect):
        with mock_introspect(json_body={
            "active": True, "subject_type": "user", "sub": "usr_2",
            "username": "acc_admin", "platform_role": "account_admin",
        }):
            r = client.get(RULES_URL, headers={"Authorization": "Bearer jwt"})
        assert r.status_code == 200

    def test_loging_admin_can_get_rule_by_id(self, client, mock_introspect):
        with mock_introspect(json_body={
            "active": True, "subject_type": "user", "sub": "usr_1",
            "username": "admin", "platform_role": "loging_admin",
        }):
            r = client.get(f"{RULES_URL}/rl_nope", headers={"Authorization": "Bearer jwt"})
        # 404 (правила нет в чистой БД) — но не 403 / не 401: dependency пропустил.
        assert r.status_code == 404

    def test_account_admin_can_get_rule_by_id(self, client, mock_introspect):
        with mock_introspect(json_body={
            "active": True, "subject_type": "user", "sub": "usr_2",
            "username": "acc_admin", "platform_role": "account_admin",
        }):
            r = client.get(f"{RULES_URL}/rl_nope", headers={"Authorization": "Bearer jwt"})
        assert r.status_code == 404


# ── denied roles ───────────────────────────────────────────────────────────


_LOGING_READER_IDENTITY = {
    "active": True, "subject_type": "user", "sub": "usr_3",
    "username": "reader", "platform_role": "loging_reader",
    "department_id": "dep_a",
}


class TestLogingReaderDeniedFromRules:
    @pytest.mark.parametrize("method, url, json_body", [
        ("get", RULES_URL, None),
        ("get", f"{RULES_URL}/rl_x", None),
        ("post", RULES_URL, {"name": "x", "effect": "SUPPRESS", "priority": 100}),
        ("patch", f"{RULES_URL}/rl_x", {"priority": 200}),
        ("delete", f"{RULES_URL}/rl_x", None),
    ])
    def test_loging_reader_403(self, client, mock_introspect, method, url, json_body):
        with mock_introspect(json_body=_LOGING_READER_IDENTITY):
            kwargs = {"headers": {"Authorization": "Bearer jwt"}}
            if json_body is not None:
                kwargs["json"] = json_body
            r = getattr(client, method)(url, **kwargs)
        assert r.status_code == 403
        assert r.json()["error_code"] == "LOGING_ADMIN_REQUIRED"


class TestDepartmentAdminDeniedFromRules:
    def test_department_admin_403_on_list(self, client, mock_introspect):
        with mock_introspect(json_body={
            "active": True, "subject_type": "user", "sub": "usr_4",
            "username": "dept_admin", "platform_role": "department_admin",
            "department_id": "dep_a",
        }):
            r = client.get(RULES_URL, headers={"Authorization": "Bearer jwt"})
        assert r.status_code == 403
        assert r.json()["error_code"] == "LOGING_ADMIN_REQUIRED"


class TestServiceRoleDeniedFromRules:
    @pytest.mark.parametrize("role", ["reader", "operator", "admin"])
    def test_loging_service_role_403_on_list(self, client, mock_introspect, role):
        """Пользователь со service-ролью в loging_service не видит rules."""
        with mock_introspect(json_body={
            "active": True, "subject_type": "user", "sub": "usr_5",
            "username": "svc_user", "platform_role": None,
            "department_id": "dep_a",
            "service_roles": {"loging_service": [role]},
        }):
            r = client.get(RULES_URL, headers={"Authorization": "Bearer jwt"})
        assert r.status_code == 403
        assert r.json()["error_code"] == "LOGING_ADMIN_REQUIRED"


class TestUnauthenticatedDeniedFromRules:
    def test_no_token_returns_401(self, client):
        r = client.get(RULES_URL)
        assert r.status_code == 401

    def test_invalid_token_returns_401(self, client, mock_introspect):
        with mock_introspect(json_body={"active": False}):
            r = client.get(RULES_URL, headers={"Authorization": "Bearer bad"})
        assert r.status_code == 401

"""Cross-service интеграция: per-department service_roles + groups → audit.

Проверяем что новая per-department архитектура (мои недавние изменения)
корректно публикует audit-события в loging_service:

* `service_role.create`, `service_role.delete` — с правильными
  department_id/service_name/role_name в details;
* `group.create`, `group.member_add`, `group.roles_assign` — severity и target;
* `department.service_grant` — CRITICAL severity, target=department.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import httpx

from tests.integration.conftest import wait_for_event  # noqa: F401


def _u() -> str:
    """Уникальный суффикс — integration stack не пересоздаёт БД между прогонами."""
    return uuid.uuid4().hex[:8]


def _find_event(logging_client, *, action: str, target_id: str | None = None,
                 from_time, retries: int = 20, delay: float = 0.5,
                 details_match: dict | None = None) -> dict:
    """Поллит /events пока не найдёт event с указанным target_id или
    matching details (для случаев когда target_id не совпадает)."""
    import time as _time
    for _ in range(retries):
        params = {"action": action, "limit": 50, "from_time": from_time.isoformat()}
        r = logging_client.get("/api/logging/v1/events", params=params)
        assert r.status_code == 200, r.text
        for item in r.json()["items"]:
            if target_id is not None and item.get("target_id") != target_id:
                continue
            if details_match:
                if not all(item.get("details", {}).get(k) == v
                           for k, v in details_match.items()):
                    continue
            return item
        _time.sleep(delay)
    raise AssertionError(
        f"event action={action!r} target_id={target_id!r} match={details_match!r} not found"
    )


def _ensure_dept(auth_client, admin_token, name) -> str:
    r = auth_client.post(
        "/api/auth/v1/departments",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": name, "display_name": name.title()},
    )
    if r.status_code == 201:
        return r.json()["department_id"]
    listing = auth_client.get(
        "/api/auth/v1/departments",
        headers={"Authorization": f"Bearer {admin_token}"},
    ).json()
    return next(d["department_id"] for d in listing if d["name"] == name)


def _ensure_service(auth_client, admin_token, name) -> None:
    auth_client.post(
        "/api/auth/v1/services",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"service_name": name, "display_name": name.title()},
    )


def _grant_service(auth_client, admin_token, dept_id, service) -> None:
    auth_client.post(
        f"/api/auth/v1/departments/{dept_id}/services",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"service_name": service},
    )


# ── department.service_grant ─────────────────────────────────────────────────

class TestDepartmentGrantAudit:
    def test_grant_emits_critical_event(
        self, auth_client: httpx.Client, logging_client: httpx.Client, admin_token: str,
    ):
        u = _u()
        dept_id = _ensure_dept(auth_client, admin_token, f"audit_grant_dept_{u}")
        _ensure_service(auth_client, admin_token, f"audit_grant_svc_{u}")

        since = datetime.now(timezone.utc)
        r = auth_client.post(
            f"/api/auth/v1/departments/{dept_id}/services",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"service_name": f"audit_grant_svc_{u}"},
        )
        assert r.status_code == 201, r.text

        ev = _find_event(
            logging_client, action="department.service_grant",
            target_id=dept_id, from_time=since,
        )
        assert ev["severity"] == "CRITICAL"
        assert ev["status"] == "success"
        assert ev["service"] == "auth_service"
        assert ev["target_id"] == dept_id
        assert ev["target_type"] == "department"
        assert ev["details"]["service_name"] == f"audit_grant_svc_{u}"


# ── service_role.* ───────────────────────────────────────────────────────────

class TestServiceRoleAudit:
    def test_create_role_emits_info_event(
        self, auth_client: httpx.Client, logging_client: httpx.Client, admin_token: str,
    ):
        u = _u()
        dept_id = _ensure_dept(auth_client, admin_token, f"svc_role_create_dept_{u}")
        svc = f"svc_role_create_{u}"
        role = f"custom_ev_{u}"
        _ensure_service(auth_client, admin_token, svc)
        _grant_service(auth_client, admin_token, dept_id, svc)

        since = datetime.now(timezone.utc)
        r = auth_client.post(
            f"/api/auth/v1/departments/{dept_id}/services/{svc}/roles",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"role_name": role, "display_name": "Custom"},
        )
        assert r.status_code == 201, r.text

        ev = _find_event(
            logging_client, action="service_role.create",
            from_time=since, details_match={"role_name": role, "service_name": svc},
        )
        assert ev["severity"] == "INFO"
        assert ev["details"]["department_id"] == dept_id

    def test_delete_role_emits_critical_event(
        self, auth_client: httpx.Client, logging_client: httpx.Client, admin_token: str,
    ):
        u = _u()
        dept_id = _ensure_dept(auth_client, admin_token, f"svc_role_del_dept_{u}")
        svc = f"svc_role_del_{u}"
        role = f"ephemeral_{u}"
        _ensure_service(auth_client, admin_token, svc)
        _grant_service(auth_client, admin_token, dept_id, svc)
        auth_client.post(
            f"/api/auth/v1/departments/{dept_id}/services/{svc}/roles",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"role_name": role, "display_name": "Eph"},
        )

        since = datetime.now(timezone.utc)
        r = auth_client.delete(
            f"/api/auth/v1/departments/{dept_id}/services/{svc}/roles/{role}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert r.status_code == 200, r.text

        ev = _find_event(
            logging_client, action="service_role.delete",
            from_time=since, details_match={"role_name": role, "service_name": svc},
        )
        assert ev["severity"] == "CRITICAL"
        assert ev["details"]["role_name"] == role
        assert ev["details"]["department_id"] == dept_id
        assert ev["details"]["auto_revoked_from_users"] is True
        assert ev["details"]["auto_revoked_from_bots"] is True


# ── group.* ──────────────────────────────────────────────────────────────────

class TestGroupAudit:
    def test_group_create_info_event(
        self, auth_client: httpx.Client, logging_client: httpx.Client, admin_token: str,
    ):
        u = _u()
        dept_id = _ensure_dept(auth_client, admin_token, f"audit_grp_dept_{u}")

        since = datetime.now(timezone.utc)
        r = auth_client.post(
            "/api/auth/v1/groups",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"department_id": dept_id, "name": f"audit_grp_{u}",
                  "display_name": "Audit Group"},
        )
        assert r.status_code == 201, r.text
        grp_id = r.json()["id"]

        ev = _find_event(
            logging_client, action="group.create",
            target_id=grp_id, from_time=since,
        )
        assert ev["severity"] == "INFO"
        assert ev["target_id"] == grp_id
        assert ev["target_type"] == "group"
        assert ev["details"]["department_id"] == dept_id

    def test_member_add_warning_event(
        self, auth_client: httpx.Client, logging_client: httpx.Client, admin_token: str,
    ):
        u = _u()
        dept_id = _ensure_dept(auth_client, admin_token, f"memb_dept_{u}")
        # Создаём юзера в том же отделе (нужен для добавления в группу)
        username = f"memb_user_{u}"
        ur = auth_client.post(
            "/api/auth/v1/users",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"username": username, "password": "Memb1234!", "department_id": dept_id},
        )
        assert ur.status_code in (201, 409), ur.text
        if ur.status_code == 201:
            user_id = ur.json()["user_id"]
        else:
            # ищем юзера через list-by-dept
            ls = auth_client.get(
                f"/api/auth/v1/users/department/{dept_id}",
                headers={"Authorization": f"Bearer {admin_token}"},
            ).json()
            user_id = next(u["user_id"] for u in ls if u["username"] == username)

        # Группа
        gr = auth_client.post(
            "/api/auth/v1/groups",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"department_id": dept_id, "name": f"memb_grp_{u}",
                  "display_name": "Memb"},
        )
        grp_id = gr.json()["id"]

        since = datetime.now(timezone.utc)
        r = auth_client.post(
            f"/api/auth/v1/groups/{grp_id}/members",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"user_id": user_id},
        )
        assert r.status_code == 201, r.text

        ev = _find_event(
            logging_client, action="group.member_add",
            target_id=grp_id, from_time=since,
        )
        assert ev["severity"] == "WARNING"
        assert ev["target_id"] == grp_id
        assert ev["details"]["user_id"] == user_id

    def test_group_roles_assign_critical_event(
        self, auth_client: httpx.Client, logging_client: httpx.Client, admin_token: str,
    ):
        u = _u()
        dept_id = _ensure_dept(auth_client, admin_token, f"grpr_dept_{u}")
        svc = f"grpr_svc_{u}"
        role = f"g_reader_{u}"
        _ensure_service(auth_client, admin_token, svc)
        _grant_service(auth_client, admin_token, dept_id, svc)
        # Создаём роль и группу
        auth_client.post(
            f"/api/auth/v1/departments/{dept_id}/services/{svc}/roles",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"role_name": role, "display_name": "GReader"},
        )
        gr = auth_client.post(
            "/api/auth/v1/groups",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"department_id": dept_id, "name": f"roles_grp_{u}", "display_name": "RG"},
        ).json()

        since = datetime.now(timezone.utc)
        r = auth_client.post(
            f"/api/auth/v1/groups/{gr['id']}/roles",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"service_name": svc, "roles": [role]},
        )
        assert r.status_code == 201, r.text

        ev = _find_event(
            logging_client, action="group.roles_assign",
            target_id=gr["id"], from_time=since,
        )
        assert ev["severity"] == "CRITICAL"
        assert ev["target_id"] == gr["id"]
        assert ev["details"]["service_name"] == svc
        assert ev["details"]["roles"] == [role]

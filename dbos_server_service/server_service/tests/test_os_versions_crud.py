"""Интеграционные тесты `/api/server/v1/os-versions` (глобальный каталог).

OS-версии — глобальный каталог: dept-isolation НЕ работает.
Чтение (list / get по id / get по имени) доступно любому аутентифицированному
актору (токен обязателен, без проверки доступа департамента к server_service);
аноним без bearer'а → 401. На чтении аудита нет. Запись
(create/update/delete) — под матрицей прав (seed-миграция).
"""

from __future__ import annotations

import pytest

from tests._helpers import assert_error, auth_hdr as _hdr

BASE = "/api/server/v1/os-versions"


def _payload(**overrides):
    base = {"name": "astra-1.7", "description": "Astra Linux SE 1.7"}
    base.update(overrides)
    return base


@pytest.fixture
def captured_emits(monkeypatch):
    """Захватывает вызовы audit_service.emit (call kwargs)."""
    from tests._helpers import make_emit_capture

    return make_emit_capture(monkeypatch)


class TestListOpenApiSchema:
    """`GET /os-versions` должен нести типизированный response-schema, а не
    пустую `{}` (иначе генераторы клиентов не видят envelope)."""

    def test_list_response_schema_is_typed(self):
        from src.main import app

        spec = app.openapi()
        get_op = spec["paths"][BASE]["get"]
        schema = get_op["responses"]["200"]["content"]["application/json"]["schema"]
        assert schema != {}
        # Union двух envelope'ов — anyOf из двух $ref.
        refs = {opt.get("$ref") for opt in schema.get("anyOf", [])}
        assert any(r and "PaginatedResponse_OsVersionResponse_" in r for r in refs)
        assert any(r and "CursorPaginatedResponse_OsVersionResponse_" in r for r in refs)

    def test_os_version_schema_fields(self):
        from src.main import app

        spec = app.openapi()
        props = spec["components"]["schemas"]["OsVersionResponse"]["properties"]
        assert {"id", "name", "repositories", "discovered_at"} <= set(props)


# ── POST ────────────────────────────────────────────────────────────────────

class TestCreateOsVersion:
    async def test_admin_creates(self, client, admin_role_token_a):
        resp = await client.post(
            BASE, headers=_hdr(admin_role_token_a), json=_payload(),
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "astra-1.7"
        assert body["id"].startswith("osv_")
        assert body["repositories"] == []

    async def test_admin_creates_with_repositories(self, client, admin_role_token_a):
        repos = [
            "https://dl.astralinux.ru/astra/stable/1.7/repository-main",
            "https://dl.astralinux.ru/astra/stable/1.7/repository-extended",
        ]
        resp = await client.post(
            BASE,
            headers=_hdr(admin_role_token_a),
            json=_payload(name="astra-1.7-repos", repositories=repos),
        )
        assert resp.status_code == 201
        assert resp.json()["repositories"] == repos

    async def test_reader_cannot_create(self, client, reader_token_a):
        resp = await client.post(
            BASE, headers=_hdr(reader_token_a), json=_payload(name="x"),
        )
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_operator_cannot_create(self, client, operator_token_a):
        """operator не имеет create на os_version."""
        resp = await client.post(
            BASE, headers=_hdr(operator_token_a), json=_payload(name="y"),
        )
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_no_token_returns_401(self, client):
        resp = await client.post(BASE, json=_payload())
        assert_error(resp, 401, "ACCESS_TOKEN_MISSING")

    async def test_duplicate_name_conflict(self, client, admin_role_token_a):
        resp = await client.post(
            BASE, headers=_hdr(admin_role_token_a), json=_payload(name="ubuntu-22.04"),
        )
        assert resp.status_code == 201
        resp2 = await client.post(
            BASE, headers=_hdr(admin_role_token_a), json=_payload(name="ubuntu-22.04"),
        )
        assert_error(resp2, 409, "OS_VERSION_DUPLICATE")


# ── Валидация repositories ────────────────────────────────────────────────────

class TestRepositoriesValidation:
    @pytest.mark.parametrize("bad_repo", [
        "ftp://repo.example.org/a",
        "not-a-url",
        "repo.example.org/a",
        "https://",
        "",
        "javascript:alert(1)",
    ])
    async def test_create_rejects_invalid_repo_url(
        self, client, admin_role_token_a, bad_repo,
    ):
        resp = await client.post(
            BASE,
            headers=_hdr(admin_role_token_a),
            json=_payload(name="osv-bad-repo", repositories=[bad_repo]),
        )
        assert_error(resp, 422, "VALIDATION_ERROR")

    async def test_create_rejects_too_many_repos(self, client, admin_role_token_a):
        repos = [f"https://repo.example.org/{i}" for i in range(65)]
        resp = await client.post(
            BASE,
            headers=_hdr(admin_role_token_a),
            json=_payload(name="osv-too-many", repositories=repos),
        )
        assert_error(resp, 422, "VALIDATION_ERROR")

    async def test_create_rejects_overlong_repo_url(self, client, admin_role_token_a):
        long_url = "https://repo.example.org/" + "a" * 2100
        resp = await client.post(
            BASE,
            headers=_hdr(admin_role_token_a),
            json=_payload(name="osv-long", repositories=[long_url]),
        )
        assert_error(resp, 422, "VALIDATION_ERROR")

    async def test_create_accepts_http_and_https(self, client, admin_role_token_a):
        repos = ["http://repo.example.org/a", "https://repo.example.org/b"]
        resp = await client.post(
            BASE,
            headers=_hdr(admin_role_token_a),
            json=_payload(name="osv-good-repos", repositories=repos),
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["repositories"] == repos

    async def test_update_rejects_invalid_repo_url(self, client, admin_role_token_a):
        created = await client.post(
            BASE, headers=_hdr(admin_role_token_a), json=_payload(name="osv-upd-bad"),
        )
        ov_id = created.json()["id"]
        resp = await client.patch(
            f"{BASE}/{ov_id}",
            headers=_hdr(admin_role_token_a),
            json={"repositories": ["ftp://repo.example.org/x"]},
        )
        assert_error(resp, 422, "VALIDATION_ERROR")


# ── GET (list) — любой аутентифицированный актор ──────────────────────────────

class TestListOsVersions:
    async def test_list_with_token(self, client, admin_role_token_a, no_role_token_a):
        for i in range(3):
            await client.post(
                BASE, headers=_hdr(admin_role_token_a),
                json=_payload(name=f"osv-list-{i}"),
            )
        resp = await client.get(BASE, headers=_hdr(no_role_token_a))
        assert resp.status_code == 200
        assert resp.json()["total"] >= 3

    async def test_pagination(self, client, admin_role_token_a, no_role_token_a):
        for i in range(5):
            await client.post(
                BASE, headers=_hdr(admin_role_token_a),
                json=_payload(name=f"osv-page-{i}"),
            )
        resp = await client.get(
            BASE, params={"limit": 2, "offset": 0}, headers=_hdr(no_role_token_a),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["limit"] == 2
        assert len(body["items"]) == 2

    async def test_no_role_token_still_lists(self, client, no_role_token_a):
        """Носитель токена без ролей читает каталог: read открыт всем аутентифицированным."""
        resp = await client.get(BASE, headers=_hdr(no_role_token_a))
        assert resp.status_code == 200

    async def test_anonymous_list_rejected_401(self, client):
        """Аноним без bearer'а → 401 ACCESS_TOKEN_MISSING."""
        resp = await client.get(BASE)
        assert_error(resp, 401, "ACCESS_TOKEN_MISSING")

    async def test_authenticated_list_emits_no_audit(
        self, client, no_role_token_a, captured_emits,
    ):
        """Authenticated read проходит без записи в audit (общий каталог)."""
        resp = await client.get(BASE, headers=_hdr(no_role_token_a))
        assert resp.status_code == 200
        os_events = [e for e in captured_emits if e["action"].startswith("os_version")]
        assert os_events == []


# ── GET /{id} — любой аутентифицированный актор ───────────────────────────────

class TestGetOsVersion:
    async def test_get_with_token(self, client, admin_role_token_a, no_role_token_a):
        created = await client.post(
            BASE, headers=_hdr(admin_role_token_a), json=_payload(name="osv-get-1"),
        )
        ov_id = created.json()["id"]
        resp = await client.get(f"{BASE}/{ov_id}", headers=_hdr(no_role_token_a))
        assert resp.status_code == 200
        assert resp.json()["id"] == ov_id

    async def test_get_returns_repositories(self, client, admin_role_token_a, no_role_token_a):
        repos = ["https://repo.example.org/astra/main"]
        created = await client.post(
            BASE,
            headers=_hdr(admin_role_token_a),
            json=_payload(name="osv-get-repos", repositories=repos),
        )
        ov_id = created.json()["id"]
        resp = await client.get(f"{BASE}/{ov_id}", headers=_hdr(no_role_token_a))
        assert resp.status_code == 200
        assert resp.json()["repositories"] == repos

    async def test_nonexistent_returns_404(self, client, no_role_token_a):
        resp = await client.get(f"{BASE}/osv_ghost", headers=_hdr(no_role_token_a))
        assert_error(resp, 404, "OS_VERSION_NOT_FOUND")

    async def test_anonymous_get_rejected_401(self, client, admin_role_token_a):
        """Аноним без bearer'а на карточку → 401."""
        created = await client.post(
            BASE, headers=_hdr(admin_role_token_a), json=_payload(name="osv-get-anon"),
        )
        ov_id = created.json()["id"]
        resp = await client.get(f"{BASE}/{ov_id}")
        assert_error(resp, 401, "ACCESS_TOKEN_MISSING")

    async def test_authenticated_get_emits_no_audit(
        self, client, admin_role_token_a, no_role_token_a, captured_emits,
    ):
        created = await client.post(
            BASE, headers=_hdr(admin_role_token_a), json=_payload(name="osv-get-auth"),
        )
        ov_id = created.json()["id"]
        captured_emits.clear()
        resp = await client.get(f"{BASE}/{ov_id}", headers=_hdr(no_role_token_a))
        assert resp.status_code == 200
        os_events = [e for e in captured_emits if e["action"].startswith("os_version")]
        assert os_events == []


# ── GET /by-name/{name} — любой аутентифицированный актор ──────────────────────

class TestGetOsVersionByName:
    async def test_get_by_name_with_token(self, client, admin_role_token_a, no_role_token_a):
        await client.post(
            BASE, headers=_hdr(admin_role_token_a), json=_payload(name="osv-by-name-1"),
        )
        resp = await client.get(
            f"{BASE}/by-name/osv-by-name-1", headers=_hdr(no_role_token_a),
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "osv-by-name-1"

    async def test_by_name_nonexistent_returns_404(self, client, no_role_token_a):
        resp = await client.get(
            f"{BASE}/by-name/ghost-os-name", headers=_hdr(no_role_token_a),
        )
        assert_error(resp, 404, "OS_VERSION_NOT_FOUND")

    async def test_anonymous_by_name_rejected_401(self, client, admin_role_token_a):
        """Аноним без bearer'а на by-name → 401."""
        await client.post(
            BASE, headers=_hdr(admin_role_token_a), json=_payload(name="osv-by-name-anon"),
        )
        resp = await client.get(f"{BASE}/by-name/osv-by-name-anon")
        assert_error(resp, 401, "ACCESS_TOKEN_MISSING")

    async def test_authenticated_by_name_emits_no_audit(
        self, client, admin_role_token_a, no_role_token_a, captured_emits,
    ):
        await client.post(
            BASE, headers=_hdr(admin_role_token_a), json=_payload(name="osv-by-name-auth"),
        )
        captured_emits.clear()
        resp = await client.get(
            f"{BASE}/by-name/osv-by-name-auth", headers=_hdr(no_role_token_a),
        )
        assert resp.status_code == 200
        os_events = [e for e in captured_emits if e["action"].startswith("os_version")]
        assert os_events == []


# ── PATCH ──────────────────────────────────────────────────────────────────

class TestUpdateOsVersion:
    async def test_admin_updates(self, client, admin_role_token_a):
        created = await client.post(
            BASE, headers=_hdr(admin_role_token_a), json=_payload(name="osv-upd-1"),
        )
        ov_id = created.json()["id"]
        resp = await client.patch(
            f"{BASE}/{ov_id}",
            headers=_hdr(admin_role_token_a),
            json={"description": "updated desc"},
        )
        assert resp.status_code == 200
        assert resp.json()["description"] == "updated desc"

    async def test_admin_updates_repositories(self, client, admin_role_token_a):
        created = await client.post(
            BASE, headers=_hdr(admin_role_token_a), json=_payload(name="osv-upd-repos"),
        )
        ov_id = created.json()["id"]
        new_repos = ["https://repo.example.org/a", "https://repo.example.org/b"]
        resp = await client.patch(
            f"{BASE}/{ov_id}",
            headers=_hdr(admin_role_token_a),
            json={"repositories": new_repos},
        )
        assert resp.status_code == 200
        assert resp.json()["repositories"] == new_repos

    async def test_reader_cannot_update(self, client, reader_token_a, admin_role_token_a):
        created = await client.post(
            BASE, headers=_hdr(admin_role_token_a), json=_payload(name="osv-ro-1"),
        )
        ov_id = created.json()["id"]
        resp = await client.patch(
            f"{BASE}/{ov_id}", headers=_hdr(reader_token_a),
            json={"description": "no"},
        )
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_no_token_cannot_update(self, client, admin_role_token_a):
        created = await client.post(
            BASE, headers=_hdr(admin_role_token_a), json=_payload(name="osv-noauth-upd"),
        )
        ov_id = created.json()["id"]
        resp = await client.patch(f"{BASE}/{ov_id}", json={"description": "no"})
        assert_error(resp, 401, "ACCESS_TOKEN_MISSING")

    async def test_empty_update_is_noop(self, client, admin_role_token_a):
        created = await client.post(
            BASE, headers=_hdr(admin_role_token_a), json=_payload(name="osv-noop"),
        )
        ov_id = created.json()["id"]
        resp = await client.patch(
            f"{BASE}/{ov_id}", headers=_hdr(admin_role_token_a), json={},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "osv-noop"

    async def test_update_to_existing_name_conflict(self, client, admin_role_token_a):
        await client.post(
            BASE, headers=_hdr(admin_role_token_a), json=_payload(name="osv-existing"),
        )
        created = await client.post(
            BASE, headers=_hdr(admin_role_token_a), json=_payload(name="osv-to-rename"),
        )
        ov_id = created.json()["id"]
        resp = await client.patch(
            f"{BASE}/{ov_id}",
            headers=_hdr(admin_role_token_a),
            json={"name": "osv-existing"},
        )
        assert_error(resp, 409, "OS_VERSION_DUPLICATE")

    async def test_nonexistent_returns_404(self, client, admin_role_token_a):
        resp = await client.patch(
            f"{BASE}/osv_ghost", headers=_hdr(admin_role_token_a),
            json={"description": "x"},
        )
        assert_error(resp, 404, "OS_VERSION_NOT_FOUND")


# ── DELETE ─────────────────────────────────────────────────────────────────

class TestDeleteOsVersion:
    async def test_admin_deletes(self, client, admin_role_token_a):
        created = await client.post(
            BASE, headers=_hdr(admin_role_token_a), json=_payload(name="osv-del-1"),
        )
        ov_id = created.json()["id"]
        resp = await client.delete(f"{BASE}/{ov_id}", headers=_hdr(admin_role_token_a))
        assert resp.status_code == 200

    async def test_reader_cannot_delete(self, client, reader_token_a, admin_role_token_a):
        created = await client.post(
            BASE, headers=_hdr(admin_role_token_a), json=_payload(name="osv-del-ro"),
        )
        ov_id = created.json()["id"]
        resp = await client.delete(f"{BASE}/{ov_id}", headers=_hdr(reader_token_a))
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_no_token_cannot_delete(self, client, admin_role_token_a):
        created = await client.post(
            BASE, headers=_hdr(admin_role_token_a), json=_payload(name="osv-noauth-del"),
        )
        ov_id = created.json()["id"]
        resp = await client.delete(f"{BASE}/{ov_id}")
        assert_error(resp, 401, "ACCESS_TOKEN_MISSING")

    async def test_delete_in_use_returns_409(
        self, client, admin_role_token_a, make_server, db,
    ):
        """FK ondelete=RESTRICT — server ссылается → OS_VERSION_IN_USE."""
        created = await client.post(
            BASE, headers=_hdr(admin_role_token_a), json=_payload(name="osv-in-use"),
        )
        ov_id = created.json()["id"]
        srv = await make_server(department_id="dep_a")
        srv.os_version_id = ov_id
        await db.flush()
        resp = await client.delete(
            f"{BASE}/{ov_id}", headers=_hdr(admin_role_token_a),
        )
        assert_error(resp, 409, "OS_VERSION_IN_USE")

    async def test_delete_nonexistent_returns_404(self, client, admin_role_token_a):
        resp = await client.delete(
            f"{BASE}/osv_ghost", headers=_hdr(admin_role_token_a),
        )
        assert_error(resp, 404, "OS_VERSION_NOT_FOUND")

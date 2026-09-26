"""Тесты `/api/testing/v1/global-variables` — платформенный каталог переменных.

Каталог не per-department: dept-изоляции нет. Чтение доступно любому
аутентифицированному актору, аноним → 401. Запись — под матрицей прав
(seed-миграция даёт её системной роли `admin`).
"""

from __future__ import annotations

import pytest

from tests.conftest import auth_hdr as _hdr

BASE = "/api/testing/v1/global-variables"


def _payload(**overrides) -> dict:
    base = {
        "code": "MY_VAR",
        "label": "Моя переменная",
        "source": "launch_context",
    }
    base.update(overrides)
    return base


# ── Сиды миграции ───────────────────────────────────────────────────────────

class TestSeededVariables:
    async def test_mandatory_variables_present(self, client, no_role_token):
        resp = await client.get(BASE, params={"limit": 500}, headers=_hdr(no_role_token))
        assert resp.status_code == 200, resp.text
        codes = {item["code"] for item in resp.json()["items"]}
        assert {
            "RC", "STAND", "KERNEL", "MODE", "TESTENV",
            "HOME_DIR", "TEST_USER", "TEST_PASSWORD", "TEST_SSH_KEY",
        } <= codes

    async def test_mode_has_static_choices_source(self, client, no_role_token):
        resp = await client.get(f"{BASE}/by-code/MODE", headers=_hdr(no_role_token))
        assert resp.status_code == 200
        assert resp.json()["choices_source"] == 'static:["orel","smolensk"]'

    @pytest.mark.parametrize("code", ["TEST_PASSWORD", "TEST_SSH_KEY"])
    async def test_credential_variables_are_sensitive(self, client, no_role_token, code):
        resp = await client.get(f"{BASE}/by-code/{code}", headers=_hdr(no_role_token))
        assert resp.status_code == 200
        assert resp.json()["is_sensitive"] is True

    async def test_rc_and_kernel_use_dynamic_resolvers(self, client, no_role_token):
        rc = await client.get(f"{BASE}/by-code/RC", headers=_hdr(no_role_token))
        kernel = await client.get(f"{BASE}/by-code/KERNEL", headers=_hdr(no_role_token))
        assert rc.json()["choices_source"] == "dynamic:os_versions"
        assert kernel.json()["choices_source"] == "dynamic:kernels"

    async def test_response_carries_no_value_field(self, client, no_role_token):
        resp = await client.get(f"{BASE}/by-code/TEST_PASSWORD", headers=_hdr(no_role_token))
        body = resp.json()
        # Каталог хранит описание переменной, а не её значение — маскировать
        # в карточке нечего, и никакого value-поля тут быть не должно.
        assert "value" not in body


# ── Чтение ──────────────────────────────────────────────────────────────────

class TestReadAccess:
    async def test_anonymous_gets_401(self, client):
        resp = await client.get(BASE)
        assert resp.status_code == 401
        assert resp.json()["error_code"] == "ACCESS_TOKEN_MISSING"

    async def test_user_without_roles_can_read(self, client, no_role_token):
        resp = await client.get(BASE, headers=_hdr(no_role_token))
        assert resp.status_code == 200

    async def test_get_by_id(self, client, no_role_token):
        listed = await client.get(BASE, headers=_hdr(no_role_token))
        first = listed.json()["items"][0]
        resp = await client.get(f"{BASE}/{first['id']}", headers=_hdr(no_role_token))
        assert resp.status_code == 200
        assert resp.json()["id"] == first["id"]

    async def test_unknown_id_404(self, client, no_role_token):
        resp = await client.get(f"{BASE}/gvar_nope", headers=_hdr(no_role_token))
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "GLOBAL_VARIABLE_NOT_FOUND"


# ── POST ────────────────────────────────────────────────────────────────────

class TestCreate:
    async def test_admin_creates(self, client, admin_token):
        resp = await client.post(BASE, headers=_hdr(admin_token), json=_payload())
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["code"] == "MY_VAR"
        assert body["id"].startswith("gvar_")
        assert body["value_type"] == "string"
        assert body["is_sensitive"] is False

    async def test_user_without_role_gets_403(self, client, no_role_token):
        resp = await client.post(BASE, headers=_hdr(no_role_token), json=_payload())
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "PERMISSION_DENIED"

    async def test_guest_cannot_create(self, client, guest_token):
        resp = await client.post(BASE, headers=_hdr(guest_token), json=_payload())
        assert resp.status_code == 403

    async def test_duplicate_code_409(self, client, admin_token):
        first = await client.post(BASE, headers=_hdr(admin_token), json=_payload())
        assert first.status_code == 201
        second = await client.post(BASE, headers=_hdr(admin_token), json=_payload())
        assert second.status_code == 409
        assert second.json()["error_code"] == "GLOBAL_VARIABLE_DUPLICATE"

    async def test_collision_with_seeded_code_409(self, client, admin_token):
        resp = await client.post(BASE, headers=_hdr(admin_token), json=_payload(code="RC"))
        assert resp.status_code == 409

    async def test_lowercase_code_rejected(self, client, admin_token):
        resp = await client.post(BASE, headers=_hdr(admin_token), json=_payload(code="my_var"))
        assert resp.status_code == 422

    async def test_unknown_source_rejected(self, client, admin_token):
        resp = await client.post(
            BASE, headers=_hdr(admin_token), json=_payload(source="from_the_void"),
        )
        assert resp.status_code == 422

    async def test_sensitive_flag_persisted(self, client, admin_token):
        resp = await client.post(
            BASE, headers=_hdr(admin_token),
            json=_payload(code="MY_SECRET", source="secret_service", is_sensitive=True),
        )
        assert resp.status_code == 201
        assert resp.json()["is_sensitive"] is True

    async def test_static_choices_source_accepted_without_parsing(self, client, admin_token):
        resp = await client.post(
            BASE, headers=_hdr(admin_token),
            json=_payload(choices_source='static:["ram","sd"]'),
        )
        assert resp.status_code == 201
        assert resp.json()["choices_source"] == 'static:["ram","sd"]'

    async def test_unknown_dynamic_resolver_rejected(self, client, admin_token):
        resp = await client.post(
            BASE, headers=_hdr(admin_token),
            json=_payload(choices_source="dynamic:department_credential"),
        )
        assert resp.status_code == 422
        assert resp.json()["error_code"] == "CHOICES_SOURCE_INVALID"

    async def test_bare_choices_source_rejected(self, client, admin_token):
        resp = await client.post(
            BASE, headers=_hdr(admin_token), json=_payload(choices_source='["a","b"]'),
        )
        assert resp.status_code == 422


# ── PATCH ───────────────────────────────────────────────────────────────────

class TestUpdate:
    @pytest.fixture
    async def variable_id(self, client, admin_token) -> str:
        resp = await client.post(BASE, headers=_hdr(admin_token), json=_payload())
        assert resp.status_code == 201
        return resp.json()["id"]

    async def test_admin_updates_label(self, client, admin_token, variable_id):
        resp = await client.patch(
            f"{BASE}/{variable_id}", headers=_hdr(admin_token),
            json={"label": "Переименована"},
        )
        assert resp.status_code == 200
        assert resp.json()["label"] == "Переименована"

    async def test_toggle_sensitive(self, client, admin_token, variable_id):
        resp = await client.patch(
            f"{BASE}/{variable_id}", headers=_hdr(admin_token), json={"is_sensitive": True},
        )
        assert resp.status_code == 200
        assert resp.json()["is_sensitive"] is True

    async def test_no_role_gets_403(self, client, no_role_token, variable_id):
        resp = await client.patch(
            f"{BASE}/{variable_id}", headers=_hdr(no_role_token), json={"label": "x"},
        )
        assert resp.status_code == 403

    async def test_empty_body_is_noop(self, client, admin_token, variable_id):
        resp = await client.patch(f"{BASE}/{variable_id}", headers=_hdr(admin_token), json={})
        assert resp.status_code == 200
        assert resp.json()["label"] == "Моя переменная"

    async def test_unknown_id_404(self, client, admin_token):
        resp = await client.patch(
            f"{BASE}/gvar_nope", headers=_hdr(admin_token), json={"label": "x"},
        )
        assert resp.status_code == 404

    async def test_code_collision_409(self, client, admin_token, variable_id):
        resp = await client.patch(
            f"{BASE}/{variable_id}", headers=_hdr(admin_token), json={"code": "RC"},
        )
        assert resp.status_code == 409

    async def test_invalid_choices_source_rejected(self, client, admin_token, variable_id):
        resp = await client.patch(
            f"{BASE}/{variable_id}", headers=_hdr(admin_token),
            json={"choices_source": "dynamic:whatever"},
        )
        assert resp.status_code == 422
        assert resp.json()["error_code"] == "CHOICES_SOURCE_INVALID"

    async def test_choices_source_can_be_cleared(self, client, admin_token, variable_id):
        set_resp = await client.patch(
            f"{BASE}/{variable_id}", headers=_hdr(admin_token),
            json={"choices_source": 'static:["a"]'},
        )
        assert set_resp.status_code == 200
        clear_resp = await client.patch(
            f"{BASE}/{variable_id}", headers=_hdr(admin_token),
            json={"choices_source": None},
        )
        assert clear_resp.status_code == 200
        assert clear_resp.json()["choices_source"] is None


# ── DELETE ──────────────────────────────────────────────────────────────────

class TestDelete:
    @pytest.fixture
    async def variable_id(self, client, admin_token) -> str:
        resp = await client.post(BASE, headers=_hdr(admin_token), json=_payload())
        assert resp.status_code == 201
        return resp.json()["id"]

    async def test_admin_deletes(self, client, admin_token, variable_id, no_role_token):
        resp = await client.delete(f"{BASE}/{variable_id}", headers=_hdr(admin_token))
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        gone = await client.get(f"{BASE}/{variable_id}", headers=_hdr(no_role_token))
        assert gone.status_code == 404

    async def test_no_role_gets_403(self, client, no_role_token, variable_id):
        resp = await client.delete(f"{BASE}/{variable_id}", headers=_hdr(no_role_token))
        assert resp.status_code == 403

    async def test_unknown_id_404(self, client, admin_token):
        resp = await client.delete(f"{BASE}/gvar_nope", headers=_hdr(admin_token))
        assert resp.status_code == 404


# ── Справочник форм source_ref ──────────────────────────────────────

class TestSourceOptions:
    async def test_lists_what_the_validator_accepts(self, client, no_role_token):
        from src.services import variable_resolver as vr

        resp = await client.get(f"{BASE}/source-options", headers=_hdr(no_role_token))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert {"template", "test_field", "stand", "department_integration", "os_version",
                "test_account", "zephyr_folder", "static", "launch_context"} <= set(body["sources"])
        assert body["test_fields"] == sorted(vr.TEST_FIELDS)
        assert body["stand_fields"] == sorted(vr.STAND_FIELDS)
        assert body["template_conditions"] == ["debug", "not_debug"]
        di = {row["field"]: row["is_credential"] for row in body["department_integration_fields"]}
        assert set(di) == vr.DEPARTMENT_INTEGRATION_FIELDS
        assert di["credential_id"] is True and di["confluence_credential_id"] is True
        assert di["stp_matrix_confluence_space"] is False
        # Каждое перечисленное поле действительно проходит валидацию.
        for name in body["test_fields"]:
            vr.validate_source_ref("test_field", {"field": name}, is_sensitive=False)
        for row in body["department_integration_fields"]:
            ref = {"field": row["field"]}
            if row["is_credential"]:
                ref["credential_part"] = "login"
            vr.validate_source_ref("department_integration", ref, is_sensitive=False)

    async def test_anonymous_is_401(self, client):
        resp = await client.get(f"{BASE}/source-options")
        assert resp.status_code == 401


# ── OpenAPI ─────────────────────────────────────────────────────────────────

class TestOpenApiSchema:
    def test_list_response_schema_is_typed(self):
        from src.main import app

        spec = app.openapi()
        get_op = spec["paths"][BASE]["get"]
        schema = get_op["responses"]["200"]["content"]["application/json"]["schema"]
        assert "PaginatedResponse_GlobalVariableResponse_" in schema.get("$ref", "")

    def test_variable_schema_fields(self):
        from src.main import app

        spec = app.openapi()
        props = spec["components"]["schemas"]["GlobalVariableResponse"]["properties"]
        assert {
            "id", "code", "label", "source", "value_type",
            "choices_source", "is_sensitive", "description",
        } <= set(props)

"""Тесты `/api/testing/v1/test-definitions/{test_id}/args` — слоты команды.

Слоты не заводят отдельную матрицу прав — запись защищена
`(test_definition, *, update)`, тем же action, что и правка самого теста.
"""

from __future__ import annotations

import uuid

from tests.conftest import auth_hdr as _hdr

TESTS_BASE = "/api/testing/v1/test-definitions"
VARS_BASE = "/api/testing/v1/global-variables"


async def _create_test(client, admin_token) -> str:
    resp = await client.post(
        TESTS_BASE, headers=_hdr(admin_token),
        json={"code": f"cmd.test.{uuid.uuid4().hex[:8]}", "full_name": "Тест конструктора"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _variable_id(client, token, code: str = "TESTENV") -> str:
    resp = await client.get(f"{VARS_BASE}/by-code/{code}", headers=_hdr(token))
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _args_url(test_id: str, arg_id: str | None = None) -> str:
    base = f"{TESTS_BASE}/{test_id}/args"
    return f"{base}/{arg_id}" if arg_id else base


# ── Чтение ──────────────────────────────────────────────────────────────────

class TestReadAccess:
    async def test_anonymous_gets_401(self, client, admin_token):
        test_id = await _create_test(client, admin_token)
        resp = await client.get(_args_url(test_id))
        assert resp.status_code == 401

    async def test_empty_test_has_no_args(self, client, admin_token, no_role_token):
        test_id = await _create_test(client, admin_token)
        resp = await client.get(_args_url(test_id), headers=_hdr(no_role_token))
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_unknown_test_404(self, client, no_role_token):
        resp = await client.get(_args_url("tdef_nope"), headers=_hdr(no_role_token))
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "TEST_DEFINITION_NOT_FOUND"

    async def test_list_ordered_by_position(self, client, admin_token, no_role_token):
        test_id = await _create_test(client, admin_token)
        for position, value in [(2, "--third"), (0, "--first"), (1, "--second")]:
            resp = await client.post(
                _args_url(test_id), headers=_hdr(admin_token),
                json={"kind": "literal", "literal_value": value, "position": position},
            )
            assert resp.status_code == 201, resp.text

        listed = await client.get(_args_url(test_id), headers=_hdr(no_role_token))
        assert listed.status_code == 200
        values = [item["literal_value"] for item in listed.json()]
        assert values == ["--first", "--second", "--third"]


# ── POST ────────────────────────────────────────────────────────────────────

class TestCreate:
    async def test_literal_slot(self, client, admin_token):
        test_id = await _create_test(client, admin_token)
        resp = await client.post(
            _args_url(test_id), headers=_hdr(admin_token),
            json={"kind": "literal", "literal_value": "--rc"},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["id"].startswith("targ_")
        assert body["kind"] == "literal"
        assert body["literal_value"] == "--rc"
        assert body["variable_id"] is None
        assert body["position"] == 0

    async def test_variable_slot(self, client, admin_token):
        test_id = await _create_test(client, admin_token)
        variable_id = await _variable_id(client, admin_token)
        resp = await client.post(
            _args_url(test_id), headers=_hdr(admin_token),
            json={"kind": "variable", "variable_id": variable_id},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["kind"] == "variable"
        assert body["variable_id"] == variable_id
        assert body["literal_value"] is None

    async def test_variable_slot_with_override(self, client, admin_token):
        test_id = await _create_test(client, admin_token)
        variable_id = await _variable_id(client, admin_token)
        resp = await client.post(
            _args_url(test_id), headers=_hdr(admin_token),
            json={"kind": "variable", "variable_id": variable_id, "override_value": "custom"},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["override_value"] == "custom"

    async def test_default_position_appends_to_end(self, client, admin_token):
        test_id = await _create_test(client, admin_token)
        first = await client.post(
            _args_url(test_id), headers=_hdr(admin_token),
            json={"kind": "literal", "literal_value": "--a"},
        )
        second = await client.post(
            _args_url(test_id), headers=_hdr(admin_token),
            json={"kind": "literal", "literal_value": "--b"},
        )
        assert first.json()["position"] == 0
        assert second.json()["position"] == 1

    async def test_literal_without_value_rejected(self, client, admin_token):
        test_id = await _create_test(client, admin_token)
        resp = await client.post(
            _args_url(test_id), headers=_hdr(admin_token), json={"kind": "literal"},
        )
        assert resp.status_code == 422
        assert resp.json()["error_code"] == "COMMAND_ARG_KIND_MISMATCH"

    async def test_literal_with_variable_id_rejected(self, client, admin_token):
        test_id = await _create_test(client, admin_token)
        variable_id = await _variable_id(client, admin_token)
        resp = await client.post(
            _args_url(test_id), headers=_hdr(admin_token),
            json={"kind": "literal", "literal_value": "--x", "variable_id": variable_id},
        )
        assert resp.status_code == 422
        assert resp.json()["error_code"] == "COMMAND_ARG_KIND_MISMATCH"

    async def test_variable_without_id_rejected(self, client, admin_token):
        test_id = await _create_test(client, admin_token)
        resp = await client.post(
            _args_url(test_id), headers=_hdr(admin_token), json={"kind": "variable"},
        )
        assert resp.status_code == 422
        assert resp.json()["error_code"] == "COMMAND_ARG_KIND_MISMATCH"

    async def test_variable_with_literal_value_rejected(self, client, admin_token):
        test_id = await _create_test(client, admin_token)
        variable_id = await _variable_id(client, admin_token)
        resp = await client.post(
            _args_url(test_id), headers=_hdr(admin_token),
            json={"kind": "variable", "variable_id": variable_id, "literal_value": "--x"},
        )
        assert resp.status_code == 422
        assert resp.json()["error_code"] == "COMMAND_ARG_KIND_MISMATCH"

    async def test_unknown_test_id_404(self, client, admin_token):
        resp = await client.post(
            _args_url("tdef_nope"), headers=_hdr(admin_token),
            json={"kind": "literal", "literal_value": "--x"},
        )
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "TEST_DEFINITION_NOT_FOUND"

    async def test_unknown_variable_id_404(self, client, admin_token):
        test_id = await _create_test(client, admin_token)
        resp = await client.post(
            _args_url(test_id), headers=_hdr(admin_token),
            json={"kind": "variable", "variable_id": "gvar_nope"},
        )
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "GLOBAL_VARIABLE_NOT_FOUND"

    async def test_no_role_gets_403(self, client, admin_token, no_role_token):
        test_id = await _create_test(client, admin_token)
        resp = await client.post(
            _args_url(test_id), headers=_hdr(no_role_token),
            json={"kind": "literal", "literal_value": "--x"},
        )
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "PERMISSION_DENIED"

    async def test_guest_cannot_create(self, client, admin_token, guest_token):
        test_id = await _create_test(client, admin_token)
        resp = await client.post(
            _args_url(test_id), headers=_hdr(guest_token),
            json={"kind": "literal", "literal_value": "--x"},
        )
        assert resp.status_code == 403


# ── PATCH ───────────────────────────────────────────────────────────────────

class TestUpdate:
    async def _create_literal(self, client, admin_token, test_id: str, value: str = "--x") -> str:
        resp = await client.post(
            _args_url(test_id), headers=_hdr(admin_token),
            json={"kind": "literal", "literal_value": value},
        )
        assert resp.status_code == 201
        return resp.json()["id"]

    async def test_update_literal_value(self, client, admin_token):
        test_id = await _create_test(client, admin_token)
        arg_id = await self._create_literal(client, admin_token, test_id)
        resp = await client.patch(
            _args_url(test_id, arg_id), headers=_hdr(admin_token),
            json={"literal_value": "--changed"},
        )
        assert resp.status_code == 200
        assert resp.json()["literal_value"] == "--changed"

    async def test_update_position(self, client, admin_token):
        test_id = await _create_test(client, admin_token)
        arg_id = await self._create_literal(client, admin_token, test_id)
        resp = await client.patch(
            _args_url(test_id, arg_id), headers=_hdr(admin_token), json={"position": 5},
        )
        assert resp.status_code == 200
        assert resp.json()["position"] == 5

    async def test_switch_literal_to_variable(self, client, admin_token):
        test_id = await _create_test(client, admin_token)
        arg_id = await self._create_literal(client, admin_token, test_id)
        variable_id = await _variable_id(client, admin_token)
        resp = await client.patch(
            _args_url(test_id, arg_id), headers=_hdr(admin_token),
            json={"kind": "variable", "variable_id": variable_id, "literal_value": None},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["kind"] == "variable"
        assert body["variable_id"] == variable_id
        assert body["literal_value"] is None

    async def test_switch_kind_without_clearing_opposite_field_rejected(self, client, admin_token):
        test_id = await _create_test(client, admin_token)
        arg_id = await self._create_literal(client, admin_token, test_id)
        variable_id = await _variable_id(client, admin_token)
        resp = await client.patch(
            _args_url(test_id, arg_id), headers=_hdr(admin_token),
            json={"kind": "variable", "variable_id": variable_id},
        )
        assert resp.status_code == 422
        assert resp.json()["error_code"] == "COMMAND_ARG_KIND_MISMATCH"

    async def test_empty_body_is_noop(self, client, admin_token):
        test_id = await _create_test(client, admin_token)
        arg_id = await self._create_literal(client, admin_token, test_id)
        resp = await client.patch(_args_url(test_id, arg_id), headers=_hdr(admin_token), json={})
        assert resp.status_code == 200
        assert resp.json()["literal_value"] == "--x"

    async def test_unknown_arg_404(self, client, admin_token):
        test_id = await _create_test(client, admin_token)
        resp = await client.patch(
            _args_url(test_id, "targ_nope"), headers=_hdr(admin_token), json={"position": 1},
        )
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "TEST_COMMAND_ARG_NOT_FOUND"

    async def test_arg_from_other_test_404(self, client, admin_token):
        test_id_a = await _create_test(client, admin_token)
        test_id_b = await _create_test(client, admin_token)
        arg_id = await self._create_literal(client, admin_token, test_id_a)
        resp = await client.patch(
            _args_url(test_id_b, arg_id), headers=_hdr(admin_token), json={"position": 1},
        )
        assert resp.status_code == 404

    async def test_no_role_gets_403(self, client, admin_token, no_role_token):
        test_id = await _create_test(client, admin_token)
        arg_id = await self._create_literal(client, admin_token, test_id)
        resp = await client.patch(
            _args_url(test_id, arg_id), headers=_hdr(no_role_token), json={"position": 1},
        )
        assert resp.status_code == 403


# ── DELETE ──────────────────────────────────────────────────────────────────

class TestDelete:
    async def test_admin_deletes(self, client, admin_token, no_role_token):
        test_id = await _create_test(client, admin_token)
        created = await client.post(
            _args_url(test_id), headers=_hdr(admin_token),
            json={"kind": "literal", "literal_value": "--x"},
        )
        arg_id = created.json()["id"]
        resp = await client.delete(_args_url(test_id, arg_id), headers=_hdr(admin_token))
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        listed = await client.get(_args_url(test_id), headers=_hdr(no_role_token))
        assert listed.json() == []

    async def test_no_role_gets_403(self, client, admin_token, no_role_token):
        test_id = await _create_test(client, admin_token)
        created = await client.post(
            _args_url(test_id), headers=_hdr(admin_token),
            json={"kind": "literal", "literal_value": "--x"},
        )
        arg_id = created.json()["id"]
        resp = await client.delete(_args_url(test_id, arg_id), headers=_hdr(no_role_token))
        assert resp.status_code == 403

    async def test_unknown_arg_404(self, client, admin_token):
        test_id = await _create_test(client, admin_token)
        resp = await client.delete(_args_url(test_id, "targ_nope"), headers=_hdr(admin_token))
        assert resp.status_code == 404


# ── Удаление переменной, на которую ссылается слот ──────────────────────────

class TestVariableInUse:
    async def test_deleting_referenced_variable_conflicts(self, client, admin_token):
        variable_resp = await client.post(
            VARS_BASE, headers=_hdr(admin_token),
            json={
                "code": f"CMD_VAR_{uuid.uuid4().hex[:8].upper()}",
                "label": "Command var",
                "source": "launch_context",
            },
        )
        assert variable_resp.status_code == 201
        variable_id = variable_resp.json()["id"]

        test_id = await _create_test(client, admin_token)
        arg = await client.post(
            _args_url(test_id), headers=_hdr(admin_token),
            json={"kind": "variable", "variable_id": variable_id},
        )
        assert arg.status_code == 201

        delete_resp = await client.delete(f"{VARS_BASE}/{variable_id}", headers=_hdr(admin_token))
        assert delete_resp.status_code == 409
        assert delete_resp.json()["error_code"] == "GLOBAL_VARIABLE_IN_USE"

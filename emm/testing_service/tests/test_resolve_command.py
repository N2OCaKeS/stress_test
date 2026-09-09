"""Тесты `services.test_command_arg.resolve_command`.

Резолв не выставлен наружу отдельным HTTP-эндпоинтом — это внутренний вызов,
которым воркер (волна 5 плана миграции) достанет готовый `list[str]` перед
SSH-исполнением. Тесты зовут сервисный слой напрямую с открытой сессией, как
это сделает будущий воркер.
"""

from __future__ import annotations

import uuid

import pytest

from src.core.exceptions import DomainValidationError, NotFoundError
from src.db.session import AsyncSessionLocal
from src.services import test_command_arg as svc
from tests.conftest import auth_hdr as _hdr

TESTS_BASE = "/api/testing/v1/test-definitions"
VARS_BASE = "/api/testing/v1/global-variables"


async def _create_test(client, admin_token) -> str:
    resp = await client.post(
        TESTS_BASE, headers=_hdr(admin_token),
        json={"code": f"resolve.test.{uuid.uuid4().hex[:8]}", "full_name": "Резолв команды"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _add_literal(client, admin_token, test_id, value, position=None):
    payload: dict = {"kind": "literal", "literal_value": value}
    if position is not None:
        payload["position"] = position
    resp = await client.post(f"{TESTS_BASE}/{test_id}/args", headers=_hdr(admin_token), json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _add_variable(client, admin_token, test_id, variable_id, override_value=None, position=None):
    payload: dict = {"kind": "variable", "variable_id": variable_id}
    if override_value is not None:
        payload["override_value"] = override_value
    if position is not None:
        payload["position"] = position
    resp = await client.post(f"{TESTS_BASE}/{test_id}/args", headers=_hdr(admin_token), json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _variable_id(client, token, code: str) -> str:
    resp = await client.get(f"{VARS_BASE}/by-code/{code}", headers=_hdr(token))
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


class TestResolveCommand:
    async def test_literal_only(self, client, admin_token):
        test_id = await _create_test(client, admin_token)
        await _add_literal(client, admin_token, test_id, "--flag")
        async with AsyncSessionLocal() as db:
            args = await svc.resolve_command(db, test_id, {})
        assert args == ["--flag"]

    async def test_variable_without_override_resolves_from_launch_context(self, client, admin_token):
        test_id = await _create_test(client, admin_token)
        variable_id = await _variable_id(client, admin_token, "TESTENV")
        await _add_variable(client, admin_token, test_id, variable_id)
        async with AsyncSessionLocal() as db:
            args = await svc.resolve_command(db, test_id, {"TESTENV": "prod"})
        assert args == ["prod"]

    async def test_variable_with_override_ignores_launch_context(self, client, admin_token):
        test_id = await _create_test(client, admin_token)
        variable_id = await _variable_id(client, admin_token, "TESTENV")
        await _add_variable(client, admin_token, test_id, variable_id, override_value="forced")
        async with AsyncSessionLocal() as db:
            args = await svc.resolve_command(db, test_id, {"TESTENV": "prod"})
        assert args == ["forced"]

    async def test_variable_with_override_does_not_need_launch_context(self, client, admin_token):
        test_id = await _create_test(client, admin_token)
        variable_id = await _variable_id(client, admin_token, "TESTENV")
        await _add_variable(client, admin_token, test_id, variable_id, override_value="forced")
        async with AsyncSessionLocal() as db:
            args = await svc.resolve_command(db, test_id, {})
        assert args == ["forced"]

    async def test_mixed_slots_in_position_order(self, client, admin_token):
        test_id = await _create_test(client, admin_token)
        variable_id = await _variable_id(client, admin_token, "TESTENV")
        await _add_literal(client, admin_token, test_id, "backup_image.py", position=0)
        await _add_variable(client, admin_token, test_id, variable_id, position=1)
        await _add_literal(client, admin_token, test_id, "--dry-run", position=2)
        async with AsyncSessionLocal() as db:
            args = await svc.resolve_command(db, test_id, {"TESTENV": "prod"})
        assert args == ["backup_image.py", "prod", "--dry-run"]

    async def test_missing_launch_context_value_raises(self, client, admin_token):
        test_id = await _create_test(client, admin_token)
        variable_id = await _variable_id(client, admin_token, "TESTENV")
        await _add_variable(client, admin_token, test_id, variable_id)
        async with AsyncSessionLocal() as db:
            with pytest.raises(DomainValidationError) as exc:
                await svc.resolve_command(db, test_id, {})
        assert exc.value.error_code == "LAUNCH_CONTEXT_VARIABLE_MISSING"

    async def test_unknown_test_id_raises_not_found(self):
        async with AsyncSessionLocal() as db:
            with pytest.raises(NotFoundError) as exc:
                await svc.resolve_command(db, "tdef_nope", {})
        assert exc.value.error_code == "TEST_DEFINITION_NOT_FOUND"

    async def test_test_without_slots_resolves_to_empty_list(self, client, admin_token):
        test_id = await _create_test(client, admin_token)
        async with AsyncSessionLocal() as db:
            args = await svc.resolve_command(db, test_id, {})
        assert args == []

    async def test_returns_list_not_string(self, client, admin_token):
        test_id = await _create_test(client, admin_token)
        await _add_literal(client, admin_token, test_id, "a")
        await _add_literal(client, admin_token, test_id, "b", position=1)
        async with AsyncSessionLocal() as db:
            args = await svc.resolve_command(db, test_id, {})
        assert isinstance(args, list)
        assert args == ["a", "b"]


class TestResolveCommandMasked:
    async def test_sensitive_variable_is_masked(self, client, admin_token):
        test_id = await _create_test(client, admin_token)
        password_id = await _variable_id(client, admin_token, "TEST_PASSWORD")
        await _add_literal(client, admin_token, test_id, "--password", position=0)
        await _add_variable(client, admin_token, test_id, password_id, position=1)
        async with AsyncSessionLocal() as db:
            args = await svc.resolve_command(db, test_id, {"TEST_PASSWORD": "hunter2"})
            masked = await svc.resolve_command_masked(db, test_id, {"TEST_PASSWORD": "hunter2"})
        assert args == ["--password", "hunter2"]
        assert masked == ["--password", "***"]

    async def test_sensitive_variable_masked_even_with_override(self, client, admin_token):
        test_id = await _create_test(client, admin_token)
        key_id = await _variable_id(client, admin_token, "TEST_SSH_KEY")
        await _add_variable(client, admin_token, test_id, key_id, override_value="-----KEY-----")
        async with AsyncSessionLocal() as db:
            masked = await svc.resolve_command_masked(db, test_id, {})
        assert masked == ["***"]

    async def test_non_sensitive_variable_not_masked(self, client, admin_token):
        test_id = await _create_test(client, admin_token)
        variable_id = await _variable_id(client, admin_token, "TESTENV")
        await _add_variable(client, admin_token, test_id, variable_id)
        async with AsyncSessionLocal() as db:
            masked = await svc.resolve_command_masked(db, test_id, {"TESTENV": "prod"})
        assert masked == ["prod"]

"""Копирование параметров через API: независимость, доступ и откат замены."""

import pytest

from src.repositories import test_command_arg as repo
from tests.conftest import auth_hdr
from tests.test_test_command_args_crud import _args_url, _create_test, _variable_id


@pytest.fixture
async def commands(client, admin_token):
    headers = auth_hdr(admin_token)
    source = await _create_test(client, admin_token)
    target = await _create_test(client, admin_token)
    variable_id = await _variable_id(client, admin_token)
    for body in (
        {"kind": "variable", "variable_id": variable_id, "override_value": "prod", "position": 8},
        {"kind": "literal", "literal_value": "--test", "position": 2},
    ):
        response = await client.post(_args_url(source), headers=headers, json=body)
        assert response.status_code == 201, response.text
    response = await client.post(
        _args_url(target), headers=headers,
        json={"kind": "literal", "literal_value": "old-parameter"},
    )
    assert response.status_code == 201, response.text
    return source, target


async def read_args(client, token, test_id):
    response = await client.get(_args_url(test_id), headers=auth_hdr(token))
    assert response.status_code == 200, response.text
    return response.json()


async def test_copy_replaces_parameters_and_can_be_edited_independently(client, admin_token, commands):
    source, target = commands
    original = await read_args(client, admin_token, source)
    target_before = await client.get(f"/api/testing/v1/test-definitions/{target}", headers=auth_hdr(admin_token))
    response = await client.post(
        _args_url(target, "copy-from"), headers=auth_hdr(admin_token),
        json={"source_test_id": source},
    )
    assert response.status_code == 200, response.text
    copied = response.json()
    assert len(copied) == 2
    assert [arg["position"] for arg in copied] == [0, 1]
    for actual, expected in zip(copied, original, strict=True):
        assert actual["test_id"] == target
        assert actual["id"] != expected["id"]
        for key in ("kind", "literal_value", "variable_id", "override_value"):
            assert actual[key] == expected[key]
    assert await read_args(client, admin_token, target) == copied
    updated = await client.patch(
        _args_url(target, copied[0]["id"]), headers=auth_hdr(admin_token),
        json={"literal_value": "--another-test"},
    )
    assert updated.status_code == 200, updated.text
    assert await read_args(client, admin_token, source) == original
    target_after = await client.get(f"/api/testing/v1/test-definitions/{target}", headers=auth_hdr(admin_token))
    assert target_after.json() == target_before.json()


@pytest.mark.parametrize("source_kind, status", [("empty", 422), ("missing", 404), ("self", 422)])
async def test_invalid_source_preserves_current_parameters(client, admin_token, commands, source_kind, status):
    _, target = commands
    before = await read_args(client, admin_token, target)
    source = {
        "empty": await _create_test(client, admin_token),
        "missing": "tdef_missing",
        "self": target,
    }[source_kind]
    response = await client.post(
        _args_url(target, "copy-from"), headers=auth_hdr(admin_token),
        json={"source_test_id": source},
    )
    assert response.status_code == status, response.text
    assert await read_args(client, admin_token, target) == before


async def test_missing_target_returns_404(client, admin_token, commands):
    source, _ = commands
    response = await client.post(
        _args_url("tdef_missing", "copy-from"), headers=auth_hdr(admin_token),
        json={"source_test_id": source},
    )
    assert response.status_code == 404


@pytest.mark.parametrize("authenticated", [False, True])
async def test_copy_requires_update_permission(client, admin_token, no_role_token, commands, authenticated):
    source, target = commands
    before = await read_args(client, admin_token, target)
    response = await client.post(
        _args_url(target, "copy-from"),
        headers=auth_hdr(no_role_token) if authenticated else {},
        json={"source_test_id": source},
    )
    assert response.status_code == (403 if authenticated else 401)
    assert await read_args(client, admin_token, target) == before


async def test_failure_after_first_insert_rolls_back_replacement(client, admin_token, commands, monkeypatch):
    source, target = commands
    before = await read_args(client, admin_token, target)
    original_create = repo.create
    calls = 0

    async def fail_second_insert(db, data):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected insert failure")
        return await original_create(db, data)

    monkeypatch.setattr(repo, "create", fail_second_insert)
    with pytest.raises(RuntimeError, match="injected insert failure"):
        await client.post(
            _args_url(target, "copy-from"), headers=auth_hdr(admin_token),
            json={"source_test_id": source},
        )
    assert calls == 2
    assert await read_args(client, admin_token, target) == before

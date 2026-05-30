"""После revoke имя PAT свободно для пересоздания.

exists_name фильтрует по `revoked_at IS NULL` + БД-уровень держит partial
unique `(user_id, name) WHERE revoked_at IS NULL` — историческое поведение
давало 409 при попытке пересоздать PAT с тем же именем, что блокировало
штатный flow ротации (revoke old → mint new same-name).
"""

PAT_URL = "/api/auth/v1/tokens"


async def _create(client, token, name):
    return await client.post(
        PAT_URL,
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name, "allowed_services": ["service_x"]},
    )


async def test_pat_name_freed_after_revoke(client, user_a_token):
    first = await _create(client, user_a_token, "rotation_pat")
    assert first.status_code == 201, first.text
    pat_id = first.json()["token_id"]

    revoke = await client.delete(
        f"{PAT_URL}/{pat_id}",
        headers={"Authorization": f"Bearer {user_a_token}"},
    )
    assert revoke.status_code == 200, revoke.text

    second = await _create(client, user_a_token, "rotation_pat")
    assert second.status_code == 201, (
        f"expected new PAT with revoked name to succeed, got {second.text}"
    )
    assert second.json()["token"] != first.json()["token"]


async def test_active_duplicate_pat_name_still_409(client, user_a_token):
    """Контр-кейс: пока старый PAT активен, имя по-прежнему занято."""
    first = await _create(client, user_a_token, "still_active_pat")
    assert first.status_code == 201
    second = await _create(client, user_a_token, "still_active_pat")
    assert second.status_code == 409
    assert second.json()["error_code"] == "TOKEN_NAME_ALREADY_EXISTS"

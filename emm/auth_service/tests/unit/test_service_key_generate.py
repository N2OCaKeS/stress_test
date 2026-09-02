"""Тесты генератора мастер-ключей шифрования (`POST /admin/service-keys/generate`).

auth_service только генерит свежий AES-256 ключ и отдаёт его account_admin'у;
сам ключ нигде не хранит. Проверяем формат/длину ответа, RBAC (только
account_admin), и что два вызова дают разный материал (CSPRNG).
"""

from __future__ import annotations

import base64

import pytest

URL = "/api/auth/v1/admin/service-keys/generate"


@pytest.mark.asyncio
async def test_account_admin_gets_fresh_32_byte_key(client, admin_token):
    resp = await client.post(URL, headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["key_bytes"] == 32
    assert body["algorithm"] == "AES-256-GCM"
    raw = base64.b64decode(body["key_b64"], validate=True)
    assert len(raw) == 32


@pytest.mark.asyncio
async def test_two_calls_return_distinct_material(client, admin_token):
    first = await client.post(URL, headers={"Authorization": f"Bearer {admin_token}"})
    second = await client.post(URL, headers={"Authorization": f"Bearer {admin_token}"})
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["key_b64"] != second.json()["key_b64"]


@pytest.mark.asyncio
async def test_regular_user_forbidden(client, user_a_token):
    resp = await client.post(URL, headers={"Authorization": f"Bearer {user_a_token}"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_no_token_rejected(client):
    resp = await client.post(URL)
    assert resp.status_code == 401

"""AAD swap protection: secret_encrypted скопированный в другую cred-row
не расшифровывается (InvalidTag → 422 DECRYPT_FAILED).

`secrets_service.aad_for_credential(cred_id)` привязывает ciphertext к
конкретному cred_id. Подмена с cred_a на cred_b → AESGCM.decrypt отбивает.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from tests.integration.conftest import auth_header

BASE = "/api/secret/v1"

pytestmark = pytest.mark.asyncio


async def test_aad_swap_attack_decrypt_fails(
    client, identity_factory, db,
):
    owner = identity_factory(
        user_id="usr_aad_owner",
        department_id="dep_a",
        service_roles={"secret_service": ["operator"]},
    )

    c1 = await client.post(
        f"{BASE}/credentials", headers=auth_header(owner),
        json={"name": "aad_a", "service": "jira", "scope": "personal", "secret": "secret-a"},
    )
    c2 = await client.post(
        f"{BASE}/credentials", headers=auth_header(owner),
        json={"name": "aad_b", "service": "jira", "scope": "personal", "secret": "secret-b"},
    )
    assert c1.status_code == 201
    assert c2.status_code == 201
    cred_a = c1.json()["id"]
    cred_b = c2.json()["id"]

    # Достаём encrypted из A.
    row_a = (await db.execute(
        text("SELECT secret_encrypted FROM credentials WHERE id = :id"),
        {"id": cred_a},
    )).first()
    encrypted_a = row_a[0]

    # Копируем encrypted_a поверх B.
    await db.execute(
        text("UPDATE credentials SET secret_encrypted = :enc WHERE id = :id"),
        {"enc": encrypted_a, "id": cred_b},
    )
    await db.commit()

    # Reveal B → должен упасть, потому что AAD не совпадает с cred_b.
    resp = await client.post(f"{BASE}/credentials/{cred_b}/reveal", headers=auth_header(owner))
    # Reveal A (control) — ok.
    ok = await client.post(f"{BASE}/credentials/{cred_a}/reveal", headers=auth_header(owner))
    assert ok.status_code == 200

    assert resp.status_code == 422
    assert resp.json()["error_code"] == "DECRYPT_FAILED"

"""Рантайм-ротация мастер-ключа secret_service на service-слое.

Проверяем инварианты `key_rotation_service.rotate/retire` поверх живого
keystore (file-backend из conftest) + реальной БД:

* rotate бампит активную версию и seed'ит outbox;
* старые токены остаются дешифруемыми после rotate (dual-version read);
* повтор с тем же материалом идемпотентен;
* retire запрещён пока на версии есть строки и пока она активна;
* после ре-шифрации всех строк retire убирает версию из keystore.
"""

from __future__ import annotations

import base64

import pytest
from sqlalchemy import text

from src.core.exceptions import ConflictError
from src.core.keystore import get_keystore
from src.repositories import credentials as repo
from src.services import (
    key_rotation_service,
    reencrypt_outbox_service,
    secrets_service,
)


def _fresh_key_b64() -> str:
    import os

    return base64.b64encode(os.urandom(32)).decode("ascii")


async def _make_cred(adb, cred_id: str, blob: str) -> None:
    await repo.create(
        adb,
        id=cred_id,
        name="rot_" + cred_id[-4:],
        service="jira",
        scope="personal",
        owner_user_id="usr_rot_owner",
        owner_dept_id=None,
        owner_user_dept_id="dep_rot",
        login=None,
        secret_encrypted=blob,
        status="active",
        created_by="usr_rot_owner",
    )


@pytest.mark.asyncio
async def test_rotate_bumps_active_version(adb):
    ks = get_keystore()
    before = ks.get_active_version()
    result = await key_rotation_service.rotate(adb, new_key_b64=_fresh_key_b64())
    assert result["idempotent"] is False
    assert result["new_version"] == before + 1
    assert result["previous_version"] == before
    assert ks.get_active_version() == before + 1


@pytest.mark.asyncio
async def test_old_tokens_still_decrypt_after_rotate(adb):
    aad = secrets_service.aad_for_credential("rot-decrypt")
    token_old = secrets_service.encrypt("super-secret", aad=aad)

    await key_rotation_service.rotate(adb, new_key_b64=_fresh_key_b64())

    # Токен зашифрован старой версией — её материал ещё в keystore.
    assert secrets_service.decrypt(token_old, aad=aad) == "super-secret"
    # Новый токен пишется под новой активной версией.
    token_new = secrets_service.encrypt("super-secret", aad=aad)
    assert token_new.split("$", 1)[0] != token_old.split("$", 1)[0]
    assert secrets_service.decrypt(token_new, aad=aad) == "super-secret"


@pytest.mark.asyncio
async def test_rotate_is_idempotent_with_same_material(adb):
    new_key = _fresh_key_b64()
    first = await key_rotation_service.rotate(adb, new_key_b64=new_key)
    second = await key_rotation_service.rotate(adb, new_key_b64=new_key)
    assert first["idempotent"] is False
    assert second["idempotent"] is True
    assert second["new_version"] == first["new_version"]


@pytest.mark.asyncio
async def test_retire_active_version_rejected(adb):
    ks = get_keystore()
    active = ks.get_active_version()
    with pytest.raises(ConflictError) as exc:
        await key_rotation_service.retire(adb, version=active)
    assert exc.value.error_code == "KEYSTORE_CANNOT_RETIRE_ACTIVE"


@pytest.mark.asyncio
async def test_retire_rejected_while_rows_on_version(adb):
    await adb.execute(text("DELETE FROM credentials"))
    await adb.flush()

    aad = secrets_service.aad_for_credential("rot-inuse")
    blob = secrets_service.encrypt("x", aad=aad)
    await _make_cred(adb, "cred_rot_inuse", blob)
    await adb.flush()
    old_version = get_keystore().get_active_version()

    # Ротация делает old_version не активной, но строка всё ещё на ней.
    await key_rotation_service.rotate(adb, new_key_b64=_fresh_key_b64())

    with pytest.raises(ConflictError) as exc:
        await key_rotation_service.retire(adb, version=old_version)
    assert exc.value.error_code == "KEYSTORE_VERSION_IN_USE"


@pytest.mark.asyncio
async def test_retire_succeeds_when_no_rows_on_version(adb):
    await adb.execute(text("DELETE FROM credentials"))
    await adb.flush()

    ks = get_keystore()
    old_version = ks.get_active_version()
    await key_rotation_service.rotate(adb, new_key_b64=_fresh_key_b64())

    # На old_version 0 строк — retire проходит и убирает версию из keystore.
    result = await key_rotation_service.retire(adb, version=old_version)
    assert result["retired"] is True
    assert old_version not in ks.list_versions()


@pytest.mark.asyncio
async def test_retire_unknown_version_is_idempotent(adb):
    await adb.execute(text("DELETE FROM credentials"))
    await adb.flush()
    result = await key_rotation_service.retire(adb, version=987)
    assert result["retired"] is False


async def _pending_count(adb) -> int:
    row = await adb.execute(
        text(
            "SELECT count(*) FROM reencrypt_outbox_entries WHERE status = 'pending'"
        )
    )
    return int(row.scalar_one())


@pytest.mark.asyncio
async def test_rotate_reseeds_outbox_after_seed_failure(adb, monkeypatch):
    """seed_outbox упал ПОСЛЕ set_active → ретрай тем же материалом досевает.

    Иначе холодные легаси-креды застряли бы под старой версией навсегда:
    keystore уже переключён, а повтор уходит в идемпотентную ветку.
    """
    await adb.execute(text("DELETE FROM reencrypt_outbox_entries"))
    await adb.execute(text("DELETE FROM credentials"))
    await adb.flush()

    old = get_keystore().get_active_version()
    aad = secrets_service.aad_for_credential("cred_seedfail")
    blob = secrets_service.encrypt("x", aad=aad)
    await _make_cred(adb, "cred_seedfail", blob)
    await adb.flush()

    new_key = _fresh_key_b64()

    async def _flaky_seed(db, **kw):
        raise RuntimeError("boom")

    real_seed = reencrypt_outbox_service.seed_outbox
    monkeypatch.setattr(reencrypt_outbox_service, "seed_outbox", _flaky_seed)

    with pytest.raises(RuntimeError):
        await key_rotation_service.rotate(adb, new_key_b64=new_key)

    # keystore переключён, но outbox не засеян.
    assert get_keystore().get_active_version() == old + 1
    assert await _pending_count(adb) == 0

    # Ретрай тем же материалом: идемпотентная ветка теперь досевает outbox.
    monkeypatch.setattr(reencrypt_outbox_service, "seed_outbox", real_seed)
    result = await key_rotation_service.rotate(adb, new_key_b64=new_key)
    assert result["idempotent"] is True
    assert result["seeded"]["inserted"] >= 1
    assert await _pending_count(adb) >= 1

"""Рантайм-ротация мастер-ключа server_service на service-слое.

Проверяем инварианты `key_rotation_service.rotate/retire` поверх живого
keystore (file-backend из conftest) + реальной БД:

* rotate бампит активную версию и seed'ит outbox;
* старые токены остаются дешифруемыми после rotate (dual-version read);
* повтор с тем же материалом идемпотентен;
* retire запрещён пока на версии есть строки (`server_accounts`) и пока
  она активна;
* после освобождения версии retire убирает её из keystore.
"""

from __future__ import annotations

import base64
import os

import pytest

from src.core.exceptions import AppException
from src.core.keystore import get_keystore
from src.services import key_rotation_service, secrets_service


def _fresh_key_b64() -> str:
    return base64.b64encode(os.urandom(32)).decode("ascii")


@pytest.mark.asyncio
async def test_rotate_bumps_active_version(db):
    ks = get_keystore()
    before = ks.get_active_version()
    result = await key_rotation_service.rotate(db, new_key_b64=_fresh_key_b64())
    assert result["idempotent"] is False
    assert result["new_version"] == before + 1
    assert result["previous_version"] == before
    assert ks.get_active_version() == before + 1


@pytest.mark.asyncio
async def test_old_tokens_still_decrypt_after_rotate(db):
    aad = secrets_service.aad_for_server_account_password("acc-rot")
    token_old = secrets_service.encrypt("server-pwd", aad=aad)

    await key_rotation_service.rotate(db, new_key_b64=_fresh_key_b64())

    assert secrets_service.decrypt(token_old, aad=aad) == "server-pwd"
    token_new = secrets_service.encrypt("server-pwd", aad=aad)
    assert token_new.split("$", 1)[0] != token_old.split("$", 1)[0]
    assert secrets_service.decrypt(token_new, aad=aad) == "server-pwd"


@pytest.mark.asyncio
async def test_rotate_is_idempotent_with_same_material(db):
    new_key = _fresh_key_b64()
    first = await key_rotation_service.rotate(db, new_key_b64=new_key)
    second = await key_rotation_service.rotate(db, new_key_b64=new_key)
    assert first["idempotent"] is False
    assert second["idempotent"] is True
    assert second["new_version"] == first["new_version"]


@pytest.mark.asyncio
async def test_rotate_rejects_bad_key(db):
    with pytest.raises(AppException) as exc:
        await key_rotation_service.rotate(db, new_key_b64="not-base64!!!")
    assert exc.value.error_code == "ROTATE_KEY_INVALID"

    short = base64.b64encode(os.urandom(16)).decode("ascii")
    with pytest.raises(AppException) as exc:
        await key_rotation_service.rotate(db, new_key_b64=short)
    assert exc.value.error_code == "ROTATE_KEY_INVALID"


@pytest.mark.asyncio
async def test_retire_active_version_rejected(db):
    ks = get_keystore()
    active = ks.get_active_version()
    with pytest.raises(AppException) as exc:
        await key_rotation_service.retire(db, version=active)
    assert exc.value.error_code == "KEYSTORE_CANNOT_RETIRE_ACTIVE"


@pytest.mark.asyncio
async def test_retire_rejected_while_rows_on_version(db, make_server, make_account):
    server = await make_server()
    await make_account(server_id=server.id, password="rot-inuse-pwd")
    old_version = get_keystore().get_active_version()

    await key_rotation_service.rotate(db, new_key_b64=_fresh_key_b64())

    with pytest.raises(AppException) as exc:
        await key_rotation_service.retire(db, version=old_version)
    assert exc.value.error_code == "KEYSTORE_VERSION_IN_USE"


@pytest.mark.asyncio
async def test_retire_succeeds_when_no_rows_on_version(db):
    ks = get_keystore()
    old_version = ks.get_active_version()
    await key_rotation_service.rotate(db, new_key_b64=_fresh_key_b64())

    result = await key_rotation_service.retire(db, version=old_version)
    assert result["retired"] is True
    assert old_version not in ks.list_versions()


@pytest.mark.asyncio
async def test_retire_unknown_version_is_idempotent(db):
    result = await key_rotation_service.retire(db, version=987)
    assert result["retired"] is False

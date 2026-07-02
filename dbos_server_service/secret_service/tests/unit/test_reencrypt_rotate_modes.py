"""Режимы ротации lazy/force и durability нового ключа.

* rotate(mode="lazy") — force не включается;
* rotate(mode="force") при наличии legacy-строк открывает force-окно;
* rotate(mode="force") на пустой БД (нечего перешифровывать) остаётся lazy;
* невалидный mode → ROTATE_MODE_INVALID;
* новый ключ переживает reload keystore (durable file-backend).
"""

from __future__ import annotations

import base64
import os

import pytest
from sqlalchemy import text

from src.core.exceptions import BadRequestError
from src.core.keystore import get_keystore
from src.repositories import credentials as repo
from src.services import (
    key_rotation_service,
    reencrypt_state_service,
    secrets_service,
)


def _fresh_key_b64() -> str:
    return base64.b64encode(os.urandom(32)).decode("ascii")


async def _clean(adb) -> None:
    await adb.execute(text("DELETE FROM reencrypt_outbox_entries"))
    await adb.execute(text("DELETE FROM credentials"))
    await adb.flush()


async def _make_cred(adb, cred_id: str) -> None:
    aad = secrets_service.aad_for_credential(cred_id)
    blob = secrets_service.encrypt("v", aad=aad)
    await repo.create(
        adb,
        id=cred_id,
        name="rm_" + cred_id[-4:],
        service="jira",
        scope="personal",
        owner_user_id="usr_rm_owner",
        owner_dept_id=None,
        owner_user_dept_id="dep_rm",
        login=None,
        secret_encrypted=blob,
        status="active",
        created_by="usr_rm_owner",
    )
    await adb.flush()


@pytest.mark.asyncio
async def test_lazy_rotate_does_not_force(adb):
    await _clean(adb)
    await _make_cred(adb, "cred_rm_lazy")
    result = await key_rotation_service.rotate(adb, new_key_b64=_fresh_key_b64())
    assert result["mode"] == "lazy"
    state = await reencrypt_state_service.get_state(adb)
    assert state.force_active is False


@pytest.mark.asyncio
async def test_force_rotate_opens_window(adb):
    await _clean(adb)
    await _make_cred(adb, "cred_rm_force")
    result = await key_rotation_service.rotate(
        adb, new_key_b64=_fresh_key_b64(), mode="force"
    )
    assert result["mode"] == "force"
    state = await reencrypt_state_service.get_state(adb)
    assert state.force_active is True
    assert state.remaining >= 1


@pytest.mark.asyncio
async def test_force_rotate_on_empty_stays_lazy(adb):
    await _clean(adb)
    # Нет legacy-строк → force-окно не открывается, чтобы не залочить сервис.
    result = await key_rotation_service.rotate(
        adb, new_key_b64=_fresh_key_b64(), mode="force"
    )
    assert result["mode"] == "lazy"
    state = await reencrypt_state_service.get_state(adb)
    assert state.force_active is False


@pytest.mark.asyncio
async def test_invalid_mode_rejected(adb):
    with pytest.raises(BadRequestError) as exc:
        await key_rotation_service.rotate(
            adb, new_key_b64=_fresh_key_b64(), mode="turbo"
        )
    assert exc.value.error_code == "ROTATE_MODE_INVALID"


@pytest.mark.asyncio
async def test_new_key_survives_keystore_reload(adb):
    await _clean(adb)
    result = await key_rotation_service.rotate(adb, new_key_b64=_fresh_key_b64())
    new_version = result["new_version"]

    # Токен под новой версией.
    aad = secrets_service.aad_for_credential("cred_rm_reload")
    token = secrets_service.encrypt("payload", aad=aad)
    assert token.startswith(f"v{new_version}$")

    # Пересоздаём keystore из durable file-backend'а (как рестарт процесса):
    # версия и её материал должны сохраниться, токен — расшифроваться.
    get_keystore.cache_clear()
    ks2 = get_keystore()
    assert new_version in ks2.list_versions()
    assert ks2.get_active_version() == new_version
    assert secrets_service.decrypt(token, aad=aad) == "payload"

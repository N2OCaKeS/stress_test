"""Авто-вывод опустевшей версии мастер-ключа после reencrypt-батча.

Проверяем `key_rotation_service.auto_retire_drained` + durable-пометку
(`reconcile_tombstones`) поверх живого keystore (file-backend из conftest)
и реальной БД:

* полная ре-шифрация → старая версия авто-выведена и удалена из keystore;
* активная версия не выводится никогда;
* остались строки на версии → не выводим;
* pending-задача reencrypt-outbox'а на версии → не выводим, пока не закроется;
* идемпотентность повторного tick'а;
* durability — выведенная версия, воскресшая в keystore, вычищается reconcile'ом.
"""

from __future__ import annotations

import base64
import os

import pytest
from sqlalchemy import select, text

from src.core.keystore import get_keystore
from src.models import RetiredKeyVersion
from src.repositories import credentials as repo
from src.services import (
    key_rotation_service,
    migration_status_service,
    reencrypt_outbox_service,
    secrets_service,
)


def _fresh_key_b64() -> str:
    return base64.b64encode(os.urandom(32)).decode("ascii")


async def _clean(adb) -> None:
    await adb.execute(text("DELETE FROM reencrypt_outbox_entries"))
    await adb.execute(text("DELETE FROM credentials"))
    await adb.flush()


async def _make_cred(adb, cred_id: str, blob: str) -> None:
    await repo.create(
        adb,
        id=cred_id,
        name="ar_" + cred_id[-4:],
        service="jira",
        scope="personal",
        owner_user_id="usr_ar_owner",
        owner_dept_id=None,
        owner_user_dept_id="dep_ar",
        login=None,
        secret_encrypted=blob,
        status="active",
        created_by="usr_ar_owner",
    )


@pytest.mark.asyncio
async def test_full_reencrypt_auto_retires_old_version(adb):
    await _clean(adb)
    ks = get_keystore()
    old = ks.get_active_version()

    aad = secrets_service.aad_for_credential("cred_ar_full")
    await _make_cred(adb, "cred_ar_full", secrets_service.encrypt("s", aad=aad))
    await adb.flush()

    await key_rotation_service.rotate(adb, new_key_b64=_fresh_key_b64())
    await reencrypt_outbox_service.seed_outbox(adb)
    await reencrypt_outbox_service.process_batch(adb)

    retired = await key_rotation_service.auto_retire_drained(adb)
    assert {"version": old, "retired": True} in retired
    assert old not in ks.list_versions()

    status = await migration_status_service.compute(
        adb, active_version=ks.get_active_version()
    )
    assert str(old) not in status.by_version


@pytest.mark.asyncio
async def test_active_version_never_auto_retired(adb):
    await _clean(adb)
    ks = get_keystore()
    active = ks.get_active_version()

    retired = await key_rotation_service.auto_retire_drained(adb)
    assert retired == []
    assert active in ks.list_versions()


@pytest.mark.asyncio
async def test_partial_migration_keeps_version(adb):
    await _clean(adb)
    ks = get_keystore()
    old = ks.get_active_version()

    aad = secrets_service.aad_for_credential("cred_ar_partial")
    await _make_cred(adb, "cred_ar_partial", secrets_service.encrypt("s", aad=aad))
    await adb.flush()

    # Ротация делает old не активной, но строка всё ещё на ней.
    await key_rotation_service.rotate(adb, new_key_b64=_fresh_key_b64())

    retired = await key_rotation_service.auto_retire_drained(adb)
    assert retired == []
    assert old in ks.list_versions()


@pytest.mark.asyncio
async def test_pending_outbox_blocks_auto_retire(adb):
    await _clean(adb)
    ks = get_keystore()
    old = ks.get_active_version()

    aad = secrets_service.aad_for_credential("cred_ar_pending")
    await _make_cred(adb, "cred_ar_pending", secrets_service.encrypt("s", aad=aad))
    await adb.flush()

    await key_rotation_service.rotate(adb, new_key_b64=_fresh_key_b64())
    await reencrypt_outbox_service.seed_outbox(adb)  # pending row source_version=old

    # Симулируем lazy-путь: строка уже переехала на активную версию (0 строк
    # на old), но pending-задача в outbox'е ещё висит.
    new_blob = secrets_service.encrypt("s", aad=aad)
    await adb.execute(
        text("UPDATE credentials SET secret_encrypted = :b WHERE id = :i"),
        {"b": new_blob, "i": "cred_ar_pending"},
    )
    await adb.flush()

    retired = await key_rotation_service.auto_retire_drained(adb)
    assert retired == []
    assert old in ks.list_versions()


@pytest.mark.asyncio
async def test_auto_retire_is_idempotent(adb):
    await _clean(adb)
    ks = get_keystore()
    old = ks.get_active_version()
    await key_rotation_service.rotate(adb, new_key_b64=_fresh_key_b64())

    first = await key_rotation_service.auto_retire_drained(adb)
    assert {"version": old, "retired": True} in first
    # Повторный tick после вывода — без ошибок и без повторного retire.
    second = await key_rotation_service.auto_retire_drained(adb)
    assert second == []
    assert old not in ks.list_versions()


@pytest.mark.asyncio
async def test_retire_records_durable_tombstone(adb):
    await _clean(adb)
    ks = get_keystore()
    old = ks.get_active_version()
    await key_rotation_service.rotate(adb, new_key_b64=_fresh_key_b64())
    await key_rotation_service.auto_retire_drained(adb)

    rows = (await adb.execute(select(RetiredKeyVersion.version))).scalars().all()
    assert old in {int(v) for v in rows}


@pytest.mark.asyncio
async def test_reconcile_prunes_resurrected_version(adb):
    await _clean(adb)
    ks = get_keystore()
    old = ks.get_active_version()
    await key_rotation_service.rotate(adb, new_key_b64=_fresh_key_b64())
    await key_rotation_service.auto_retire_drained(adb)
    assert old not in ks.list_versions()

    # Воскрешаем версию в keystore, как сделал бы bootstrap из env после
    # потери файла на emptyDir.
    ks.set_key(old, _fresh_key_b64())
    assert old in ks.list_versions()

    pruned = await key_rotation_service.reconcile_tombstones(adb)
    assert old in pruned
    assert old not in ks.list_versions()

    # Повторный reconcile — без ошибок, ничего не вычищает.
    assert await key_rotation_service.reconcile_tombstones(adb) == []

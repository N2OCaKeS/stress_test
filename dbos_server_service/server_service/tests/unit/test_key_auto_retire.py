"""Авто-вывод опустевшей версии мастер-ключа после перешифровки.

Проверяем, что как только не-активная версия осушена (0 owner-строк и пустой
reencrypt-outbox), `key_rotation_service.auto_retire_drained_versions`
автоматически убирает её материал из keystore, переиспользуя гарды `retire`:

* полная перешифровка через outbox (finalize_done) → старая версия выведена,
  migration_status её больше не показывает;
* активная версия не выводится никогда;
* частичная миграция (часть строк ещё на старой версии) не выводит;
* повторный тик идемпотентен;
* durability: вывод записан в backing store keystore и переживает рестарт
  процесса (перечитывание файла, не bootstrap из env).
"""

from __future__ import annotations

import base64
import os

import pytest

from src.core.keystore import get_keystore
from src.services import (
    key_rotation_service,
    secrets_migration_service,
    secrets_service,
)


def _fresh_key_b64() -> str:
    return base64.b64encode(os.urandom(32)).decode("ascii")


async def _drain_outbox(db) -> None:
    """Проклеймить и закрыть все pending outbox-row'ы (как делает worker)."""
    while True:
        claimed = await secrets_migration_service.claim_pending(db, limit=100)
        if not claimed:
            break
        for item in claimed:
            await secrets_migration_service.finalize_done(db, item["id"])


@pytest.mark.asyncio
async def test_full_reencryption_auto_retires_old_version(
    db, make_server, make_account
):
    ks = get_keystore()
    old_version = ks.get_active_version()

    server = await make_server()
    await make_account(server_id=server.id, password="rotate-me-pwd")

    await key_rotation_service.rotate(db, new_key_b64=_fresh_key_b64())
    new_version = ks.get_active_version()
    assert new_version == old_version + 1

    # Полная перешифровка через outbox-путь (finalize_done хукает auto-retire).
    await _drain_outbox(db)

    assert old_version not in ks.list_versions()
    assert new_version in ks.list_versions()

    status = await secrets_migration_service.status(db)
    assert old_version not in status["by_version"]
    assert status["active_version"] == new_version


@pytest.mark.asyncio
async def test_active_version_never_auto_retired(db, make_server, make_account):
    ks = get_keystore()
    active = ks.get_active_version()
    server = await make_server()
    await make_account(server_id=server.id, password="active-pwd")

    retired = await key_rotation_service.auto_retire_drained_versions(db)

    assert retired == []
    assert active in ks.list_versions()


@pytest.mark.asyncio
async def test_active_version_with_no_rows_still_not_retired(db):
    """Даже пустая активная версия не выводится — её пишут новые токены."""
    ks = get_keystore()
    active = ks.get_active_version()

    retired = await key_rotation_service.auto_retire_drained_versions(
        db, candidate_versions=[active]
    )

    assert retired == []
    assert active in ks.list_versions()


@pytest.mark.asyncio
async def test_partial_migration_does_not_retire(db, make_server, make_account):
    ks = get_keystore()
    old_version = ks.get_active_version()

    server = await make_server()
    await make_account(server_id=server.id, login="root", password="acc-one")
    await make_account(server_id=server.id, login="svc", password="acc-two")

    await key_rotation_service.rotate(db, new_key_b64=_fresh_key_b64())

    # Закрываем ровно одну outbox-row — вторая остаётся на старой версии.
    claimed = await secrets_migration_service.claim_pending(db, limit=1)
    assert len(claimed) == 1
    await secrets_migration_service.finalize_done(db, claimed[0]["id"])

    # Одна строка ещё на old_version → версия не выводится.
    assert old_version in ks.list_versions()
    retired = await key_rotation_service.auto_retire_drained_versions(
        db, candidate_versions=[old_version]
    )
    assert retired == []
    assert old_version in ks.list_versions()


@pytest.mark.asyncio
async def test_open_outbox_blocks_retire(db, make_server, make_account):
    """0 owner-строк, но висит pending outbox-row → не выводим версию."""
    ks = get_keystore()
    old_version = ks.get_active_version()

    server = await make_server()
    await make_account(server_id=server.id, password="outbox-pending-pwd")

    await key_rotation_service.rotate(db, new_key_b64=_fresh_key_b64())
    new_version = ks.get_active_version()

    # Руками переписываем owner-row на активную версию, НЕ закрывая outbox-row:
    # owner-строк на old_version больше нет, но pending-задача всё ещё ссылается
    # на неё.
    from sqlalchemy import select, update

    from src.models import ServerAccount

    acc = (await db.execute(select(ServerAccount))).scalars().first()
    aad = secrets_service.aad_for_server_account_password(acc.id)
    new_ct = secrets_service.encrypt("outbox-pending-pwd", aad=aad)
    await db.execute(
        update(ServerAccount)
        .where(ServerAccount.id == acc.id)
        .values(password_encrypted=new_ct)
    )
    await db.commit()

    assert not await secrets_migration_service.has_rows_on_version(db, old_version)
    assert await secrets_migration_service.has_open_outbox_on_version(db, old_version)

    retired = await key_rotation_service.auto_retire_drained_versions(
        db, candidate_versions=[old_version]
    )
    assert retired == []
    assert old_version in ks.list_versions()
    assert new_version in ks.list_versions()


@pytest.mark.asyncio
async def test_repeated_tick_is_idempotent(db, make_server, make_account):
    ks = get_keystore()
    old_version = ks.get_active_version()

    server = await make_server()
    await make_account(server_id=server.id, password="idem-pwd")
    await key_rotation_service.rotate(db, new_key_b64=_fresh_key_b64())
    await _drain_outbox(db)

    assert old_version not in ks.list_versions()

    # Повторные тики ничего не делают и не падают.
    first = await key_rotation_service.auto_retire_drained_versions(db)
    second = await key_rotation_service.auto_retire_drained_versions(
        db, candidate_versions=[old_version]
    )
    assert first == []
    assert second == []


@pytest.mark.asyncio
async def test_auto_retire_is_durable_across_restart(
    db, make_server, make_account
):
    """Вывод записан в файловый backing store и не воскресает из env."""
    import src.core.keystore as keystore_mod

    ks = get_keystore()
    old_version = ks.get_active_version()

    server = await make_server()
    await make_account(server_id=server.id, password="durable-pwd")
    await key_rotation_service.rotate(db, new_key_b64=_fresh_key_b64())
    new_version = ks.get_active_version()
    await _drain_outbox(db)
    assert old_version not in ks.list_versions()

    # Эмулируем рестарт процесса: сбрасываем lru_cache, keystore перечитает
    # backing store (файл), а не bootstrap'нется заново из env.
    keystore_mod.get_keystore.cache_clear()
    ks_reloaded = get_keystore()
    assert old_version not in ks_reloaded.list_versions()
    assert new_version in ks_reloaded.list_versions()
    assert ks_reloaded.get_active_version() == new_version

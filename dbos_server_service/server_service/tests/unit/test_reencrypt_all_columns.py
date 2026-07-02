"""Перешифровка и retire-гард покрывают ВСЕ шифр-колонки всех таблиц.

Регрессия на потерю данных: раньше дренаж мастер-ключа трогал только
`password_encrypted` двух таблиц, а `servers.mgmt_*` / `server_accounts.ssh_*`
и `previous_*` оставались на старой версии. Авто-retire дропал старый ключ, и
эти ячейки становились нерасшифровываемыми.

Проверяем:

* полный round-trip через outbox — все восемь шифр-ячеек одной server-row +
  account + ipmi переезжают на активную версию, каждая расшифровывается своим
  AAD в исходный plaintext;
* инвариант retire — пока хоть одна колонка (mgmt_ssh) на старой версии,
  `has_rows_on_version` / auto-retire НЕ дропают версию; дропают только когда
  мигрированы все;
* `remaining_legacy` = сумма по всем колонкам.
"""

from __future__ import annotations

import base64
import os

import pytest
from sqlalchemy import select

from src.core.keystore import get_keystore
from src.models import IpmiController, Server, ServerAccount
from src.services import (
    key_rotation_service,
    secrets_migration_service,
    secrets_service,
)


def _fresh_key_b64() -> str:
    return base64.b64encode(os.urandom(32)).decode("ascii")


# Plaintext'ы для каждой шифр-ячейки — уникальны, чтобы swap AAD'ов или колонок
# ловился на расшифровке.
_SRV_MGMT_PW = "srv-mgmt-password"
_SRV_PREV_MGMT_PW = "srv-prev-mgmt-password"
_SRV_MGMT_SSH = "srv-mgmt-ssh-private-key"
_SRV_PREV_MGMT_SSH = "srv-prev-mgmt-ssh-private-key"
_ACC_PW = "acc-password"
_ACC_PREV_PW = "acc-prev-password"
_ACC_SSH = "acc-ssh-private-key"
_IPMI_PW = "ipmi-password"


async def _seed_all_columns(db, make_server, make_account, make_ipmi):
    """Создать server + account + ipmi со ВСЕМИ шифр-колонками на активной версии.

    Возвращает `(server, account, ipmi, expectations)`, где expectations —
    список `(model, obj_id, column_name, aad_fn, plaintext)` для проверки.
    """
    srv = await make_server(department_id="dep_a")
    # Управляющие креды сервера (mgmt_* + previous_*).
    srv.mgmt_password_encrypted = secrets_service.encrypt(
        _SRV_MGMT_PW, aad=secrets_service.aad_for_server_mgmt_password(srv.id)
    )
    srv.previous_mgmt_password_encrypted = secrets_service.encrypt(
        _SRV_PREV_MGMT_PW, aad=secrets_service.aad_for_server_mgmt_password(srv.id)
    )
    srv.mgmt_ssh_private_key_encrypted = secrets_service.encrypt(
        _SRV_MGMT_SSH, aad=secrets_service.aad_for_server_mgmt_ssh_key(srv.id)
    )
    srv.previous_mgmt_ssh_private_key_encrypted = secrets_service.encrypt(
        _SRV_PREV_MGMT_SSH, aad=secrets_service.aad_for_server_mgmt_ssh_key(srv.id)
    )

    acc = await make_account(server_id=srv.id, password=_ACC_PW)
    acc.previous_password_encrypted = secrets_service.encrypt(
        _ACC_PREV_PW, aad=secrets_service.aad_for_server_account_password(acc.id)
    )
    acc.ssh_private_key_encrypted = secrets_service.encrypt(
        _ACC_SSH, aad=secrets_service.aad_for_server_account_ssh_key(acc.id)
    )

    ctrl = await make_ipmi(server_id=srv.id, password=_IPMI_PW)

    await db.flush()
    await db.commit()

    expectations = [
        (Server, srv.id, "mgmt_password_encrypted",
         secrets_service.aad_for_server_mgmt_password, _SRV_MGMT_PW),
        (Server, srv.id, "previous_mgmt_password_encrypted",
         secrets_service.aad_for_server_mgmt_password, _SRV_PREV_MGMT_PW),
        (Server, srv.id, "mgmt_ssh_private_key_encrypted",
         secrets_service.aad_for_server_mgmt_ssh_key, _SRV_MGMT_SSH),
        (Server, srv.id, "previous_mgmt_ssh_private_key_encrypted",
         secrets_service.aad_for_server_mgmt_ssh_key, _SRV_PREV_MGMT_SSH),
        (ServerAccount, acc.id, "password_encrypted",
         secrets_service.aad_for_server_account_password, _ACC_PW),
        (ServerAccount, acc.id, "previous_password_encrypted",
         secrets_service.aad_for_server_account_password, _ACC_PREV_PW),
        (ServerAccount, acc.id, "ssh_private_key_encrypted",
         secrets_service.aad_for_server_account_ssh_key, _ACC_SSH),
        (IpmiController, ctrl.id, "password_encrypted",
         secrets_service.aad_for_ipmi_credential, _IPMI_PW),
    ]
    return srv, acc, ctrl, expectations


async def _drain_outbox(db) -> None:
    while True:
        claimed = await secrets_migration_service.claim_pending(db, limit=100)
        if not claimed:
            break
        for item in claimed:
            await secrets_migration_service.finalize_done(db, item["id"])


@pytest.mark.asyncio
async def test_all_columns_reencrypted_end_to_end(
    db, make_server, make_account, make_ipmi
):
    """Все восемь шифр-ячеек переходят на новую версию и расшифровываются."""
    ks = get_keystore()
    old_version = ks.get_active_version()

    _, _, _, expectations = await _seed_all_columns(
        db, make_server, make_account, make_ipmi
    )

    # rotate заводит новую версию и сам сидит outbox по всему реестру.
    await key_rotation_service.rotate(db, new_key_b64=_fresh_key_b64())
    new_version = ks.get_active_version()
    assert new_version == old_version + 1

    # Все 8 ячеек попали в outbox (повторный seed находит их же, ничего не
    # добавляя).
    reseed = await secrets_migration_service.seed_outbox(db)
    assert reseed["scanned"] == 8, reseed
    assert reseed["inserted"] == 0, reseed

    await _drain_outbox(db)

    # Каждая колонка теперь на активной версии и расшифровывается своим AAD.
    for model, obj_id, column_name, aad_fn, plaintext in expectations:
        obj = (
            await db.execute(select(model).where(model.id == obj_id))
        ).scalar_one()
        ciphertext = getattr(obj, column_name)
        assert ciphertext is not None
        version = secrets_migration_service._parse_version(ciphertext)
        assert version == new_version, (
            f"{model.__tablename__}.{column_name} осталась на v{version},"
            f" ждали v{new_version}"
        )
        decrypted = secrets_service.decrypt(ciphertext, aad=aad_fn(obj_id))
        assert decrypted == plaintext, f"{column_name} расшифровалась неверно"

    # Все ячейки мигрированы → старая версия осушена и авто-выведена.
    assert old_version not in ks.list_versions()
    assert not await secrets_migration_service.has_rows_on_version(db, old_version)


@pytest.mark.asyncio
async def test_lingering_mgmt_ssh_blocks_retire(
    db, make_server, make_account, make_ipmi
):
    """Пока mgmt_ssh на старой версии — retire не срабатывает, хотя остальные мигрированы."""
    ks = get_keystore()
    old_version = ks.get_active_version()

    srv, _, _, expectations = await _seed_all_columns(
        db, make_server, make_account, make_ipmi
    )

    # Заводим новую версию напрямую в keystore (без seed'а outbox), чтобы
    # руками контролировать, какие колонки мигрированы.
    new_version = max(ks.list_versions()) + 1
    ks.set_key(new_version, _fresh_key_b64())
    ks.set_active(new_version)

    # Перешифровываем ВСЕ колонки на новую версию, КРОМЕ
    # servers.mgmt_ssh_private_key_encrypted — она остаётся на old_version.
    target_skip = "mgmt_ssh_private_key_encrypted"
    for model, obj_id, column_name, aad_fn, plaintext in expectations:
        if model is Server and column_name == target_skip:
            continue
        obj = (
            await db.execute(select(model).where(model.id == obj_id))
        ).scalar_one()
        setattr(obj, column_name, secrets_service.encrypt(plaintext, aad=aad_fn(obj_id)))
    await db.commit()

    # Контроль: account/ipmi/mgmt-пароль уже на новой версии — старый баг,
    # смотревший только на password_encrypted, счёл бы версию осушённой.
    acc = (
        await db.execute(select(ServerAccount))
    ).scalars().first()
    assert secrets_migration_service._parse_version(acc.password_encrypted) == new_version

    # Но одна колонка (mgmt_ssh) ещё на старой версии → версия занята.
    assert await secrets_migration_service.has_rows_on_version(db, old_version)

    retired = await key_rotation_service.auto_retire_drained_versions(
        db, candidate_versions=[old_version]
    )
    assert retired == []
    assert old_version in ks.list_versions()

    # Домигрируем последнюю колонку — теперь версия свободна и выводится.
    server = (
        await db.execute(select(Server).where(Server.id == srv.id))
    ).scalar_one()
    server.mgmt_ssh_private_key_encrypted = secrets_service.encrypt(
        _SRV_MGMT_SSH, aad=secrets_service.aad_for_server_mgmt_ssh_key(srv.id)
    )
    await db.commit()

    assert not await secrets_migration_service.has_rows_on_version(db, old_version)
    retired = await key_rotation_service.auto_retire_drained_versions(
        db, candidate_versions=[old_version]
    )
    assert retired == [old_version]
    assert old_version not in ks.list_versions()


@pytest.mark.asyncio
async def test_remaining_legacy_sums_all_columns(
    db, make_server, make_account, make_ipmi
):
    """remaining_legacy и seed.scanned учитывают все 8 шифр-ячеек, не только пароли."""
    await _seed_all_columns(db, make_server, make_account, make_ipmi)

    await key_rotation_service.rotate(db, new_key_b64=_fresh_key_b64())

    # Ничего ещё не дренировали — все 8 ячеек под старой версией.
    assert await secrets_migration_service.remaining_legacy(db) == 8

    status = await secrets_migration_service.status(db)
    assert status["remaining_legacy_total"] == 8
    assert status["remaining"] == 8
    # Полный per-column breakdown содержит все колонки реестра.
    assert set(status["columns"].keys()) == {
        "servers.mgmt_password_encrypted",
        "servers.previous_mgmt_password_encrypted",
        "servers.mgmt_ssh_private_key_encrypted",
        "servers.previous_mgmt_ssh_private_key_encrypted",
        "server_accounts.password_encrypted",
        "server_accounts.previous_password_encrypted",
        "server_accounts.ssh_private_key_encrypted",
        "ipmi_controllers.password_encrypted",
    }

    await _drain_outbox(db)
    assert await secrets_migration_service.remaining_legacy(db) == 0

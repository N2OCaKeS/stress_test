"""Self-drain loop: тик дренажа перешифровывает outbox и завершает окно.

Проверяем `reencrypt_drain_service.drain_tick` поверх живого keystore + БД:

* lazy-тик дренит остаток до нуля и авто-выводит опустевшую версию, force
  при этом не включается;
* force-тик по завершении снимает force-флаг (разблокировка);
* при remaining > 0 версия НЕ выводится, снапшот прогресса пишется.
"""

from __future__ import annotations

import base64
import os

import pytest
from sqlalchemy import text

from src.core.keystore import get_keystore
from src.repositories import credentials as repo
from src.services import (
    key_rotation_service,
    reencrypt_drain_service,
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
    blob = secrets_service.encrypt("secret-value", aad=aad)
    await repo.create(
        adb,
        id=cred_id,
        name="dr_" + cred_id[-4:],
        service="jira",
        scope="personal",
        owner_user_id="usr_dr_owner",
        owner_dept_id=None,
        owner_user_dept_id="dep_dr",
        login=None,
        secret_encrypted=blob,
        status="active",
        created_by="usr_dr_owner",
    )
    await adb.flush()


@pytest.mark.asyncio
async def test_lazy_tick_drains_and_auto_retires(adb):
    await _clean(adb)
    ks = get_keystore()
    old = ks.get_active_version()

    await _make_cred(adb, "cred_dr_lazy")
    # lazy-ротация: бампит версию, сеет outbox, force НЕ включает.
    await key_rotation_service.rotate(adb, new_key_b64=_fresh_key_b64())

    tick = await reencrypt_drain_service.drain_tick(adb)
    assert tick["remaining"] == 0
    assert tick["force_active"] is False
    # Опустевшая версия выведена из keystore.
    assert old not in ks.list_versions()

    state = await reencrypt_state_service.get_state(adb)
    assert state.force_active is False
    assert state.remaining == 0


@pytest.mark.asyncio
async def test_force_tick_clears_flag_when_done(adb):
    await _clean(adb)
    ks = get_keystore()
    old = ks.get_active_version()

    await _make_cred(adb, "cred_dr_force")
    result = await key_rotation_service.rotate(
        adb, new_key_b64=_fresh_key_b64(), mode="force"
    )
    assert result["mode"] == "force"
    state = await reencrypt_state_service.get_state(adb)
    assert state.force_active is True

    tick = await reencrypt_drain_service.drain_tick(adb)
    assert tick["remaining"] == 0
    # После завершения force снят — gate разблокирован.
    state = await reencrypt_state_service.get_state(adb)
    assert state.force_active is False
    assert old not in ks.list_versions()


@pytest.mark.asyncio
async def test_self_drain_auto_retire_emits_critical_audit(adb, monkeypatch):
    await _clean(adb)
    ks = get_keystore()
    old = ks.get_active_version()

    await _make_cred(adb, "cred_dr_audit")
    await key_rotation_service.rotate(adb, new_key_b64=_fresh_key_b64())

    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        reencrypt_drain_service.audit_service,
        "emit",
        lambda action, **kw: events.append((action, kw)),
    )

    tick = await reencrypt_drain_service.drain_tick(adb)
    assert tick["remaining"] == 0
    assert old not in ks.list_versions()

    retire_events = [e for e in events if e[0] == "secrets.encryption_auto_retire"]
    assert len(retire_events) == 1
    kw = retire_events[0][1]
    assert kw["details"]["version"] == old
    assert kw["details"]["caller"] == "self_drain"


@pytest.mark.asyncio
async def test_tick_keeps_version_when_remaining(adb, monkeypatch):
    await _clean(adb)
    ks = get_keystore()
    old = ks.get_active_version()

    await _make_cred(adb, "cred_dr_partial_a")
    await _make_cred(adb, "cred_dr_partial_b")
    await key_rotation_service.rotate(adb, new_key_b64=_fresh_key_b64())

    # Симулируем тик, который ничего не обработал (батч пуст / lock занят):
    # остаток > 0, значит опустошения версии нет.
    async def _noop_process(db, *, batch_size):
        return {"processed": 0, "errors": 0, "failed": []}

    monkeypatch.setattr(
        reencrypt_drain_service, "reencrypt_outbox_process", _noop_process
    )

    tick = await reencrypt_drain_service.drain_tick(adb)
    assert tick["remaining"] == 2
    # Инвариант: пока на версии есть строки — не выводим.
    assert old in ks.list_versions()

    state = await reencrypt_state_service.get_state(adb)
    assert state.remaining == 2
    assert state.eta_seconds >= 0

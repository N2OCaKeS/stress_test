"""Unit-тесты `services.reencrypt_outbox_service`.

Покрывают seed → process → status flow и edge case'ы:

* seed заполняет outbox для credentials с legacy-префиксом и пропускает
  credentials под активной версией.
* повторный seed идемпотентен — partial UNIQUE не плодит дубли.
* process переводит pending → done и перешифровывает blob под активный
  ключ; decrypt с активным ключом возвращает тот же plaintext.
* битый ciphertext (с unknown legacy-key) → status=error с
  error_message.
* status возвращает корректные counters.
* race с lazy-путём (cred уже под активной версией) → done без crypto.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text

from src.core.config import get_settings
from src.models import ReencryptOutboxEntry
from src.repositories import credentials as repo
from src.services import reencrypt_outbox_service, secrets_service


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    get_settings.cache_clear()  # type: ignore[attr-defined]
    yield
    get_settings.cache_clear()  # type: ignore[attr-defined]


async def _clean_tables(adb) -> None:
    """Чистим обе таблицы перед сценарием — savepoint откатит после."""
    await adb.execute(text("DELETE FROM reencrypt_outbox_entries"))
    await adb.execute(text("DELETE FROM credentials"))
    await adb.flush()


def _bump_to_v3(monkeypatch) -> None:
    """v2 master → legacy slot v2, активная — v3 (тот же мастер)."""
    monkeypatch.setenv(
        "SECRET_ENCRYPTION_KEY__v2",
        os.environ["SECRET_ENCRYPTION_KEY"],
    )
    monkeypatch.setenv("SECRET_ENCRYPTION_KEY_VERSION", "3")
    get_settings.cache_clear()  # type: ignore[attr-defined]


async def _make_cred(adb, cred_id: str, *, blob: str | None = None) -> None:
    if blob is None:
        aad = secrets_service.aad_for_credential(cred_id)
        blob = secrets_service.encrypt("payload-" + cred_id[-4:], aad=aad)
    await repo.create(
        adb,
        id=cred_id,
        name="ox_" + cred_id[-4:],
        service="jira",
        scope="personal",
        owner_user_id="usr_ox_owner",
        owner_dept_id=None,
        owner_user_dept_id="dep_ox",
        login=None,
        secret_encrypted=blob,
        status="active",
        created_by="usr_ox_owner",
    )


@pytest.mark.asyncio
async def test_seed_empty_table_returns_zero(adb):
    await _clean_tables(adb)
    result = await reencrypt_outbox_service.seed_outbox(adb)
    assert result == {"inserted": 0, "scanned": 0, "active_version": 2}


@pytest.mark.asyncio
async def test_seed_skips_active_version(adb):
    """Credential под активной версией ключа в очередь не попадает."""
    await _clean_tables(adb)
    await _make_cred(adb, "cred_outbox_active1")
    await _make_cred(adb, "cred_outbox_active2")
    await adb.flush()

    result = await reencrypt_outbox_service.seed_outbox(adb)
    assert result["inserted"] == 0
    assert result["scanned"] == 0


@pytest.mark.asyncio
async def test_seed_picks_legacy_rows(adb, monkeypatch):
    """3 row'ы под v2 → bump до v3 → seed публикует 3 pending."""
    await _clean_tables(adb)
    for i in range(3):
        await _make_cred(adb, f"cred_outbox_seed_{i}")
    await adb.flush()

    _bump_to_v3(monkeypatch)

    result = await reencrypt_outbox_service.seed_outbox(adb)
    assert result["inserted"] == 3
    assert result["scanned"] == 3
    assert result["active_version"] == 3

    status = await reencrypt_outbox_service.migration_status(adb)
    assert status["pending"] == 3
    assert status["done"] == 0
    assert status["total"] == 3


@pytest.mark.asyncio
async def test_seed_idempotent(adb, monkeypatch):
    """Повторный seed на той же legacy-cred'е не плодит дубль."""
    await _clean_tables(adb)
    await _make_cred(adb, "cred_outbox_dup_1")
    await adb.flush()
    _bump_to_v3(monkeypatch)

    first = await reencrypt_outbox_service.seed_outbox(adb)
    second = await reencrypt_outbox_service.seed_outbox(adb)

    assert first["inserted"] == 1
    assert second["inserted"] == 0
    # scanned не меняется — credential всё ещё legacy.
    assert second["scanned"] == 1


@pytest.mark.asyncio
async def test_process_batch_reencrypts_under_active_key(adb, monkeypatch):
    """Pending row → process → blob становится `v3$`, status=done."""
    await _clean_tables(adb)
    await _make_cred(adb, "cred_outbox_proc_1")
    await adb.flush()
    _bump_to_v3(monkeypatch)
    await reencrypt_outbox_service.seed_outbox(adb)

    result = await reencrypt_outbox_service.process_batch(adb, batch_size=10)
    assert result["processed"] == 1
    assert result["errors"] == 0

    refreshed = await repo.get_by_id(adb, "cred_outbox_proc_1")
    assert refreshed is not None
    assert refreshed.secret_encrypted.startswith("v3$")
    # decrypt активным ключом должен вернуть оригинальный plaintext
    aad = secrets_service.aad_for_credential("cred_outbox_proc_1")
    assert secrets_service.decrypt(refreshed.secret_encrypted, aad=aad) == "payload-oc_1"

    status = await reencrypt_outbox_service.migration_status(adb)
    assert status["done"] == 1
    assert status["pending"] == 0


@pytest.mark.asyncio
async def test_process_batch_respects_batch_size(adb, monkeypatch):
    """5 pending + batch_size=2 → две обработаны, три остаются pending."""
    await _clean_tables(adb)
    for i in range(5):
        await _make_cred(adb, f"cred_outbox_bsz_{i}")
    await adb.flush()
    _bump_to_v3(monkeypatch)
    await reencrypt_outbox_service.seed_outbox(adb)

    result = await reencrypt_outbox_service.process_batch(adb, batch_size=2)
    assert result["processed"] == 2

    status = await reencrypt_outbox_service.migration_status(adb)
    assert status["done"] == 2
    assert status["pending"] == 3


@pytest.mark.asyncio
async def test_process_skips_already_active(adb, monkeypatch):
    """Если lazy-путь успел перешифровать row до process'а — done без crypto."""
    await _clean_tables(adb)
    cred_id = "cred_outbox_lazy_race"
    await _make_cred(adb, cred_id)
    await adb.flush()
    _bump_to_v3(monkeypatch)
    await reencrypt_outbox_service.seed_outbox(adb)

    # Имитируем lazy-путь: переписываем ciphertext под v3 руками.
    aad = secrets_service.aad_for_credential(cred_id)
    new_blob = secrets_service.encrypt("payload-race", aad=aad)
    cred = await repo.get_by_id(adb, cred_id)
    cred.secret_encrypted = new_blob
    await adb.flush()

    result = await reencrypt_outbox_service.process_batch(adb, batch_size=10)
    assert result["processed"] == 1
    assert result["errors"] == 0

    # blob, который записали имитированным lazy-путём, не перетёрся
    refreshed = await repo.get_by_id(adb, cred_id)
    assert refreshed.secret_encrypted == new_blob


@pytest.mark.asyncio
async def test_process_records_error_for_missing_legacy_key(adb, monkeypatch):
    """Bump'ем без переноса мастер-ключа в legacy slot → decrypt падает,
    row уходит в status=error с заполненным error_message."""
    await _clean_tables(adb)
    await _make_cred(adb, "cred_outbox_err_1")
    await adb.flush()

    # Bump без выставления SECRET_ENCRYPTION_KEY__v2 — старый ключ "потерян".
    monkeypatch.setenv("SECRET_ENCRYPTION_KEY_VERSION", "3")
    monkeypatch.delenv("SECRET_ENCRYPTION_KEY__v2", raising=False)
    get_settings.cache_clear()  # type: ignore[attr-defined]

    await reencrypt_outbox_service.seed_outbox(adb)

    result = await reencrypt_outbox_service.process_batch(adb, batch_size=10)
    assert result["processed"] == 0
    assert result["errors"] == 1
    assert result["failed"][0]["error_code"] == "ENCRYPTION_KEY_MISSING"

    status = await reencrypt_outbox_service.migration_status(adb)
    assert status["error"] == 1


@pytest.mark.asyncio
async def test_process_empty_returns_zero(adb):
    """Пустая очередь — { processed:0, errors:0, failed:[] }."""
    await _clean_tables(adb)
    result = await reencrypt_outbox_service.process_batch(adb, batch_size=10)
    assert result == {"processed": 0, "errors": 0, "failed": []}


@pytest.mark.asyncio
async def test_process_batch_size_le_zero_noop(adb):
    """batch_size <= 0 → ранний выход без запроса к БД."""
    result = await reencrypt_outbox_service.process_batch(adb, batch_size=0)
    assert result == {"processed": 0, "errors": 0, "failed": []}


@pytest.mark.asyncio
async def test_seed_then_process_then_seed_again(adb, monkeypatch):
    """После done пройти новый раунд (например, ещё одна ротация)."""
    await _clean_tables(adb)
    await _make_cred(adb, "cred_outbox_round_1")
    await adb.flush()
    _bump_to_v3(monkeypatch)
    await reencrypt_outbox_service.seed_outbox(adb)
    await reencrypt_outbox_service.process_batch(adb, batch_size=10)

    # Ещё одна ротация: v3 → v4.
    monkeypatch.setenv(
        "SECRET_ENCRYPTION_KEY__v3",
        os.environ["SECRET_ENCRYPTION_KEY"],
    )
    monkeypatch.setenv("SECRET_ENCRYPTION_KEY_VERSION", "4")
    get_settings.cache_clear()  # type: ignore[attr-defined]

    seed_2 = await reencrypt_outbox_service.seed_outbox(adb)
    assert seed_2["inserted"] == 1
    assert seed_2["active_version"] == 4

    # В таблице теперь 1 done (старая) + 1 pending (новая).
    status = await reencrypt_outbox_service.migration_status(adb)
    assert status["pending"] == 1
    assert status["done"] == 1
    assert status["total"] == 2

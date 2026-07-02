"""Self-drain reencrypt-outbox внутри server_service + режимы lazy/force.

Проверяем:

* durable force-флаг (`get_force_active`/`set_force_active`) и его дефолт;
* `rotate(mode=...)` взводит/не взводит force;
* `drain_once` осушает outbox батчами, доводит до конца, авто-retire срабатывает,
  force снимается;
* `finish_if_drained` не завершает миграцию, пока остаток > 0 (инвариант retire);
* шардинг: два параллельных claim'а берут непересекающиеся блоки (SKIP LOCKED);
* `compute_retry_after` зажимает Retry-After в [min, max] с буфером;
* `status()` отдаёт mode/force_active/eta/throughput/versions_in_keystore.
"""

from __future__ import annotations

import asyncio
import base64
import os

import pytest
from sqlalchemy import delete

from src.core.config import get_settings
from src.core.keystore import get_keystore
from src.db.session import AsyncSessionLocal
from src.models import ReencryptOutboxEntry
from src.services import key_rotation_service, secrets_drainer, secrets_migration_service


def _fresh_key_b64() -> str:
    return base64.b64encode(os.urandom(32)).decode("ascii")


async def _drain_via_loop(db, *, force: bool) -> None:
    """Крутить drain_once до полного осушения (как фоновый таск)."""
    settings = get_settings()
    for _ in range(50):
        result = await secrets_drainer.drain_once(db, force=force, settings=settings)
        if result["finished"]:
            return
        if result["claimed"] == 0 and result["reseeded"] == 0:
            return
    raise AssertionError("drain did not finish within iteration budget")


class TestForceFlag:
    @pytest.mark.asyncio
    async def test_default_is_off(self, db):
        assert await secrets_migration_service.get_force_active(db) is False

    @pytest.mark.asyncio
    async def test_set_and_read_back(self, db):
        await secrets_migration_service.set_force_active(db, True)
        assert await secrets_migration_service.get_force_active(db) is True
        await secrets_migration_service.set_force_active(db, False)
        assert await secrets_migration_service.get_force_active(db) is False


class TestRotateMode:
    @pytest.mark.asyncio
    async def test_lazy_does_not_set_force(self, db):
        result = await key_rotation_service.rotate(db, new_key_b64=_fresh_key_b64())
        assert result["mode"] == "lazy"
        assert result["force_active"] is False
        assert await secrets_migration_service.get_force_active(db) is False

    @pytest.mark.asyncio
    async def test_force_sets_flag(self, db):
        result = await key_rotation_service.rotate(
            db, new_key_b64=_fresh_key_b64(), mode="force"
        )
        assert result["mode"] == "force"
        assert result["force_active"] is True
        assert await secrets_migration_service.get_force_active(db) is True

    @pytest.mark.asyncio
    async def test_force_idempotent_repeat_reasserts_flag(self, db):
        key = _fresh_key_b64()
        first = await key_rotation_service.rotate(db, new_key_b64=key, mode="force")
        assert first["idempotent"] is False
        # Снимаем force и повторяем force-ротацию тем же ключом — idempotent,
        # но force снова взводится.
        await secrets_migration_service.set_force_active(db, False)
        second = await key_rotation_service.rotate(db, new_key_b64=key, mode="force")
        assert second["idempotent"] is True
        assert second["force_active"] is True
        assert await secrets_migration_service.get_force_active(db) is True


class TestDrainLazy:
    @pytest.mark.asyncio
    async def test_lazy_drain_reaches_zero_and_auto_retires(
        self, db, make_server, make_account
    ):
        ks = get_keystore()
        old_version = ks.get_active_version()
        server = await make_server()
        await make_account(server_id=server.id, password="lazy-drain-pwd")

        await key_rotation_service.rotate(db, new_key_b64=_fresh_key_b64())
        new_version = ks.get_active_version()
        assert new_version == old_version + 1

        await _drain_via_loop(db, force=False)

        status = await secrets_migration_service.status(db)
        assert status["remaining_legacy_total"] == 0
        assert status["outbox"]["pending"] == 0
        # Авто-retire увёл старую версию.
        assert old_version not in ks.list_versions()
        # Force не трогали — сервис не блокировался.
        assert status["force_active"] is False


class TestDrainForce:
    @pytest.mark.asyncio
    async def test_force_drain_clears_flag_and_retires(
        self, db, make_server, make_account
    ):
        ks = get_keystore()
        old_version = ks.get_active_version()
        server = await make_server()
        await make_account(server_id=server.id, password="force-drain-pwd")

        result = await key_rotation_service.rotate(
            db, new_key_b64=_fresh_key_b64(), mode="force"
        )
        assert result["force_active"] is True
        assert await secrets_migration_service.get_force_active(db) is True

        await _drain_via_loop(db, force=True)

        # Force снят, старая версия выведена, остаток нулевой.
        assert await secrets_migration_service.get_force_active(db) is False
        assert old_version not in ks.list_versions()
        status = await secrets_migration_service.status(db)
        assert status["remaining_legacy_total"] == 0
        assert status["force_active"] is False
        assert status["mode"] == "lazy"


class TestFinishInvariant:
    @pytest.mark.asyncio
    async def test_finish_false_while_rows_remain(
        self, db, make_server, make_account
    ):
        ks = get_keystore()
        old_version = ks.get_active_version()
        server = await make_server()
        await make_account(server_id=server.id, login="a", password="p1")
        await make_account(server_id=server.id, login="b", password="p2")

        await key_rotation_service.rotate(
            db, new_key_b64=_fresh_key_b64(), mode="force"
        )

        # Закрываем ровно одну outbox-row — вторая ещё на старой версии.
        claimed = await secrets_migration_service.claim_pending(db, limit=1)
        assert len(claimed) == 1
        await secrets_migration_service.finalize_done(db, claimed[0]["id"])

        finished = await secrets_migration_service.finish_if_drained(db)
        assert finished is False
        # Инвариант retire: версия НЕ выведена, пока на ней есть строки.
        assert old_version in ks.list_versions()
        # Force остаётся взведён — миграция не завершена.
        assert await secrets_migration_service.get_force_active(db) is True


class TestRetryAfterMath:
    def test_zero_remaining_hits_floor(self):
        assert secrets_migration_service.compute_retry_after(0) == 15

    def test_small_remaining_hits_floor(self):
        # eta = 25/25 = 1s; +5 buffer = 6; floor 15.
        assert secrets_migration_service.compute_retry_after(25) == 15

    def test_mid_remaining_between_bounds(self):
        # eta = 1000/25 = 40s; +5 = 45; в [15, 300].
        assert secrets_migration_service.compute_retry_after(1000) == 45

    def test_huge_remaining_hits_ceiling(self):
        assert secrets_migration_service.compute_retry_after(10_000_000) == 300


class TestStatusExtras:
    @pytest.mark.asyncio
    async def test_status_reports_mode_and_progress_fields(self, db):
        status = await secrets_migration_service.status(db)
        for key in ("mode", "force_active", "eta_seconds", "throughput", "versions_in_keystore"):
            assert key in status
        assert status["mode"] == "lazy"
        assert status["force_active"] is False
        assert status["throughput"] == get_settings().reencrypt_throughput_per_second
        assert isinstance(status["versions_in_keystore"], list)
        assert get_keystore().get_active_version() in status["versions_in_keystore"]

    @pytest.mark.asyncio
    async def test_status_mode_force_when_flag_set(self, db):
        await secrets_migration_service.set_force_active(db, True)
        status = await secrets_migration_service.status(db)
        assert status["mode"] == "force"
        assert status["force_active"] is True


class TestSkipLockedSharding:
    @pytest.mark.asyncio
    async def test_two_concurrent_claims_take_disjoint_blocks(self):
        """Два параллельных claim'а через FOR UPDATE SKIP LOCKED не пересекаются.

        Данные комитятся в реальную БД (отдельно от savepoint-фикстуры) и
        подчищаются в finally — иначе SKIP LOCKED негде проявиться (нужны две
        живые транзакции на разных коннектах).
        """
        ids = [f"rox_shard_{i}" for i in range(6)]
        try:
            async with AsyncSessionLocal() as seed:
                for i, rid in enumerate(ids):
                    seed.add(ReencryptOutboxEntry(
                        id=rid,
                        entity_type="server_account",
                        entity_id=f"acc_shard_{i}",
                        legacy_ciphertext="v1$AAAA$BBBB",
                        status="pending",
                    ))
                await seed.commit()

            async with AsyncSessionLocal() as s1, AsyncSessionLocal() as s2:
                r1, r2 = await asyncio.gather(
                    secrets_migration_service.claim_pending(s1, limit=3),
                    secrets_migration_service.claim_pending(s2, limit=3),
                )
            got1 = {x["id"] for x in r1}
            got2 = {x["id"] for x in r2}
            # Непересекающиеся блоки, суммарно — все 6, ни одна не заклеймлена дважды.
            assert got1.isdisjoint(got2)
            assert got1 | got2 == set(ids)
        finally:
            async with AsyncSessionLocal() as cleanup:
                await cleanup.execute(
                    delete(ReencryptOutboxEntry).where(
                        ReencryptOutboxEntry.id.in_(ids)
                    )
                )
                await cleanup.commit()

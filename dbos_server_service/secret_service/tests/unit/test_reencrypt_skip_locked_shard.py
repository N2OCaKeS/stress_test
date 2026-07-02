"""Шардинг дренажа через `FOR UPDATE SKIP LOCKED`.

Клейм батча в `reencrypt_outbox_service.process_batch` берёт pending-строки
с `FOR UPDATE SKIP LOCKED`. Это то, что позволяет нескольким репликам
secret_service дренить outbox параллельно, не наступая друг на друга: пока
одна сессия держит блок строк, вторая их пропускает и берёт следующие.

Здесь проверяем инвариант напрямую на двух живых соединениях: два claim'а
не пересекаются по строкам.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.models import STATUS_PENDING, Credential, ReencryptOutboxEntry
from src.services import secrets_service


_DSN = (
    os.environ.get("TEST_DATABASE_URL_SYNC")
    or os.environ.get("TEST_DATABASE_URL")
    or os.environ["DATABASE_URL"]
)


def _claim_stmt(limit: int):
    """Копия claim-запроса из process_batch."""
    return (
        select(ReencryptOutboxEntry)
        .where(ReencryptOutboxEntry.status == STATUS_PENDING)
        .order_by(ReencryptOutboxEntry.seeded_at, ReencryptOutboxEntry.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )


@pytest.mark.asyncio
async def test_skip_locked_shards_between_two_sessions():
    engine = create_async_engine(_DSN, pool_size=4, max_overflow=4)
    ids = [f"cred_shard_{i}" for i in range(4)]
    try:
        # ── Arrange: 4 credential'а + 4 pending outbox-row'а, закоммичены ──
        async with AsyncSession(engine, expire_on_commit=False) as seed:
            await seed.execute(text("DELETE FROM reencrypt_outbox_entries"))
            await seed.execute(
                text("DELETE FROM credentials WHERE id LIKE 'cred_shard_%'")
            )
            for i, cid in enumerate(ids):
                aad = secrets_service.aad_for_credential(cid)
                blob = secrets_service.encrypt("s", aad=aad)
                seed.add(Credential(
                    id=cid,
                    name=f"shard_{i}",
                    service="jira",
                    scope="personal",
                    owner_user_id="usr_shard",
                    owner_user_dept_id="dep_shard",
                    secret_encrypted=blob,
                    status="active",
                    created_by="usr_shard",
                ))
                seed.add(ReencryptOutboxEntry(
                    id=f"rox_shard_{i}",
                    credential_id=cid,
                    source_version=2,
                    target_version=3,
                    status=STATUS_PENDING,
                ))
            await seed.commit()

        # ── Act: два независимых соединения клеймят по 2 строки ──
        s1 = AsyncSession(engine, expire_on_commit=False)
        s2 = AsyncSession(engine, expire_on_commit=False)
        try:
            await s1.begin()
            first = list((await s1.execute(_claim_stmt(2))).scalars().all())
            # s1 держит блокировку на своих 2 строках; s2 должна их пропустить.
            await s2.begin()
            second = list((await s2.execute(_claim_stmt(2))).scalars().all())

            first_ids = {e.id for e in first}
            second_ids = {e.id for e in second}

            assert len(first_ids) == 2
            assert len(second_ids) == 2
            # Ключевой инвариант шардинга — блоки не пересекаются.
            assert first_ids.isdisjoint(second_ids)
            assert first_ids | second_ids == {f"rox_shard_{i}" for i in range(4)}
        finally:
            await s1.rollback()
            await s2.rollback()
            await s1.close()
            await s2.close()
    finally:
        # ── Cleanup: убираем закоммиченные строки (savepoint их не откатит) ──
        async with AsyncSession(engine, expire_on_commit=False) as cleanup:
            await cleanup.execute(text("DELETE FROM reencrypt_outbox_entries"))
            await cleanup.execute(
                text("DELETE FROM credentials WHERE id LIKE 'cred_shard_%'")
            )
            await cleanup.commit()
        await engine.dispose()

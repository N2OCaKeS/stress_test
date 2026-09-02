"""`secrets_migration_service._count_by_version` теперь SQL-side (substring + GROUP BY).

Раньше функция тянула все ciphertext'ы в Python и парсила regex'ом per-row.
На крупных таблицах это выедало pool и память; переписана в чистый SQL через
`substring(col,'^v([0-9]+)\\$')::bigint + GROUP BY`. `::bigint` (не `::int`)
— чтобы не повторить overflow, который ловили в secret_service migration_status.
"""

from __future__ import annotations

import pytest

from src.models import IpmiController, ServerAccount
from src.services import secrets_migration_service


class TestCountByVersionSql:
    async def test_empty_db_returns_empty_dict(self, db):
        result = await secrets_migration_service._count_by_version(db)
        assert result == {}

    async def test_groups_by_wire_version(self, db, make_server, make_account, make_ipmi):
        """3 server_account-row'а на v2 + 1 ipmi на v2 → `{2: 4}`."""
        # Cycle: 3 серверa + аккаунт, 1 ipmi — все шифруются активным v2.
        srv = await make_server(department_id="dep_a")
        for i in range(3):
            await make_account(server_id=srv.id, login=f"u{i}", password=f"pw_{i}")
        await make_ipmi(server_id=srv.id, password="ipmi-pw")

        result = await secrets_migration_service._count_by_version(db)
        assert result == {2: 4}, result

    async def test_per_column_split(self, db, make_server, make_account, make_ipmi):
        srv = await make_server(department_id="dep_a")
        await make_account(server_id=srv.id, login="a", password="pw1")
        await make_account(server_id=srv.id, login="b", password="pw2")
        await make_ipmi(server_id=srv.id, password="ipmi-pw")

        sa_breakdown = await secrets_migration_service._count_by_version_for_column(
            db, ServerAccount.password_encrypted,
        )
        assert sa_breakdown == {2: 2}, sa_breakdown
        ipmi_breakdown = await secrets_migration_service._count_by_version_for_column(
            db, IpmiController.password_encrypted,
        )
        assert ipmi_breakdown == {2: 1}, ipmi_breakdown

    async def test_malformed_ciphertext_dropped_from_count(self, db, make_server, make_account):
        """`password_encrypted` без `v<N>$` префикса не должен фигурировать в by_version."""
        from sqlalchemy import text

        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="u", password="pw")
        # Грязная подмена ciphertext'а через UPDATE — нам нужен malformed формат,
        # чтобы подтвердить, что substring(...) IS NULL отбрасывает row из GROUP BY.
        await db.execute(
            text("UPDATE server_accounts SET password_encrypted = :bad WHERE id = :id"),
            {"bad": "totally-malformed-no-prefix", "id": acc.id},
        )
        await db.flush()
        result = await secrets_migration_service._count_by_version(db)
        # Row есть, но в by_version его нет — `total - sum(by_version) >= 1`.
        assert 2 not in result or result.get(2, 0) == 0, result

"""Расширенный response GET /internal/secrets/migration_status.

Проверяет per-column breakdown, `migrated_pct`, `remaining_legacy_total`,
`outbox_pending` — добавлены поверх legacy-полей (`remaining`/`total`/
`by_version`/`outbox`).
"""

from __future__ import annotations

import pytest

from tests._helpers import auth_hdr as _hdr

BASE = "/api/server/v1/internal/secrets"


class TestExtendedFields:
    async def test_empty_db_shape(self, client, worker_pat_token):
        resp = await client.get(
            f"{BASE}/migration_status", headers=_hdr(worker_pat_token)
        )
        assert resp.status_code == 200
        body = resp.json()
        # Новые поля присутствуют и пусты.
        assert "server_account_password_encrypted" in body
        assert "ipmi_controller_password_encrypted" in body
        assert "remaining_legacy_total" in body
        assert "migrated_pct" in body
        assert "outbox_pending" in body

        sa = body["server_account_password_encrypted"]
        ipmi = body["ipmi_controller_password_encrypted"]
        assert sa == {"total": 0, "by_version": {}, "remaining_legacy": 0}
        assert ipmi == {"total": 0, "by_version": {}, "remaining_legacy": 0}
        assert body["remaining_legacy_total"] == 0
        # При total=0 migrated_pct=100 — нечего мигрировать = всё мигрировано.
        assert body["migrated_pct"] == 100.0
        assert body["outbox_pending"] == 0

    async def test_per_column_counts(
        self, client, worker_pat_token, make_server, make_account, make_ipmi,
    ):
        srv = await make_server(department_id="dep_a")
        await make_account(server_id=srv.id, login="root", password="pwd1")
        await make_account(server_id=srv.id, login="ops", password="pwd2")
        await make_ipmi(server_id=srv.id, password="ipmi-pwd")

        resp = await client.get(
            f"{BASE}/migration_status", headers=_hdr(worker_pat_token)
        )
        body = resp.json()
        active = body["active_version"]

        sa = body["server_account_password_encrypted"]
        assert sa["total"] == 2
        # Все зашифрованы активным ключом — remaining_legacy = 0.
        assert sa["remaining_legacy"] == 0
        # by_version ключ — стрingified int в JSON.
        assert sa["by_version"].get(str(active)) == 2 or sa["by_version"].get(active) == 2

        ipmi = body["ipmi_controller_password_encrypted"]
        assert ipmi["total"] == 1
        assert ipmi["remaining_legacy"] == 0

        assert body["remaining_legacy_total"] == 0
        assert body["migrated_pct"] == 100.0

    async def test_remaining_legacy_with_v1_token(
        self, client, worker_pat_token, make_server, make_account, db,
    ):
        """v1-ciphertext под active=v2 → per-column remaining_legacy=1, migrated_pct<100."""
        from src.core.config import get_settings

        settings = get_settings()
        if settings.server_encryption_key_version == 1:
            pytest.skip("active=v1; cannot synthesize legacy token below it")

        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, password="x")
        original = acc.password_encrypted
        _, nonce_b64, ct_b64 = original.split("$", 2)
        acc.password_encrypted = f"v1${nonce_b64}${ct_b64}"
        await db.flush()
        await db.commit()

        resp = await client.get(
            f"{BASE}/migration_status", headers=_hdr(worker_pat_token)
        )
        body = resp.json()

        sa = body["server_account_password_encrypted"]
        assert sa["total"] == 1
        # v1 != active → remaining_legacy=1.
        assert sa["remaining_legacy"] == 1
        # by_version содержит v1.
        assert sa["by_version"].get("1") == 1 or sa["by_version"].get(1) == 1

        # Общий счётчик и progress совпадают с per-column.
        assert body["remaining_legacy_total"] == 1
        # 1 row, 0 мигрировано → 0.0%.
        assert body["migrated_pct"] == 0.0

    async def test_outbox_pending_top_level(
        self, client, worker_pat_token, db,
    ):
        """`outbox_pending` дублирует `outbox.pending` на top-level."""
        from src.models import ReencryptOutboxEntry

        # Пара pending row'ов.
        for i in range(3):
            db.add(ReencryptOutboxEntry(
                id=f"rox_top_{i}",
                entity_type="server_account",
                entity_id=f"acc_top_{i}",
                legacy_ciphertext="v1$abc$def",
                status="pending",
            ))
        await db.commit()

        resp = await client.get(
            f"{BASE}/migration_status", headers=_hdr(worker_pat_token)
        )
        body = resp.json()
        assert body["outbox"]["pending"] == 3
        assert body["outbox_pending"] == 3

    async def test_migrated_pct_rounding(
        self, client, worker_pat_token, make_server, make_account, db,
    ):
        """`migrated_pct` округляется до десятых."""
        from src.core.config import get_settings

        settings = get_settings()
        if settings.server_encryption_key_version == 1:
            pytest.skip("active=v1; cannot synthesize legacy token")

        srv = await make_server(department_id="dep_a")
        # 3 row'ы: 2 активные, 1 legacy → 66.7% мигрировано.
        accs = []
        for i in range(3):
            accs.append(await make_account(
                server_id=srv.id, login=f"u{i}", password=f"p{i}",
            ))
        # Подменим один на v1$ префикс.
        target = accs[0]
        original = target.password_encrypted
        _, nonce_b64, ct_b64 = original.split("$", 2)
        target.password_encrypted = f"v1${nonce_b64}${ct_b64}"
        await db.flush()
        await db.commit()

        resp = await client.get(
            f"{BASE}/migration_status", headers=_hdr(worker_pat_token)
        )
        body = resp.json()
        # 2/3 = 66.666... → 66.7 после round(_,1).
        assert body["migrated_pct"] == 66.7
        assert body["remaining_legacy_total"] == 1

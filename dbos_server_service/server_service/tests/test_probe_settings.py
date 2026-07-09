"""Интеграционные тесты `/api/server/v1/settings/probes` + internal-read.

Платформенный singleton под `account_admin`. GET отдаёт текущие настройки
(дефолт, если строки ещё нет); PUT делает upsert с частичным слиянием и
проверкой порядка интервалов. Не-`account_admin` отбивается 403. Internal-read
воркера — под грантом `(server, prepare_callback)`.
"""

from __future__ import annotations

from tests._helpers import assert_error, auth_hdr as _hdr

BASE = "/api/server/v1/settings/probes"
INTERNAL = "/api/server/v1/internal/settings/probes"


# ── GET ───────────────────────────────────────────────────────────────────────

class TestGetSettings:
    async def test_defaults_when_empty(self, client, account_admin_token):
        resp = await client.get(BASE, headers=_hdr(account_admin_token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["reachability_probe_interval_seconds"] == 60
        assert body["power_probe_interval_seconds"] == 300
        assert body["reachability_probe_enabled"] is True
        assert body["power_probe_enabled"] is True

    async def test_requires_auth(self, client):
        resp = await client.get(BASE)
        assert_error(resp, 401, "ACCESS_TOKEN_MISSING")


# ── account_admin gate ──────────────────────────────────────────────────────

class TestAccountAdminGate:
    async def test_department_admin_forbidden(self, client, admin_token):
        resp = await client.get(BASE, headers=_hdr(admin_token))
        assert_error(resp, 403, "ACCOUNT_ADMIN_REQUIRED")

    async def test_reader_forbidden(self, client, reader_token_a):
        resp = await client.get(BASE, headers=_hdr(reader_token_a))
        assert_error(resp, 403, "ACCOUNT_ADMIN_REQUIRED")

    async def test_put_department_admin_forbidden(self, client, admin_token):
        resp = await client.put(
            BASE,
            headers=_hdr(admin_token),
            json={"reachability_probe_interval_seconds": 30},
        )
        assert_error(resp, 403, "ACCOUNT_ADMIN_REQUIRED")


# ── PUT ─────────────────────────────────────────────────────────────────────

class TestPutSettings:
    async def test_put_changes_and_persists(self, client, account_admin_token):
        resp = await client.put(
            BASE,
            headers=_hdr(account_admin_token),
            json={
                "reachability_probe_interval_seconds": 30,
                "power_probe_interval_seconds": 120,
                "reachability_probe_enabled": False,
                "power_probe_enabled": True,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["reachability_probe_interval_seconds"] == 30
        assert body["power_probe_interval_seconds"] == 120
        assert body["reachability_probe_enabled"] is False

        again = await client.get(BASE, headers=_hdr(account_admin_token))
        assert again.json()["reachability_probe_interval_seconds"] == 30
        assert again.json()["power_probe_interval_seconds"] == 120

    async def test_put_partial_keeps_other_fields(self, client, account_admin_token):
        await client.put(
            BASE,
            headers=_hdr(account_admin_token),
            json={
                "reachability_probe_interval_seconds": 45,
                "power_probe_interval_seconds": 200,
            },
        )
        # Второй PUT трогает только enabled — интервалы должны сохраниться.
        resp = await client.put(
            BASE,
            headers=_hdr(account_admin_token),
            json={"power_probe_enabled": False},
        )
        body = resp.json()
        assert body["reachability_probe_interval_seconds"] == 45
        assert body["power_probe_interval_seconds"] == 200
        assert body["power_probe_enabled"] is False


# ── Validation ──────────────────────────────────────────────────────────────

class TestValidation:
    async def test_reachability_below_min_rejected(self, client, account_admin_token):
        resp = await client.put(
            BASE,
            headers=_hdr(account_admin_token),
            json={"reachability_probe_interval_seconds": 10},
        )
        assert resp.status_code == 422

    async def test_power_below_min_rejected(self, client, account_admin_token):
        resp = await client.put(
            BASE,
            headers=_hdr(account_admin_token),
            json={"power_probe_interval_seconds": 30},
        )
        assert resp.status_code == 422

    async def test_power_below_reachability_rejected(self, client, account_admin_token):
        resp = await client.put(
            BASE,
            headers=_hdr(account_admin_token),
            json={
                "reachability_probe_interval_seconds": 120,
                "power_probe_interval_seconds": 90,
            },
        )
        assert_error(resp, 400, "POWER_INTERVAL_BELOW_REACHABILITY")

    async def test_power_below_reachability_via_merge_rejected(
        self, client, account_admin_token,
    ):
        # reachability поднимаем выше текущего power (300) одним полем — порядок
        # должен провериться на слитых значениях, а не только внутри запроса.
        resp = await client.put(
            BASE,
            headers=_hdr(account_admin_token),
            json={"reachability_probe_interval_seconds": 600},
        )
        assert_error(resp, 400, "POWER_INTERVAL_BELOW_REACHABILITY")


# ── Internal (worker) ───────────────────────────────────────────────────────

class TestInternalRead:
    async def test_worker_bot_reads_settings(self, client, worker_bot_token_a):
        resp = await client.get(INTERNAL, headers=_hdr(worker_bot_token_a))
        assert resp.status_code == 200
        body = resp.json()
        assert body["reachability_probe_interval_seconds"] == 60
        assert body["power_probe_interval_seconds"] == 300

    async def test_worker_reads_updated_values(
        self, client, account_admin_token, worker_bot_token_a,
    ):
        await client.put(
            BASE,
            headers=_hdr(account_admin_token),
            json={"reachability_probe_interval_seconds": 90},
        )
        resp = await client.get(INTERNAL, headers=_hdr(worker_bot_token_a))
        assert resp.json()["reachability_probe_interval_seconds"] == 90

    async def test_reader_forbidden_on_internal(self, client, reader_token_a):
        resp = await client.get(INTERNAL, headers=_hdr(reader_token_a))
        assert_error(resp, 403, "PERMISSION_DENIED")

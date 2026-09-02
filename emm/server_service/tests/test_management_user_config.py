"""Интеграционные тесты `/api/server/v1/management-user-config`.

Платформенный singleton под `account_admin`. GET отдаёт текущий конфиг (дефолт,
если строки ещё нет); PUT заменяет/обновляет login и пер-режимные настройки.
Не-`account_admin` отбивается 403. Валидация login / групп / пустых команд.
"""

from __future__ import annotations

from tests._helpers import assert_error, auth_hdr as _hdr

BASE = "/api/server/v1/management-user-config"

_ALL_MODES = {"astra_orel", "astra_smolensk", "astra_voronezh", "other_os"}


# ── GET ───────────────────────────────────────────────────────────────────────

class TestGetConfig:
    async def test_default_when_empty(self, client, account_admin_token):
        resp = await client.get(BASE, headers=_hdr(account_admin_token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["login"] == "dbos"
        assert set(body["modes"]) == _ALL_MODES
        for cfg in body["modes"].values():
            assert cfg["groups"] == []
            assert cfg["extra_create_commands"] == []
        assert body["login_changed"] is False

    async def test_requires_auth(self, client):
        resp = await client.get(BASE)
        assert_error(resp, 401, "ACCESS_TOKEN_MISSING")


# ── account_admin gate ─────────────────────────────────────────────────────────

class TestAccountAdminGate:
    async def test_department_admin_forbidden(self, client, admin_token):
        resp = await client.get(BASE, headers=_hdr(admin_token))
        assert_error(resp, 403, "ACCOUNT_ADMIN_REQUIRED")

    async def test_reader_forbidden(self, client, reader_token_a):
        resp = await client.get(BASE, headers=_hdr(reader_token_a))
        assert_error(resp, 403, "ACCOUNT_ADMIN_REQUIRED")

    async def test_loging_admin_forbidden(self, client, loging_admin_token):
        resp = await client.put(
            BASE, headers=_hdr(loging_admin_token), json={"login": "ctl"},
        )
        assert_error(resp, 403, "ACCOUNT_ADMIN_REQUIRED")


# ── PUT ─────────────────────────────────────────────────────────────────────────

class TestPutConfig:
    async def test_put_per_mode_commands_and_groups(self, client, account_admin_token):
        payload = {
            "modes": {
                "astra_smolensk": {
                    "groups": ["astra-admin", "shadow"],
                    "extra_create_commands": [
                        "pdpl-user -i 63 ctl",
                        "usermod -aG astra-admin ctl",
                    ],
                },
                "other_os": {
                    "groups": ["sudo"],
                    "extra_create_commands": [],
                },
            },
        }
        resp = await client.put(
            BASE, headers=_hdr(account_admin_token), json=payload,
        )
        assert resp.status_code == 200
        body = resp.json()
        smolensk = body["modes"]["astra_smolensk"]
        assert smolensk["groups"] == ["astra-admin", "shadow"]
        assert smolensk["extra_create_commands"] == [
            "pdpl-user -i 63 ctl",
            "usermod -aG astra-admin ctl",
        ]
        assert body["modes"]["other_os"]["groups"] == ["sudo"]
        # Неприсланные режимы остаются дефолтными, но всегда присутствуют.
        assert set(body["modes"]) == _ALL_MODES
        assert body["modes"]["astra_orel"]["groups"] == []
        # login не присылали — без изменений.
        assert body["login"] == "dbos"
        assert body["login_changed"] is False

    async def test_put_persists(self, client, account_admin_token):
        await client.put(
            BASE,
            headers=_hdr(account_admin_token),
            json={"modes": {"astra_orel": {"groups": ["g1"], "extra_create_commands": ["c1"]}}},
        )
        resp = await client.get(BASE, headers=_hdr(account_admin_token))
        body = resp.json()
        assert body["modes"]["astra_orel"]["groups"] == ["g1"]
        assert body["modes"]["astra_orel"]["extra_create_commands"] == ["c1"]

    async def test_put_login_change_flag(self, client, account_admin_token):
        resp = await client.put(
            BASE, headers=_hdr(account_admin_token), json={"login": "ctl_user"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["login"] == "ctl_user"
        assert body["login_changed"] is True
        assert body["previous_login"] == "dbos"

    async def test_put_same_login_no_change_flag(self, client, account_admin_token):
        resp = await client.put(
            BASE, headers=_hdr(account_admin_token), json={"login": "dbos"},
        )
        body = resp.json()
        assert body["login_changed"] is False
        assert body["previous_login"] is None

    async def test_put_partial_merge_keeps_other_modes(self, client, account_admin_token):
        await client.put(
            BASE,
            headers=_hdr(account_admin_token),
            json={"modes": {"astra_orel": {"groups": ["orel-g"]}}},
        )
        # Второй PUT трогает только smolensk — orel должен сохраниться.
        resp = await client.put(
            BASE,
            headers=_hdr(account_admin_token),
            json={"modes": {"astra_smolensk": {"groups": ["smol-g"]}}},
        )
        body = resp.json()
        assert body["modes"]["astra_orel"]["groups"] == ["orel-g"]
        assert body["modes"]["astra_smolensk"]["groups"] == ["smol-g"]


# ── Validation ──────────────────────────────────────────────────────────────────

class TestValidation:
    async def test_invalid_login_rejected(self, client, account_admin_token):
        resp = await client.put(
            BASE, headers=_hdr(account_admin_token), json={"login": "1bad name!"},
        )
        assert resp.status_code == 422

    async def test_empty_command_rejected(self, client, account_admin_token):
        resp = await client.put(
            BASE,
            headers=_hdr(account_admin_token),
            json={"modes": {"other_os": {"extra_create_commands": ["   "]}}},
        )
        assert resp.status_code == 422

    async def test_unknown_mode_rejected(self, client, account_admin_token):
        resp = await client.put(
            BASE,
            headers=_hdr(account_admin_token),
            json={"modes": {"windows": {"groups": []}}},
        )
        assert resp.status_code == 422

    async def test_invalid_group_rejected(self, client, account_admin_token):
        resp = await client.put(
            BASE,
            headers=_hdr(account_admin_token),
            json={"modes": {"other_os": {"groups": ["BAD GROUP"]}}},
        )
        assert resp.status_code == 422

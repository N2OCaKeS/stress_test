"""Интеграционные тесты `/api/server/v1/admin/password-policy`.

Платформенный singleton под `account_admin`. GET отдаёт текущую политику
(засеяна миграцией дефолтами), PUT делает upsert с частичным слиянием и
обновляет процессный кэш `core/password_policy`. Не-`account_admin` отбивается
403 ровно как на `/admin/encryption/*` и `/settings/*`.

PUT мутирует глобальный кэш политики, поэтому каждый тест восстанавливает
исходную политику после себя — иначе мутация протекла бы в другие тесты.
"""

from __future__ import annotations

import pytest

from src.core import password_policy
from tests._helpers import assert_error, auth_hdr as _hdr

BASE = "/api/server/v1/admin/password-policy"


@pytest.fixture(autouse=True)
def _restore_policy():
    saved = password_policy.current_policy()
    yield
    password_policy.apply_policy(saved)


# ── GET ───────────────────────────────────────────────────────────────────────

class TestGet:
    async def test_seeded_defaults(self, client, account_admin_token):
        resp = await client.get(BASE, headers=_hdr(account_admin_token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["min_length"] == 8
        assert body["require_letter"] is True
        assert body["require_digit"] is True

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
            BASE, headers=_hdr(admin_token), json={"min_length": 12},
        )
        assert_error(resp, 403, "ACCOUNT_ADMIN_REQUIRED")


# ── PUT ─────────────────────────────────────────────────────────────────────

class TestPut:
    async def test_put_changes_and_persists(self, client, account_admin_token):
        resp = await client.put(
            BASE,
            headers=_hdr(account_admin_token),
            json={"min_length": 12, "require_letter": True, "require_digit": False},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["min_length"] == 12
        assert body["require_digit"] is False
        assert body["updated_by"]  # actor-id заполнен

        again = await client.get(BASE, headers=_hdr(account_admin_token))
        assert again.json()["min_length"] == 12
        assert again.json()["require_digit"] is False

    async def test_put_partial_keeps_other_fields(self, client, account_admin_token):
        await client.put(
            BASE,
            headers=_hdr(account_admin_token),
            json={"min_length": 16, "require_digit": False},
        )
        # Второй PUT трогает только require_letter — остальное сохраняется.
        resp = await client.put(
            BASE, headers=_hdr(account_admin_token), json={"require_letter": False},
        )
        body = resp.json()
        assert body["min_length"] == 16
        assert body["require_digit"] is False
        assert body["require_letter"] is False

    async def test_put_updates_process_cache(self, client, account_admin_token):
        # До PUT дефолт (8) отвергает 5-символьный пароль.
        assert password_policy.is_compliant("ab123") is False
        await client.put(
            BASE,
            headers=_hdr(account_admin_token),
            json={"min_length": 4, "require_letter": True, "require_digit": True},
        )
        # После PUT процессный кэш ослаблен — тот же пароль проходит.
        assert password_policy.is_compliant("ab123") is True


# ── Validation ──────────────────────────────────────────────────────────────

class TestValidation:
    async def test_min_length_below_floor_rejected(self, client, account_admin_token):
        resp = await client.put(
            BASE, headers=_hdr(account_admin_token), json={"min_length": 0},
        )
        assert resp.status_code == 422

    async def test_min_length_above_ceiling_rejected(self, client, account_admin_token):
        resp = await client.put(
            BASE, headers=_hdr(account_admin_token), json={"min_length": 129},
        )
        assert resp.status_code == 422

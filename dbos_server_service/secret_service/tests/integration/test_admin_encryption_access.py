"""Admin-эндпоинты ротации мастер-ключа secret_service для account_admin.

`/api/secret/v1/admin/encryption/*` — инфраструктурные ручки, доступные
платформенному `account_admin`'у. Ключи шифрования не бизнес-данные; ручки
отдают только статус ротации и версии ключа.

Покрытие:

* `account_admin` может rotate / retire / читать migration_status;
* остальные роли (department_admin, operator, reader) — 403 ACCOUNT_ADMIN_REQUIRED;
* rotate / retire эмитят CRITICAL-audit с actor = account_admin.

Через реальный PostgreSQL + живой file-keystore из conftest.
mock_auth_service / mock_logging_service — autouse фикстуры conftest'а.
"""

from __future__ import annotations

import base64
import os

import pytest

from src.core.keystore import get_keystore

from tests.integration.conftest import auth_header

BASE = "/api/secret/v1"
ENC = f"{BASE}/admin/encryption"


def _fresh_key_b64() -> str:
    return base64.b64encode(os.urandom(32)).decode("ascii")


# ── account_admin: happy path ────────────────────────────────────────────────


class TestAccountAdminAllowed:
    async def test_migration_status_readable(self, client, identity_factory):
        token = identity_factory(platform_role="account_admin", allowed_services=[])
        resp = await client.get(f"{ENC}/migration_status", headers=auth_header(token))
        assert resp.status_code == 200
        body = resp.json()
        for key in ("active_version", "total_rows", "by_version", "remaining_legacy", "migrated_pct"):
            assert key in body

    async def test_rotate_bumps_version(self, client, identity_factory):
        token = identity_factory(platform_role="account_admin", allowed_services=[])
        before = get_keystore().get_active_version()
        resp = await client.post(
            f"{ENC}/rotate",
            headers=auth_header(token),
            json={"new_key_b64": _fresh_key_b64()},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["idempotent"] is False
        assert body["new_version"] == before + 1
        assert body["previous_version"] == before
        assert get_keystore().get_active_version() == before + 1

    async def test_rotate_rejects_bad_key(self, client, identity_factory):
        token = identity_factory(platform_role="account_admin", allowed_services=[])
        resp = await client.post(
            f"{ENC}/rotate",
            headers=auth_header(token),
            json={"new_key_b64": "not-base64!!!"},
        )
        assert resp.status_code == 400
        assert resp.json()["error_code"] == "ROTATE_KEY_INVALID"

    async def test_retire_active_rejected(self, client, identity_factory):
        token = identity_factory(platform_role="account_admin", allowed_services=[])
        active = get_keystore().get_active_version()
        resp = await client.post(f"{ENC}/retire/{active}", headers=auth_header(token))
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "KEYSTORE_CANNOT_RETIRE_ACTIVE"

    async def test_rotate_then_retire_old_version(self, client, identity_factory):
        token = identity_factory(platform_role="account_admin", allowed_services=[])
        old = get_keystore().get_active_version()
        rot = await client.post(
            f"{ENC}/rotate",
            headers=auth_header(token),
            json={"new_key_b64": _fresh_key_b64()},
        )
        assert rot.status_code == 200
        resp = await client.post(f"{ENC}/retire/{old}", headers=auth_header(token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["version"] == old
        assert body["retired"] is True
        assert old not in get_keystore().list_versions()


# ── Audit: CRITICAL на rotate/retire ─────────────────────────────────────────


class TestAuditEmitted:
    async def test_rotate_emits_admin_rotate(
        self, client, identity_factory, mock_logging_service
    ):
        token = identity_factory(
            user_id="usr_acct_admin",
            platform_role="account_admin",
            allowed_services=[],
        )
        resp = await client.post(
            f"{ENC}/rotate",
            headers=auth_header(token),
            json={"new_key_b64": _fresh_key_b64()},
        )
        assert resp.status_code == 200
        events = mock_logging_service.by_action("secrets.admin_encryption_rotate")
        assert len(events) == 1
        ev = events[0]
        assert ev["status"] == "success"
        assert ev["actor_id"] == "usr_acct_admin"
        assert ev["details"]["idempotent"] is False

    async def test_retire_emits_admin_retire(
        self, client, identity_factory, mock_logging_service
    ):
        token = identity_factory(platform_role="account_admin", allowed_services=[])
        old = get_keystore().get_active_version()
        await client.post(
            f"{ENC}/rotate",
            headers=auth_header(token),
            json={"new_key_b64": _fresh_key_b64()},
        )
        resp = await client.post(f"{ENC}/retire/{old}", headers=auth_header(token))
        assert resp.status_code == 200
        events = mock_logging_service.by_action("secrets.admin_encryption_retire")
        assert len(events) == 1
        assert events[0]["details"]["version"] == old


# ── Прочие роли: 403 ──────────────────────────────────────────────────────────


class TestNonAccountAdminForbidden:
    async def test_department_admin_forbidden_on_rotate(self, client, identity_factory):
        token = identity_factory(
            platform_role="department_admin",
            department_id="dep_a",
            service_roles={"secret_service": ["admin"]},
        )
        resp = await client.post(
            f"{ENC}/rotate",
            headers=auth_header(token),
            json={"new_key_b64": _fresh_key_b64()},
        )
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "ACCOUNT_ADMIN_REQUIRED"

    async def test_operator_forbidden_on_status(self, client, identity_factory):
        token = identity_factory(service_roles={"secret_service": ["operator"]})
        resp = await client.get(f"{ENC}/migration_status", headers=auth_header(token))
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "ACCOUNT_ADMIN_REQUIRED"

    async def test_reader_forbidden_on_retire(self, client, identity_factory):
        token = identity_factory(service_roles={"secret_service": ["reader"]})
        resp = await client.post(f"{ENC}/retire/1", headers=auth_header(token))
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "ACCOUNT_ADMIN_REQUIRED"

    async def test_anonymous_gets_401(self, client):
        resp = await client.get(f"{ENC}/migration_status")
        assert resp.status_code == 401

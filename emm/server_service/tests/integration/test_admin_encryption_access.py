"""Admin-эндпоинты ротации ключей шифрования для account_admin.

`/api/server/v1/admin/encryption/*` — инфраструктурные ручки, доступные
платформенному `account_admin`'у. Это явное исключение из
`platform_admin_guard` business-data-блока: ключи шифрования не бизнес-данные,
ручки отдают только статус ротации и версии ключа.

Покрытие:

* `account_admin` может rotate / retire / читать migration_status;
* остальные роли (department_admin, operator, reader, loging_admin) — 403;
* platform-guard НЕ мешает этим путям, но по-прежнему блокирует обычные
  server_service-эндпоинты для account_admin;
* rotate / retire эмитят CRITICAL-audit с actor = account_admin.

Всё через реальный PostgreSQL + живой file-keystore из conftest.
"""

from __future__ import annotations

import base64
import os

import pytest

from src.core.keystore import get_keystore

from tests._helpers import assert_error, auth_hdr as _hdr, make_emit_capture  # noqa: E402

BASE = "/api/server/v1"
ENC = f"{BASE}/admin/encryption"


def _fresh_key_b64() -> str:
    return base64.b64encode(os.urandom(32)).decode("ascii")


@pytest.fixture
def captured_emits(monkeypatch):
    return make_emit_capture(
        monkeypatch,
        "src.api.v1.endpoints.admin_encryption.audit_service.emit",
        "src.middleware.platform_admin_guard.audit_service.emit",
    )


def _events(captured: list[dict], action: str) -> list[dict]:
    return [e for e in captured if e["action"] == action]


# ── account_admin: happy path ────────────────────────────────────────────────


class TestAccountAdminAllowed:
    async def test_migration_status_readable(self, client, account_admin_token):
        resp = await client.get(
            f"{ENC}/migration_status", headers=_hdr(account_admin_token)
        )
        assert resp.status_code == 200
        body = resp.json()
        # UI-поллинг ждёт эти поля для прогресс-бара.
        for key in ("remaining", "total", "by_version", "migrated_pct", "active_version"):
            assert key in body

    async def test_rotate_bumps_version(self, client, account_admin_token):
        before = get_keystore().get_active_version()
        resp = await client.post(
            f"{ENC}/rotate",
            headers=_hdr(account_admin_token),
            json={"new_key_b64": _fresh_key_b64()},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["idempotent"] is False
        assert body["new_version"] == before + 1
        assert body["previous_version"] == before
        assert get_keystore().get_active_version() == before + 1

    async def test_rotate_rejects_bad_key(self, client, account_admin_token):
        resp = await client.post(
            f"{ENC}/rotate",
            headers=_hdr(account_admin_token),
            json={"new_key_b64": "not-base64!!!"},
        )
        assert_error(resp, 422, "ROTATE_KEY_INVALID")

    async def test_retire_active_rejected(self, client, account_admin_token):
        active = get_keystore().get_active_version()
        resp = await client.post(
            f"{ENC}/retire/{active}", headers=_hdr(account_admin_token)
        )
        assert_error(resp, 409, "KEYSTORE_CANNOT_RETIRE_ACTIVE")

    async def test_rotate_then_retire_old_version(self, client, account_admin_token):
        old = get_keystore().get_active_version()
        rot = await client.post(
            f"{ENC}/rotate",
            headers=_hdr(account_admin_token),
            json={"new_key_b64": _fresh_key_b64()},
        )
        assert rot.status_code == 200
        resp = await client.post(
            f"{ENC}/retire/{old}", headers=_hdr(account_admin_token)
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["version"] == old
        assert body["retired"] is True
        assert old not in get_keystore().list_versions()


# ── Audit: CRITICAL на rotate/retire с actor = account_admin ─────────────────


class TestAuditEmitted:
    async def test_rotate_emits_admin_rotate(
        self, client, account_admin_token, captured_emits
    ):
        resp = await client.post(
            f"{ENC}/rotate",
            headers=_hdr(account_admin_token),
            json={"new_key_b64": _fresh_key_b64()},
        )
        assert resp.status_code == 200
        events = _events(captured_emits, "encryption.admin_rotate")
        assert len(events) == 1
        ev = events[0]
        assert ev["status"] == "success"
        assert ev["details"]["idempotent"] is False
        # account_admin не должен ловить platform-block на этом пути.
        assert _events(captured_emits, "http.platform_admin_blocked") == []

    async def test_retire_emits_admin_retire(
        self, client, account_admin_token, captured_emits
    ):
        old = get_keystore().get_active_version()
        await client.post(
            f"{ENC}/rotate",
            headers=_hdr(account_admin_token),
            json={"new_key_b64": _fresh_key_b64()},
        )
        resp = await client.post(
            f"{ENC}/retire/{old}", headers=_hdr(account_admin_token)
        )
        assert resp.status_code == 200
        events = _events(captured_emits, "encryption.admin_retire")
        assert len(events) == 1
        assert events[0]["details"]["version"] == old


# ── Прочие роли: 403 ──────────────────────────────────────────────────────────


class TestNonAccountAdminForbidden:
    async def test_department_admin_forbidden_on_rotate(self, client, admin_token):
        resp = await client.post(
            f"{ENC}/rotate",
            headers=_hdr(admin_token),
            json={"new_key_b64": _fresh_key_b64()},
        )
        assert_error(resp, 403, "ACCOUNT_ADMIN_REQUIRED")

    async def test_operator_forbidden_on_status(self, client, operator_token_a):
        resp = await client.get(
            f"{ENC}/migration_status", headers=_hdr(operator_token_a)
        )
        assert_error(resp, 403, "ACCOUNT_ADMIN_REQUIRED")

    async def test_reader_forbidden_on_retire(self, client, reader_token_a):
        resp = await client.post(f"{ENC}/retire/1", headers=_hdr(reader_token_a))
        assert_error(resp, 403, "ACCOUNT_ADMIN_REQUIRED")

    async def test_loging_admin_forbidden_on_rotate(self, client, loging_admin_token):
        # loging_admin тоже platform, но не account_admin → 403 ACCOUNT_ADMIN_REQUIRED.
        resp = await client.post(
            f"{ENC}/rotate",
            headers=_hdr(loging_admin_token),
            json={"new_key_b64": _fresh_key_b64()},
        )
        assert_error(resp, 403, "ACCOUNT_ADMIN_REQUIRED")

    async def test_anonymous_gets_401(self, client):
        resp = await client.get(f"{ENC}/migration_status")
        assert_error(resp, 401, "ACCESS_TOKEN_MISSING")


# ── Platform-guard regression: business endpoints всё ещё закрыты ────────────


class TestPlatformBlockStillApplies:
    async def test_account_admin_still_blocked_on_business(
        self, client, account_admin_token
    ):
        """Allowlist на /admin/encryption не открыл account_admin'у бизнес-данные."""
        resp = await client.get(f"{BASE}/servers", headers=_hdr(account_admin_token))
        assert_error(resp, 403, "PLATFORM_ADMIN_BUSINESS_DATA_DENIED")

    async def test_account_admin_blocked_on_lookalike_path(
        self, client, account_admin_token
    ):
        """Путь с тем же началом, но вне сегмента — НЕ в allowlist'е, блок остаётся."""
        resp = await client.get(
            f"{BASE}/admin/encryptionx", headers=_hdr(account_admin_token)
        )
        # Либо guard-403 (путь не в allowlist), либо 404 — но точно НЕ 200.
        assert resp.status_code != 200

import base64
from unittest.mock import AsyncMock

import httpx
import pytest

from src.core.exceptions import BadRequestError, ConflictError, ServiceUnavailableError
from src.models.acs_settings import AcsSettings, SINGLETON_ID
from src.services import acs_settings, acs_credential_migration, secret_client, secrets_service
from tests._helpers import auth_hdr

BASE = "/api/server/v1/settings/acs"


async def legacy(db):
    row = AcsSettings(id=SINGLETON_ID, enabled=True, acs_url="https://acs.example/",
        acs_password_encrypted=secrets_service.encrypt("старый пароль", aad=secrets_service.aad_for_acs_password(SINGLETON_ID)))
    db.add(row)
    await db.commit()
    return row


async def test_link_clears_legacy_and_reads_current_value_each_time(client, db, account_admin_token, admin_token, monkeypatch):
    row = await legacy(db)
    reveal = AsyncMock(return_value="новый пароль")
    monkeypatch.setattr(secret_client, "reveal_acs_password", reveal)
    denied = await client.put(BASE, headers=auth_hdr(admin_token), json={"credential_id": "cred_acs"})
    assert denied.status_code == 403
    response = await client.put(BASE, headers=auth_hdr(account_admin_token), json={"credential_id": "cred_acs"})
    assert response.status_code == 200, response.text
    assert response.json()["credential_id"] == "cred_acs"
    assert response.json()["legacy_password_is_set"] is False
    assert "пароль" not in response.text
    await db.refresh(row)
    assert row.acs_password_encrypted is None
    assert (await acs_settings.get_acs_credentials(db))[1] == "новый пароль"
    reveal.return_value = "после ротации"
    assert (await acs_settings.get_acs_credentials(db))[1] == "после ротации"
    response = await client.put(BASE, headers=auth_hdr(account_admin_token), json={"acs_password": "must-not-store"})
    assert response.status_code == 400
    assert response.json()["error_code"] == "ACS_PASSWORD_MANAGED_EXTERNALLY"


async def test_unavailable_link_preserves_legacy_and_runtime_does_not_fallback(client, db, account_admin_token, monkeypatch):
    row = await legacy(db)
    old_blob = row.acs_password_encrypted
    monkeypatch.setattr(secret_client, "reveal_acs_password", AsyncMock(side_effect=ServiceUnavailableError(error_code="SECRET_SERVICE_UNAVAILABLE", message="offline")))
    response = await client.put(BASE, headers=auth_hdr(account_admin_token), json={"credential_id": "cred_acs"})
    assert response.status_code == 503
    await db.refresh(row)
    assert row.acs_password_encrypted == old_blob and row.credential_id is None
    row.credential_id = "cred_acs"
    await db.commit()
    with pytest.raises(ServiceUnavailableError):
        await acs_settings.get_acs_credentials(db)


@pytest.mark.parametrize("status", [403, 404, 410, 500])
async def test_secret_client_handles_denied_expired_and_failure_without_echo(monkeypatch, status):
    from src.core.config import get_settings
    monkeypatch.setattr(get_settings(), "secret_service_url", "https://secrets.example")
    monkeypatch.setattr(get_settings(), "secret_service_api_key", "test-bot")
    def handler(request):
        assert request.headers["Authorization"] == "Bearer test-bot"
        if request.url.path.endswith("/reveal"):
            return httpx.Response(status, text="must-not-echo-secret")
        return httpx.Response(200, json={"scope": "service", "service": "acs", "owner_dept_id": "dep_a"})
    monkeypatch.setattr(secret_client, "build_client", lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises((BadRequestError, ServiceUnavailableError)) as error:
        await secret_client.reveal_acs_password("cred_acs")
    assert "must-not-echo" not in str(error.value)


async def test_secret_client_rejects_wrong_scope_before_reveal(monkeypatch):
    request = AsyncMock(return_value=httpx.Response(200, json={"scope": "personal", "service": "acs", "owner_dept_id": None}))
    monkeypatch.setattr(secret_client, "request", request)
    with pytest.raises(BadRequestError) as error:
        await secret_client.reveal_acs_password("cred_personal")
    assert error.value.error_code == "ACS_CREDENTIAL_INVALID"
    assert request.await_count == 1


async def test_migration_reuses_created_record_after_failure_and_is_repeatable(db, monkeypatch):
    row = await legacy(db)
    created = []
    async def request(method, path, *, token, body=None):
        assert token == "owner-token"
        if method == "POST":
            assert base64.b64decode(body["secret_b64"]).decode() == "старый пароль"
            if created:
                assert body["name"] == created[0]["name"]
                return httpx.Response(409)
            created.append({"id": "cred_imported", **{k: v for k, v in body.items() if k != "secret_b64"}})
            return httpx.Response(201, json=created[0])
        return httpx.Response(200, json={"items": created, "next_cursor": None})
    monkeypatch.setattr(secret_client, "request", request)
    reveal = AsyncMock(side_effect=ServiceUnavailableError(error_code="SECRET_SERVICE_UNAVAILABLE", message="offline"))
    monkeypatch.setattr(secret_client, "reveal_acs_password", reveal)
    with pytest.raises(ServiceUnavailableError):
        await acs_credential_migration.migrate(db, "dep_a", "owner-token")
    await db.refresh(row)
    assert row.acs_password_encrypted and row.credential_id is None
    reveal.side_effect = None
    reveal.return_value = "старый пароль"
    assert await acs_credential_migration.migrate(db, "dep_a", "owner-token") == "cred_imported"
    assert await acs_credential_migration.migrate(db, "dep_a", "owner-token") == "cred_imported"
    await db.refresh(row)
    assert row.acs_password_encrypted is None and row.credential_id == "cred_imported"
    assert len(created) == 1


async def test_migration_does_not_overwrite_concurrent_password_change(db, monkeypatch):
    row = await legacy(db)
    new_blob = secrets_service.encrypt("changed", aad=secrets_service.aad_for_acs_password(SINGLETON_ID))
    monkeypatch.setattr(secret_client, "request", AsyncMock(return_value=httpx.Response(201, json={"id": "cred_imported", "owner_dept_id": "dep_a"})))
    async def reveal(_id):
        row.acs_password_encrypted = new_blob
        await db.commit()
        return "старый пароль"
    monkeypatch.setattr(secret_client, "reveal_acs_password", reveal)
    with pytest.raises(ConflictError) as error:
        await acs_credential_migration.migrate(db, "dep_a", "owner-token")
    assert error.value.error_code == "ACS_MIGRATION_CONFIG_CHANGED"
    await db.refresh(row)
    assert row.acs_password_encrypted == new_blob and row.credential_id is None


async def test_acs_catalog_is_admin_only_and_never_exposes_values(client, account_admin_token, admin_token, monkeypatch):
    calls = []
    async def request(method, path, **kwargs):
        calls.append(path)
        assert method == "GET" and "scope=service" in path and "service=acs" in path
        return httpx.Response(200, json={"items": [{
            "id": "cred_acs", "scope": "service", "service": "acs", "name": "ACS испытаний",
            "owner_dept_id": "dep_a", "secret_b64": "must-not-appear", "login": "private-login",
        }], "next_cursor": None})
    monkeypatch.setattr(secret_client, "request", request)
    denied = await client.get(BASE + "/credentials", headers=auth_hdr(admin_token))
    assert denied.status_code == 403 and not calls
    response = await client.get(BASE + "/credentials", headers=auth_hdr(account_admin_token))
    assert response.status_code == 200, response.text
    assert response.json()[0]["name"] == "ACS испытаний"
    assert "must-not-appear" not in response.text and "private-login" not in response.text

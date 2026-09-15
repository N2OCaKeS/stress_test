import base64
from unittest.mock import AsyncMock

import httpx
import pytest

from src.core.exceptions import BadRequestError, ConflictError, ServiceUnavailableError
from src.models.host_services_settings import HostServicesSettings
from src.services import host_services_settings, host_ssh_credential_migration, secret_client, secrets_service
from tests._helpers import auth_hdr

BASE = "/api/server/v1/settings/host-services"
DEPT = "dep_a"


async def legacy(db, department_id: str = DEPT):
    row = HostServicesSettings(
        department_id=department_id, ssh_host="10.0.0.1", ssh_port=22, ssh_user="emm-host-control",
        ssh_private_key_encrypted=secrets_service.encrypt(
            "старый ключ", aad=secrets_service.aad_for_host_control_ssh_key(department_id)
        ),
    )
    db.add(row)
    await db.commit()
    return row


async def test_link_clears_legacy_and_reads_current_value_each_time(client, db, admin_token, reader_token_a, monkeypatch):
    row = await legacy(db)
    reveal = AsyncMock(return_value="новый ключ")
    monkeypatch.setattr(secret_client, "reveal_host_ssh_key", reveal)
    denied = await client.put(BASE, headers=auth_hdr(reader_token_a), json={"credential_id": "cred_host"})
    assert denied.status_code == 403
    response = await client.put(BASE, headers=auth_hdr(admin_token), json={"credential_id": "cred_host"})
    assert response.status_code == 200, response.text
    assert response.json()["credential_id"] == "cred_host"
    assert response.json()["legacy_private_key_is_set"] is False
    assert "ключ" not in response.text
    await db.refresh(row)
    assert row.ssh_private_key_encrypted is None
    assert await host_services_settings.get_decrypted_private_key(db, DEPT) == "новый ключ"
    reveal.return_value = "после ротации"
    assert await host_services_settings.get_decrypted_private_key(db, DEPT) == "после ротации"
    response = await client.put(BASE, headers=auth_hdr(admin_token), json={"ssh_private_key": "must-not-store"})
    assert response.status_code == 400
    assert response.json()["error_code"] == "HOST_SSH_KEY_MANAGED_EXTERNALLY"


async def test_unavailable_link_preserves_legacy_and_runtime_does_not_fallback(client, db, admin_token, monkeypatch):
    row = await legacy(db)
    old_blob = row.ssh_private_key_encrypted
    monkeypatch.setattr(secret_client, "reveal_host_ssh_key", AsyncMock(side_effect=ServiceUnavailableError(error_code="SECRET_SERVICE_UNAVAILABLE", message="offline")))
    response = await client.put(BASE, headers=auth_hdr(admin_token), json={"credential_id": "cred_host"})
    assert response.status_code == 503
    await db.refresh(row)
    assert row.ssh_private_key_encrypted == old_blob and row.credential_id is None
    row.credential_id = "cred_host"
    await db.commit()
    with pytest.raises(ServiceUnavailableError):
        await host_services_settings.get_decrypted_private_key(db, DEPT)


@pytest.mark.parametrize("status", [403, 404, 410, 500])
async def test_secret_client_handles_denied_expired_and_failure_without_echo(monkeypatch, status):
    from src.core.config import get_settings
    monkeypatch.setattr(get_settings(), "secret_service_url", "https://secrets.example")
    monkeypatch.setattr(get_settings(), "secret_service_api_key", "test-bot")

    def handler(request):
        assert request.headers["Authorization"] == "Bearer test-bot"
        if request.url.path.endswith("/reveal"):
            return httpx.Response(status, text="must-not-echo-secret")
        return httpx.Response(200, json={"scope": "service", "service": "host_ssh", "owner_dept_id": DEPT})

    monkeypatch.setattr(secret_client, "build_client", lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises((BadRequestError, ServiceUnavailableError)) as error:
        await secret_client.reveal_host_ssh_key("cred_host", DEPT)
    assert "must-not-echo" not in str(error.value)


async def test_secret_client_rejects_wrong_scope_before_reveal(monkeypatch):
    request = AsyncMock(return_value=httpx.Response(200, json={"scope": "personal", "service": "host_ssh", "owner_dept_id": None}))
    monkeypatch.setattr(secret_client, "request", request)
    with pytest.raises(BadRequestError) as error:
        await secret_client.reveal_host_ssh_key("cred_personal", DEPT)
    assert error.value.error_code == "HOST_SSH_CREDENTIAL_INVALID"
    assert request.await_count == 1


async def test_secret_client_rejects_credential_owned_by_other_department(monkeypatch):
    request = AsyncMock(return_value=httpx.Response(200, json={"scope": "service", "service": "host_ssh", "owner_dept_id": "dep_b"}))
    monkeypatch.setattr(secret_client, "request", request)
    with pytest.raises(BadRequestError) as error:
        await secret_client.reveal_host_ssh_key("cred_dep_b", DEPT)
    assert error.value.error_code == "HOST_SSH_CREDENTIAL_INVALID"


async def test_migration_reuses_created_record_after_failure_and_is_repeatable(db, monkeypatch):
    row = await legacy(db)
    created = []

    async def request(method, path, *, token, body=None):
        assert token == "owner-token"
        if method == "POST":
            assert base64.b64decode(body["secret_b64"]).decode() == "старый ключ"
            if created:
                assert body["name"] == created[0]["name"]
                return httpx.Response(409)
            created.append({"id": "cred_imported", **{k: v for k, v in body.items() if k != "secret_b64"}})
            return httpx.Response(201, json=created[0])
        return httpx.Response(200, json={"items": created, "next_cursor": None})

    monkeypatch.setattr(secret_client, "request", request)
    reveal = AsyncMock(side_effect=ServiceUnavailableError(error_code="SECRET_SERVICE_UNAVAILABLE", message="offline"))
    monkeypatch.setattr(secret_client, "reveal_host_ssh_key", reveal)
    with pytest.raises(ServiceUnavailableError):
        await host_ssh_credential_migration.migrate(db, DEPT, "owner-token")
    await db.refresh(row)
    assert row.ssh_private_key_encrypted and row.credential_id is None
    reveal.side_effect = None
    reveal.return_value = "старый ключ"
    assert await host_ssh_credential_migration.migrate(db, DEPT, "owner-token") == "cred_imported"
    assert await host_ssh_credential_migration.migrate(db, DEPT, "owner-token") == "cred_imported"
    await db.refresh(row)
    assert row.ssh_private_key_encrypted is None and row.credential_id == "cred_imported"
    assert len(created) == 1


async def test_migration_does_not_overwrite_concurrent_key_change(db, monkeypatch):
    row = await legacy(db)
    new_blob = secrets_service.encrypt("changed", aad=secrets_service.aad_for_host_control_ssh_key(DEPT))
    monkeypatch.setattr(secret_client, "request", AsyncMock(return_value=httpx.Response(201, json={"id": "cred_imported", "owner_dept_id": DEPT})))

    async def reveal(_id, _dept):
        row.ssh_private_key_encrypted = new_blob
        await db.commit()
        return "старый ключ"

    monkeypatch.setattr(secret_client, "reveal_host_ssh_key", reveal)
    with pytest.raises(ConflictError) as error:
        await host_ssh_credential_migration.migrate(db, DEPT, "owner-token")
    assert error.value.error_code == "HOST_SSH_MIGRATION_CONFIG_CHANGED"
    await db.refresh(row)
    assert row.ssh_private_key_encrypted == new_blob and row.credential_id is None


async def test_migration_missing_legacy_key_returns_error(db):
    with pytest.raises(BadRequestError) as error:
        await host_ssh_credential_migration.migrate(db, DEPT, "owner-token")
    assert error.value.error_code == "HOST_SSH_LEGACY_KEY_MISSING"


async def test_credential_catalog_is_scoped_to_own_department(client, admin_token, admin_token_b, reader_token_a, monkeypatch):
    calls = []

    async def request(method, path, **kwargs):
        calls.append(path)
        assert method == "GET" and "scope=service" in path and "service=host_ssh" in path
        return httpx.Response(200, json={"items": [
            {"id": "cred_a", "scope": "service", "service": "host_ssh", "name": "Ключ A",
             "owner_dept_id": "dep_a", "secret_b64": "must-not-appear", "login": "private-login"},
            {"id": "cred_b", "scope": "service", "service": "host_ssh", "name": "Ключ B",
             "owner_dept_id": "dep_b", "secret_b64": "must-not-appear"},
        ], "next_cursor": None})

    monkeypatch.setattr(secret_client, "request", request)
    denied = await client.get(BASE + "/credentials", headers=auth_hdr(reader_token_a))
    assert denied.status_code == 403 and not calls
    response = await client.get(BASE + "/credentials", headers=auth_hdr(admin_token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["id"] for item in body] == ["cred_a"]
    assert "must-not-appear" not in response.text and "private-login" not in response.text

    response_b = await client.get(BASE + "/credentials", headers=auth_hdr(admin_token_b))
    assert [item["id"] for item in response_b.json()] == ["cred_b"]

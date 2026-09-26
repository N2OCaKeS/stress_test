"""`prepare-for-test` с тестовой учёткой отдела.

С `test_account_credential_id` пайплайн:

* проверяет ссылку до restore (сервисная запись `test_account` отдела
  сервера), чужая — терминальный `failed_step=user_provision` без restore;
* на шаге `user_provision` раскрывает credential и кладёт воркеру его логин,
  пароль и публичный ключ (не случайные), своей копии не держит;
* callback учётных данных не несёт.

Без поля — прежнее поведение (случайные пароль и ключ).
secret_service подменяется на уровне `secret_client.request`.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest
from sqlalchemy import select

from src.models.server_prepare_for_test import (
    PREPARE_FOR_TEST_FAILED,
    PREPARE_FOR_TEST_IN_PROGRESS,
    PREPARE_FOR_TEST_SUCCEEDED,
    STEP_RESTORE,
    STEP_USER_PROVISION,
    ServerPrepareForTestRequest,
    ServerTestCredentials,
)
from src.services import prepare_for_test as pft_svc
from src.services import secret_client, worker_client
from src.utils.ids import prepare_for_test_request_id
from tests.test_prepare_for_test import (
    BASE,
    _body,
    _make_os_version,
    _set_bootstrap_password,
    _svc_hdr,
    captured_dispatch as captured_dispatch,
    configure_service_keys as configure_service_keys,
    fake_acs as fake_acs,
)

CRED_ID = "cred_test_account_a"
ACCOUNT_SECRET = {
    "v": 1,
    "password": "Shared-Srv-Pass1",
    "private_key": "-----BEGIN OPENSSH PRIVATE KEY-----\nacct\n-----END OPENSSH PRIVATE KEY-----\n",
    "public_key": "ssh-ed25519 AAAAaccount test-account",
}


@pytest.fixture
def fake_secret(monkeypatch):
    """`secret_client.request` поверх словаря credential'ов: GET метаданных и reveal."""
    state = {
        "creds": {
            CRED_ID: {
                "scope": "service", "service": "test_account", "owner_dept_id": "dep_a",
                "login": "u", "secret": json.dumps(ACCOUNT_SECRET),
            },
        },
        "calls": [],
    }

    async def fake_request(method, path, *, token=None, body=None):
        state["calls"].append((method, path))
        cred_id = path.split("/")[2]
        cred = state["creds"].get(cred_id)
        if cred is None:
            return httpx.Response(404, json={})
        if method == "POST" and path.endswith("/reveal"):
            return httpx.Response(200, json={
                "login": cred["login"],
                "secret_b64": base64.b64encode(cred["secret"].encode()).decode(),
            })
        return httpx.Response(200, json={"id": cred_id, **{k: v for k, v in cred.items() if k != "secret"}})

    monkeypatch.setattr(secret_client, "request", fake_request)
    return state


@pytest.fixture
def captured_callbacks(monkeypatch):
    captured: list[dict] = []

    async def fake_send(request_id, body):
        captured.append(body)
        return True, 1, None

    monkeypatch.setattr("src.services.testing_client.send_prepare_for_test_completed", fake_send)
    return captured


@pytest.fixture
def captured_stash(monkeypatch):
    stored: dict[str, dict] = {}

    async def fake_store(key, creds):
        stored[key] = creds

    async def fake_delete(key):
        stored.pop(key, None)

    monkeypatch.setattr(worker_client, "store_dispatch_creds", fake_store)
    monkeypatch.setattr(worker_client, "delete_dispatch_creds", fake_delete)
    return stored


async def _request_row(db, srv, *, credential_id: str | None) -> ServerPrepareForTestRequest:
    req = ServerPrepareForTestRequest(
        id=prepare_for_test_request_id(),
        server_id=srv.id,
        correlation_id=f"qi_{srv.id}",
        os_version_id="osv_x",
        kernel="5.10.0",
        mode="orel",
        test_username="ignored",
        test_account_credential_id=credential_id,
        requested_by_service="testing_service",
        status=PREPARE_FOR_TEST_IN_PROGRESS,
        stage=STEP_RESTORE,
    )
    db.add(req)
    await db.flush()
    return req


class TestStart:
    async def test_credential_reference_stored_and_validated(
        self, client, make_server, db, configure_service_keys, captured_dispatch, fake_secret,
    ):
        srv = await make_server(department_id="dep_a")
        osv = await _make_os_version(db, name="1.8-ta", kernels=["5.10.0"])
        await _set_bootstrap_password(db, osv.id)

        resp = await client.post(
            f"{BASE}/{srv.id}/prepare-for-test", headers=_svc_hdr(),
            json=_body(os_version_id=osv.id, test_account_credential_id=CRED_ID, correlation_id="qi_ta"),
        )
        assert resp.status_code == 202, resp.text
        assert resp.json()["status"] == "in_progress"
        assert [c["task_kind"] for c in captured_dispatch] == ["acs.snapshot_restore"]
        # До restore — только метаданные, без reveal.
        assert fake_secret["calls"] == [("GET", f"/credentials/{CRED_ID}")]

        status = await client.get(
            f"{BASE}/{srv.id}/prepare-for-test/{resp.json()['prepare_request_id']}", headers=_svc_hdr(),
        )
        assert status.status_code == 200, status.text
        assert status.json()["test_account_credential_id"] == CRED_ID

    async def test_foreign_department_credential_fails_before_restore(
        self, client, make_server, db, configure_service_keys, captured_dispatch, fake_secret,
        captured_callbacks,
    ):
        fake_secret["creds"][CRED_ID]["owner_dept_id"] = "dep_b"
        srv = await make_server(department_id="dep_a")
        osv = await _make_os_version(db, name="1.8-tb", kernels=["5.10.0"])
        await _set_bootstrap_password(db, osv.id)

        resp = await client.post(
            f"{BASE}/{srv.id}/prepare-for-test", headers=_svc_hdr(),
            json=_body(os_version_id=osv.id, test_account_credential_id=CRED_ID, correlation_id="qi_tb"),
        )
        assert resp.status_code == 202, resp.text
        assert resp.json()["status"] == "failed"
        assert captured_dispatch == []
        row = (await db.execute(select(ServerPrepareForTestRequest).where(
            ServerPrepareForTestRequest.id == resp.json()["prepare_request_id"],
        ))).scalar_one()
        assert row.failed_step == STEP_USER_PROVISION
        assert "TEST_ACCOUNT_CREDENTIAL_INVALID" in row.error
        assert captured_callbacks[-1]["succeeded"] is False

    async def test_without_field_no_secret_service_call(
        self, client, make_server, db, configure_service_keys, captured_dispatch, fake_secret,
    ):
        srv = await make_server(department_id="dep_a")
        osv = await _make_os_version(db, name="1.8-tc", kernels=["5.10.0"])
        await _set_bootstrap_password(db, osv.id)
        resp = await client.post(
            f"{BASE}/{srv.id}/prepare-for-test", headers=_svc_hdr(),
            json=_body(os_version_id=osv.id, correlation_id="qi_tc"),
        )
        assert resp.status_code == 202, resp.text
        assert fake_secret["calls"] == []


class TestUserProvision:
    async def test_stash_carries_credential_values(
        self, make_server, db, captured_dispatch, fake_secret, captured_stash,
    ):
        srv = await make_server(department_id="dep_a")
        await _request_row(db, srv, credential_id=CRED_ID)
        # Строка от прошлой подготовки со случайной учёткой.
        from src.services import secrets_service
        from src.utils.ids import server_test_credentials_id

        stale_id = server_test_credentials_id()
        db.add(ServerTestCredentials(
            id=stale_id, server_id=srv.id, username="u",
            password_encrypted=secrets_service.encrypt("old", aad=secrets_service.aad_for_server_test_password(stale_id)),
            ssh_public_key="ssh-ed25519 AAAAold",
            ssh_private_key_encrypted=secrets_service.encrypt("old", aad=secrets_service.aad_for_server_test_ssh_key(stale_id)),
        ))
        await db.flush()

        request = await pft_svc.on_server_prepared(db, srv)
        assert request.stage == STEP_USER_PROVISION
        assert request.status == PREPARE_FOR_TEST_IN_PROGRESS

        [task] = captured_dispatch
        assert task["task_kind"] == "server.prepare_for_test"
        stash = captured_stash[task["payload"]["test_creds_key"]]
        assert stash == {
            "test_username": "u",
            "test_password": ACCOUNT_SECRET["password"],
            "test_ssh_public_key": ACCOUNT_SECRET["public_key"],
        }
        # Секреты — только в stash, не в payload задачи.
        assert ACCOUNT_SECRET["password"] not in json.dumps(task["payload"])
        # Своей копии учётки server_service не держит.
        rows = (await db.execute(select(ServerTestCredentials).where(
            ServerTestCredentials.server_id == srv.id,
        ))).scalars().all()
        assert rows == []

    async def test_credential_gone_fails_user_provision(
        self, make_server, db, captured_dispatch, fake_secret, captured_stash, captured_callbacks,
    ):
        srv = await make_server(department_id="dep_a")
        req = await _request_row(db, srv, credential_id="cred_missing")
        await pft_svc.on_server_prepared(db, srv)
        assert captured_dispatch == []
        row = (await db.execute(select(ServerPrepareForTestRequest).where(
            ServerPrepareForTestRequest.id == req.id,
        ))).scalar_one()
        assert row.status == PREPARE_FOR_TEST_FAILED
        assert row.failed_step == STEP_USER_PROVISION
        assert "TEST_ACCOUNT_CREDENTIAL_UNAVAILABLE" in row.error
        assert captured_callbacks[-1] == {
            "correlation_id": req.correlation_id, "succeeded": False,
            "failed_step": STEP_USER_PROVISION, "error": row.error,
        }

    async def test_legacy_path_generates_random_credentials(
        self, make_server, db, captured_dispatch, fake_secret, captured_stash,
    ):
        srv = await make_server(department_id="dep_a")
        await _request_row(db, srv, credential_id=None)
        await pft_svc.on_server_prepared(db, srv)
        [task] = captured_dispatch
        stash = captured_stash[task["payload"]["test_creds_key"]]
        assert stash["test_username"] == "ignored"
        assert stash["test_password"] != ACCOUNT_SECRET["password"]
        assert stash["test_ssh_public_key"].startswith("ssh-ed25519 ")
        assert fake_secret["calls"] == []


class TestCallback:
    async def test_success_callback_has_no_secrets(self, make_server, db, captured_callbacks):
        srv = await make_server(department_id="dep_a")
        req = await _request_row(db, srv, credential_id=CRED_ID)
        status, delivered = await pft_svc.complete_from_worker(
            db, req, succeeded=True, failed_step=None, error="mode was voronezh",
        )
        assert (status, delivered) == (PREPARE_FOR_TEST_SUCCEEDED, True)
        assert captured_callbacks[-1] == {
            "correlation_id": req.correlation_id, "succeeded": True, "warning": "mode was voronezh",
        }


class TestSecretClient:
    async def test_reveal_validates_service_and_format(self, fake_secret):
        account = await secret_client.reveal_test_account(CRED_ID, "dep_a")
        assert account == {
            "username": "u",
            "password": ACCOUNT_SECRET["password"],
            "ssh_public_key": ACCOUNT_SECRET["public_key"],
            "ssh_private_key": ACCOUNT_SECRET["private_key"],
        }
        fake_secret["creds"][CRED_ID]["service"] = "host_ssh"
        with pytest.raises(Exception) as exc_info:
            await secret_client.reveal_test_account(CRED_ID, "dep_a")
        assert exc_info.value.error_code == "TEST_ACCOUNT_CREDENTIAL_INVALID"

        fake_secret["creds"][CRED_ID]["service"] = "test_account"
        fake_secret["creds"][CRED_ID]["secret"] = json.dumps({"v": 1, "password": "p"})
        with pytest.raises(Exception) as exc_info:
            await secret_client.reveal_test_account(CRED_ID, "dep_a")
        assert exc_info.value.error_code == "TEST_ACCOUNT_CREDENTIAL_INVALID"

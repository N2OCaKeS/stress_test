"""«Тестовая учётка» отдела.

* API `/department-test-account/{department_id}`: гейт (testing admin и
  department_admin своего отдела — да, остальные — 403), первая настройка
  и ротация пароля/ключа через secret_service, секреты наружу не уходят.
* Очередь: без учётки — `TEST_ACCOUNT_NOT_CONFIGURED` до брони стенда и на
  claim; с учёткой — ссылка в prepare-for-test, в стэше только ссылка,
  приватный ключ воркеру выдаётся из credential.
* Резолвер источника `test_account`.

secret_service подменяется MockTransport'ом с маленьким in-memory хранилищем
credential — тот же приём, что у server_service в `test_queue.py`.
"""

from __future__ import annotations

import base64
import json
import uuid

import httpx
import pytest
from sqlalchemy import select

from src.core.constants import QueueItemState
from src.db.session import AsyncSessionLocal
from src.models import DepartmentTestSettings, QueueItem
from src.repositories import department_test_settings as dts_repo
from src.schemas.department_test_account import DepartmentTestAccountUpdate
from src.services import audit_service, creds_stash, secret_client, server_client
from src.services import queue as queue_svc
from src.services import test_account
from src.utils.ids import department_test_settings_id
from tests.conftest import auth_hdr as _hdr
from tests.test_queue import (
    CALLBACK_BASE,
    LAUNCH_CTX,
    QUEUE_BASE,
    SERVER_SECRET,
    WORKER_SECRET,
    _create_stand,
    _create_test_def,
    _get_item,
    _identity,
    _server_hdr,
    configure_internal_keys as configure_internal_keys,
    mock_git_token as mock_git_token,
    mock_server_service as mock_server_service,
    recorded_calls as recorded_calls,
)

pytestmark = pytest.mark.real_test_account

_REAL_REVEAL = secret_client.reveal_credential

BASE = "/api/testing/v1/department-test-account"


class _SecretSettings:
    secret_service_url = "http://secret-service"
    secret_service_api_key = "dbos_bot_testing"
    secret_request_timeout_seconds = 2.0


@pytest.fixture
def fake_secret_service(monkeypatch):
    """In-memory secret_service: POST/PATCH/GET /credentials и reveal.

    `store[cred_id] = {"login", "secret", "owner_dept_id", "name", ...}`;
    `calls` — `(method, path, bearer)`; `deny_writes=True` — 403 на запись,
    как у пользователя без прав на сервисные credential отдела.
    """
    state = {"store": {}, "calls": [], "deny_writes": False}

    def handler(request: httpx.Request) -> httpx.Response:
        bearer = request.headers.get("authorization", "")
        path = request.url.path
        state["calls"].append((request.method, path, bearer))
        prefix = "/api/secret/v1/credentials"
        if request.method in ("POST", "PATCH") and state["deny_writes"] and not path.endswith("/reveal"):
            return httpx.Response(403, json={"error_code": "CREDENTIAL_ACCESS_DENIED"})
        if request.method == "POST" and path == prefix:
            body = json.loads(request.content)
            for cred in state["store"].values():
                if (cred["name"], cred["owner_dept_id"], cred["service"]) == (
                    body["name"], body["owner_dept_id"], body["service"],
                ):
                    return httpx.Response(409, json={"error_code": "NAME_DUPLICATE"})
            cred_id = f"cred_{uuid.uuid4().hex[:10]}"
            state["store"][cred_id] = {
                "id": cred_id, "name": body["name"], "service": body["service"],
                "scope": body["scope"], "owner_dept_id": body["owner_dept_id"],
                "login": body["login"],
                "secret": base64.b64decode(body["secret_b64"]).decode(),
            }
            return httpx.Response(201, json={k: v for k, v in state["store"][cred_id].items() if k != "secret"})
        if request.method == "GET" and path == prefix:
            items = [
                {k: v for k, v in cred.items() if k != "secret"}
                for cred in state["store"].values()
                if cred["service"] == request.url.params.get("service")
            ]
            return httpx.Response(200, json={"items": items, "next_cursor": None})
        cred_id = path[len(prefix) + 1:].split("/")[0]
        cred = state["store"].get(cred_id)
        if cred is None:
            return httpx.Response(404, json={"error_code": "CREDENTIAL_NOT_FOUND"})
        if request.method == "POST" and path.endswith("/reveal"):
            return httpx.Response(200, json={
                "login": cred["login"],
                "secret_b64": base64.b64encode(cred["secret"].encode()).decode(),
            })
        if request.method == "PATCH":
            body = json.loads(request.content)
            if "login" in body:
                cred["login"] = body["login"]
            if "secret_b64" in body:
                cred["secret"] = base64.b64decode(body["secret_b64"]).decode()
            return httpx.Response(200, json={k: v for k, v in cred.items() if k != "secret"})
        return httpx.Response(404, json={})

    monkeypatch.setattr(secret_client, "get_settings", lambda: _SecretSettings())
    monkeypatch.setattr(
        secret_client, "build_client",
        lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return state


@pytest.fixture
def audit_events(monkeypatch):
    events: list[tuple[str, dict]] = []

    def _emit(action, **kwargs):
        events.append((action, kwargs))

    monkeypatch.setattr(audit_service, "emit", _emit)
    return events


@pytest.fixture
def dep_admin_token(make_token, dept_a) -> str:
    return make_token(department_id=dept_a, platform_role="department_admin")


async def _settings_row(department_id: str = "dep_a") -> DepartmentTestSettings | None:
    async with AsyncSessionLocal() as db:
        return await dts_repo.get_by_department(db, department_id)


def _secret_of(state, cred_id: str) -> dict:
    return json.loads(state["store"][cred_id]["secret"])


# ── Доступ ───────────────────────────────────────────────────────────────────


class TestAccess:
    async def test_guest_and_no_role_get_403(self, client, guest_token, no_role_token, fake_secret_service):
        for token in (guest_token, no_role_token):
            resp = await client.get(f"{BASE}/dep_a", headers=_hdr(token))
            assert resp.status_code == 403, resp.text
            resp = await client.put(f"{BASE}/dep_a", headers=_hdr(token), json={"password": "p"})
            assert resp.status_code == 403, resp.text
        assert fake_secret_service["calls"] == []

    async def test_other_department_403(self, client, admin_token, fake_secret_service):
        resp = await client.get(f"{BASE}/dep_b", headers=_hdr(admin_token))
        assert resp.status_code == 403
        resp = await client.put(f"{BASE}/dep_b", headers=_hdr(admin_token), json={"password": "p"})
        assert resp.status_code == 403

    async def test_denied_update_is_audited(self, client, guest_token, fake_secret_service, audit_events):
        await client.put(f"{BASE}/dep_a", headers=_hdr(guest_token), json={"password": "p"})
        assert ("department_test_account.update", "denied") in [
            (a, kw["status"]) for a, kw in audit_events
        ]

    async def test_testing_admin_and_dep_admin_allowed(
        self, client, admin_token, dep_admin_token, fake_secret_service,
    ):
        for token in (admin_token, dep_admin_token):
            resp = await client.get(f"{BASE}/dep_a", headers=_hdr(token))
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["configured"] is False
            assert body["login_hint"] == "u"
            assert body["home_template"] == "/home/{TEST_USER}"


# ── Настройка и ротация ─────────────────────────────────────────────────────


class TestUpsert:
    async def test_first_setup_requires_password(self, client, admin_token, fake_secret_service):
        resp = await client.put(f"{BASE}/dep_a", headers=_hdr(admin_token), json={"login": "u"})
        assert resp.status_code == 422, resp.text
        assert resp.json()["error_code"] == "TEST_ACCOUNT_PASSWORD_REQUIRED"
        assert fake_secret_service["store"] == {}

    async def test_first_setup_creates_service_credential(
        self, client, admin_token, fake_secret_service, audit_events,
    ):
        resp = await client.put(
            f"{BASE}/dep_a", headers=_hdr(admin_token),
            json={"login": "tester", "password": "srv-Pass-1"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["configured"] is True
        assert body["login"] == "tester"
        assert body["has_password"] is True
        assert body["home"] == "/home/tester"
        assert body["ssh_public_key"].startswith("ssh-ed25519 ")
        # Ни пароль, ни приватный ключ в ответ не попадают.
        text = resp.text
        assert "srv-Pass-1" not in text
        assert "PRIVATE KEY" not in text

        [(cred_id, cred)] = fake_secret_service["store"].items()
        assert body["credential_id"] == cred_id
        assert (cred["scope"], cred["service"], cred["owner_dept_id"], cred["login"]) == (
            "service", "test_account", "dep_a", "tester",
        )
        secret = _secret_of(fake_secret_service, cred_id)
        assert secret["password"] == "srv-Pass-1"
        assert secret["private_key"].startswith("-----BEGIN OPENSSH PRIVATE KEY-----")
        assert secret["public_key"] == body["ssh_public_key"]
        # Запись — bearer'ом пользователя, не ботом сервиса.
        post = [c for c in fake_secret_service["calls"] if c[0] == "POST" and not c[1].endswith("/reveal")]
        assert post and all(c[2] == f"Bearer {admin_token}" for c in post)

        row = await _settings_row()
        assert row.test_account_credential_id == cred_id
        assert row.test_username == "tester"

        [(action, kwargs)] = [e for e in audit_events if e[0] == "department_test_account.update"]
        assert kwargs["status"] == "success"
        assert kwargs["details"]["created"] is True
        assert set(kwargs["details"]["fields"]) == {"login", "password", "ssh_key"}
        assert "srv-Pass-1" not in json.dumps(kwargs, default=str)

    async def test_login_defaults_to_legacy_hint(self, client, admin_token, fake_secret_service):
        async with AsyncSessionLocal() as db:
            await dts_repo.create(db, {
                "id": department_test_settings_id(), "department_id": "dep_a",
                "retry_enabled": True, "test_username": "legacy",
                "activity_report_auto_generate": False,
            })
            await db.commit()
        got = await client.get(f"{BASE}/dep_a", headers=_hdr(admin_token))
        assert got.json()["login_hint"] == "legacy"
        resp = await client.put(f"{BASE}/dep_a", headers=_hdr(admin_token), json={"password": "p"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["login"] == "legacy"

    async def test_rotation_keeps_unset_parts(self, client, dep_admin_token, fake_secret_service, audit_events):
        first = (await client.put(
            f"{BASE}/dep_a", headers=_hdr(dep_admin_token), json={"login": "u", "password": "one"},
        )).json()
        cred_id = first["credential_id"]
        key_before = _secret_of(fake_secret_service, cred_id)["private_key"]

        # Смена только пароля: ключ тот же, credential тот же.
        resp = await client.put(f"{BASE}/dep_a", headers=_hdr(dep_admin_token), json={"password": "two"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["credential_id"] == cred_id
        secret = _secret_of(fake_secret_service, cred_id)
        assert secret["password"] == "two"
        assert secret["private_key"] == key_before

        # Новая пара по флагу, пароль не трогаем.
        resp = await client.put(
            f"{BASE}/dep_a", headers=_hdr(dep_admin_token), json={"regenerate_ssh_key": True},
        )
        body = resp.json()
        secret = _secret_of(fake_secret_service, cred_id)
        assert secret["password"] == "two"
        assert secret["private_key"] != key_before
        assert body["ssh_public_key"] == secret["public_key"] != first["ssh_public_key"]
        assert len(fake_secret_service["store"]) == 1

        fields = [kw["details"]["fields"] for a, kw in audit_events if a == "department_test_account.update"]
        assert fields[1] == ["password"]
        assert fields[2] == ["ssh_key"]

    async def test_home_template_only_does_not_touch_secret(self, client, admin_token, fake_secret_service):
        await client.put(f"{BASE}/dep_a", headers=_hdr(admin_token), json={"password": "p"})
        patches_before = sum(1 for c in fake_secret_service["calls"] if c[0] == "PATCH")
        resp = await client.put(
            f"{BASE}/dep_a", headers=_hdr(admin_token), json={"home_template": "/srv/{TEST_USER}/home"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["home"] == "/srv/u/home"
        assert sum(1 for c in fake_secret_service["calls"] if c[0] == "PATCH") == patches_before

    @pytest.mark.parametrize("body", [
        {"login": "bad login"},
        {"login": "../x"},
        {"password": "line\nbreak"},
        {"home_template": "relative/{TEST_USER}"},
        {"home_template": "/home/{OTHER}"},
        {"home_template": "/home/$(id)"},
    ])
    async def test_invalid_payload_422(self, client, admin_token, fake_secret_service, body):
        resp = await client.put(f"{BASE}/dep_a", headers=_hdr(admin_token), json=body)
        assert resp.status_code == 422, resp.text

    async def test_secret_service_denies_write(self, client, admin_token, fake_secret_service, audit_events):
        fake_secret_service["deny_writes"] = True
        resp = await client.put(f"{BASE}/dep_a", headers=_hdr(admin_token), json={"password": "p"})
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "CREDENTIAL_ACCESS_DENIED"
        assert (await _settings_row()) is None
        statuses = [kw["status"] for a, kw in audit_events if a == "department_test_account.update"]
        assert statuses == ["denied"]

    async def test_adopts_existing_credential_on_name_duplicate(self, client, admin_token, fake_secret_service):
        fake_secret_service["store"]["cred_orphan"] = {
            "id": "cred_orphan", "name": test_account.CREDENTIAL_NAME, "service": "test_account",
            "scope": "service", "owner_dept_id": "dep_a", "login": "old", "secret": "{}",
        }
        resp = await client.put(f"{BASE}/dep_a", headers=_hdr(admin_token), json={"password": "p"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["credential_id"] == "cred_orphan"
        assert _secret_of(fake_secret_service, "cred_orphan")["password"] == "p"

    async def test_credential_deleted_in_secret_service(self, client, admin_token, fake_secret_service):
        first = (await client.put(f"{BASE}/dep_a", headers=_hdr(admin_token), json={"password": "p"})).json()
        fake_secret_service["store"].clear()
        got = (await client.get(f"{BASE}/dep_a", headers=_hdr(admin_token))).json()
        assert got["configured"] is False
        assert got["credential_missing"] is True
        # Повторная настройка заводит новый credential.
        resp = await client.put(f"{BASE}/dep_a", headers=_hdr(admin_token), json={"password": "p2"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["credential_id"] != first["credential_id"]


# ── Резолвер источника test_account (C1) ─────────────────────────


class TestResolver:
    async def test_not_configured(self, fake_secret_service):
        async with AsyncSessionLocal() as db:
            with pytest.raises(Exception) as exc_info:
                await test_account.resolve_field(db, "dep_a", "login")
        assert exc_info.value.error_code == "TEST_ACCOUNT_NOT_CONFIGURED"

    async def test_fields(self, client, admin_token, fake_secret_service):
        await client.put(
            f"{BASE}/dep_a", headers=_hdr(admin_token),
            json={"login": "tester", "password": "pw", "home_template": "/data/{TEST_USER}"},
        )
        async with AsyncSessionLocal() as db:
            assert await test_account.resolve_field(db, "dep_a", "login") == "tester"
            assert await test_account.resolve_field(db, "dep_a", "password") == "pw"
            assert await test_account.resolve_field(db, "dep_a", "home") == "/data/tester"
            with pytest.raises(Exception) as exc_info:
                await test_account.resolve_field(db, "dep_a", "private_key")
        assert exc_info.value.error_code == "TEST_ACCOUNT_FIELD_UNKNOWN"
        assert test_account.SENSITIVE_FIELDS == {"password"}

    def test_secret_roundtrip_and_repr(self):
        private, public = test_account.generate_ssh_keypair()
        secret = test_account.encode_secret(password="pw", private_key=private, public_key=public)
        account = test_account.decode_secret("u", secret)
        assert (account.login, account.password, account.private_key, account.public_key) == ("u", "pw", private, public)
        assert "pw" not in repr(account) and "PRIVATE" not in repr(account)
        with pytest.raises(Exception) as exc_info:
            test_account.decode_secret("u", '{"v": 1, "password": "pw"}')
        assert exc_info.value.error_code == "TEST_ACCOUNT_INVALID"

    def test_update_schema_defaults(self):
        body = DepartmentTestAccountUpdate()
        assert body.regenerate_ssh_key is False and body.password is None


# ── Очередь ─────────────────────────────────────────────────────────────────


@pytest.fixture
def prepare_calls(monkeypatch):
    """Перехват тел `start_prepare_for_test` поверх HTTP-мока server_service."""
    calls: list[dict] = []
    original = server_client.start_prepare_for_test

    async def _wrapped(server_id, **kwargs):
        calls.append(kwargs)
        return await original(server_id, **kwargs)

    monkeypatch.setattr(server_client, "start_prepare_for_test", _wrapped)
    return calls


async def _configure_account(client, token) -> str:
    resp = await client.put(f"{BASE}/dep_a", headers=_hdr(token), json={"login": "u", "password": "srv"})
    assert resp.status_code == 200, resp.text
    return resp.json()["credential_id"]


class TestQueue:
    async def test_enqueue_without_account_fails_before_reservation(
        self, client, admin_token, mock_server_service, recorded_calls, prepare_calls,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        async with AsyncSessionLocal() as db:
            item = await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)
        row = await _get_item(item.id)
        assert row.state == QueueItemState.FAILED
        assert row.failed_step == "test_account"
        assert row.error.startswith("TEST_ACCOUNT_NOT_CONFIGURED")
        assert prepare_calls == []
        assert not any(path.endswith("/acquire-for-service") for _m, path in recorded_calls)

    async def test_prepare_claim_use_credential(
        self, client, admin_token, mock_server_service, prepare_calls,
        configure_internal_keys, mock_git_token, fake_secret_service, monkeypatch,
    ):
        mock_server_service(host="10.1.1.1")
        await mock_git_token()
        _reveal_git_or_real(monkeypatch)

        cred_id = await _configure_account(client, admin_token)
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        async with AsyncSessionLocal() as db:
            item = await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)
        assert item.state == QueueItemState.PREPARING
        assert prepare_calls[-1]["test_account_credential_id"] == cred_id

        resp = await client.post(
            f"{CALLBACK_BASE}/{item.prepare_request_id}/completed",
            headers=_server_hdr("server_service", SERVER_SECRET),
            json={"correlation_id": item.id, "succeeded": True},
        )
        assert resp.status_code == 200, resp.text
        ready = await _get_item(item.id)
        assert ready.state == QueueItemState.READY
        # В стэше — только ссылка на credential.
        stash = await creds_stash.pop_creds(ready.creds_stash_key)
        assert stash == {"test_account_credential_id": cred_id}
        await creds_stash.store_creds(ready.creds_stash_key, stash)

        # Ротация ключа до claim: воркер получает уже новый ключ.
        await client.put(f"{BASE}/dep_a", headers=_hdr(admin_token), json={"regenerate_ssh_key": True})
        expected_key = _secret_of(fake_secret_service, cred_id)["private_key"]

        resp = await client.post(f"{QUEUE_BASE}/claim", headers=_server_hdr("testing_worker", WORKER_SECRET))
        assert resp.status_code == 200, resp.text
        payload = resp.json()["item"]
        assert payload["test_username"] == "u"
        assert payload["test_ssh_private_key"] == expected_key
        assert "srv" not in json.dumps({k: v for k, v in payload.items() if k != "test_ssh_private_key"})

    async def test_claim_without_account_fails_clearly(
        self, client, admin_token, mock_server_service, configure_internal_keys, mock_git_token,
        fake_secret_service, monkeypatch,
    ):
        mock_server_service()
        await mock_git_token()
        _reveal_git_or_real(monkeypatch)
        cred_id = await _configure_account(client, admin_token)
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        async with AsyncSessionLocal() as db:
            item = await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)
        assert item.state == QueueItemState.PREPARING

        # Учётку сняли между подготовкой и callback'ом.
        async with AsyncSessionLocal() as db:
            row = await dts_repo.get_by_department(db, "dep_a")
            await dts_repo.update(db, row, {"test_account_credential_id": None})
            await db.commit()
        await client.post(
            f"{CALLBACK_BASE}/{item.prepare_request_id}/completed",
            headers=_server_hdr("server_service", SERVER_SECRET),
            json={"correlation_id": item.id, "succeeded": True},
        )
        resp = await client.post(f"{QUEUE_BASE}/claim", headers=_server_hdr("testing_worker", WORKER_SECRET))
        assert resp.status_code == 200, resp.text
        assert resp.json()["item"] is None
        failed = await _get_item(item.id)
        assert failed.state == QueueItemState.FAILED
        assert failed.failed_step == "test_account"
        assert failed.error.startswith("TEST_ACCOUNT_NOT_CONFIGURED")
        assert cred_id in fake_secret_service["store"]
        async with AsyncSessionLocal() as db:
            retries = (await db.execute(
                select(QueueItem).where(QueueItem.retry_of_id == item.id)
            )).scalars().all()
        # Retry (включён по умолчанию) тоже упал до брони с той же причиной.
        assert all(r.error.startswith("TEST_ACCOUNT_NOT_CONFIGURED") for r in retries)


def _reveal_git_or_real(monkeypatch) -> None:
    """`mock_git_token` подменяет reveal целиком; учётке нужен настоящий (через fake secret_service)."""

    async def _reveal(cred_id: str):
        if cred_id == "cred_git_header":
            return ("git-bot", "git-token-value")
        return await _REAL_REVEAL(cred_id)

    monkeypatch.setattr(secret_client, "reveal_credential", _reveal)

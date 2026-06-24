"""Интеграционные тесты `/api/server/v1/server-accounts` (CRUD + rotate_password).

Покрытие:
* POST / — happy paths (admin/operator), reader/guest → 403,
  cross-dept server → 404 hidden, has_sudo требует grant_sudo,
  дубль (server_id, login) → 409 ACCOUNT_DUPLICATE,
  пароль шифруется и не возвращается в response;
* GET / (list) — фильтрация по server_id, пагинация,
  cross-dept → 404 hidden, без роли с view → 403;
* GET /{id} — reader видит свой dept, cross-dept → 404,
  плейнтекст и password_encrypted никогда не в ответе;
* PATCH /{id} — operator OK, reader → 403, cross-dept → 404,
  подъём has_sudo требует grant_sudo, снятие has_sudo разрешено
  обычным update'ом;
* DELETE /{id} — operator без default `delete` → 403, admin удаляет,
  cross-dept → 404;
* POST /{id}/rotate_password — happy path (admin/operator), reader → 403,
  cross-dept → 404, plaintext не возвращается,
  password_rotated_at обновлён, password_encrypted в БД меняется.
"""

from __future__ import annotations

import pytest

BASE = "/api/server/v1/server-accounts"


from tests._helpers import assert_error, auth_hdr as _hdr, b64  # noqa: E402


class TestListOpenApiSchema:
    """`GET /server-accounts` должен нести типизированный response-schema, а не
    пустую `{}`; при этом plaintext-пароль (`password_b64`) в листинге не светим
    через схему — он заполняется только в single-GET держателю `view_password`."""

    def test_list_response_schema_is_typed(self):
        from src.main import app

        spec = app.openapi()
        get_op = spec["paths"][BASE]["get"]
        schema = get_op["responses"]["200"]["content"]["application/json"]["schema"]
        assert schema != {}
        refs = {opt.get("$ref") for opt in schema.get("anyOf", [])}
        assert any(r and "PaginatedResponse_ServerAccountResponse_" in r for r in refs)
        assert any(
            r and "CursorPaginatedResponse_ServerAccountResponse_" in r for r in refs
        )

    def test_account_schema_hides_raw_secret(self):
        from src.main import app

        spec = app.openapi()
        props = spec["components"]["schemas"]["ServerAccountResponse"]["properties"]
        # Зашифрованный пароль и сырой plaintext в схему не попадают.
        assert "password" not in props
        assert "password_encrypted" not in props
        assert {"id", "login", "server_ids", "department_id"} <= set(props)


@pytest.fixture
def captured_dispatch(monkeypatch):
    """Перехват worker_client.dispatch_task для fan-out'а на PATCH'е."""
    calls: list[dict] = []

    async def fake_dispatch(*, db=None, task_kind, target_server_id, payload,
                            created_by, request_id,
                            target_resource_id=None, idempotency_key=None,
                            priority=0,
                            return_hit=False):
        calls.append({
            "task_kind": task_kind,
            "target_server_id": target_server_id,
            "target_resource_id": target_resource_id,
            "payload": payload,
            "idempotency_key": idempotency_key,
        })
        new_id = f"tsk_{task_kind.replace('.', '_')}_fake_{len(calls)}"
        return (new_id, False) if return_hit else new_id

    async def fake_dispatch_with_hit(**kwargs):
        kwargs["return_hit"] = True
        return await fake_dispatch(**kwargs)

    import src.services.worker_client as worker_mod
    monkeypatch.setattr(worker_mod, "dispatch_task", fake_dispatch)
    monkeypatch.setattr(worker_mod, "dispatch_task_with_hit", fake_dispatch_with_hit)
    monkeypatch.setattr(
        "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task",
        fake_dispatch,
    )
    monkeypatch.setattr(
        "src.api.v1.endpoints.worker_dispatch.worker_client.dispatch_task_with_hit",
        fake_dispatch_with_hit,
    )
    return calls


# ── POST / (create) ──────────────────────────────────────────────────────────

class TestCreateAccount:
    async def test_admin_creates_with_explicit_password(
        self, client, admin_token, make_server,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            BASE,
            headers=_hdr(admin_token),
            json={
                "server_ids": [srv.id],
                "login": "root",
                "password_b64": b64("s3cret-explicit"),
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["login"] == "root"
        assert body["server_ids"] == [srv.id]
        # Пароль никогда не должен утекать наружу.
        assert "password" not in body
        assert "password_encrypted" not in body
        assert "s3cret-explicit" not in resp.text

    async def test_create_then_reveal_round_trips_password(
        self, client, admin_role_token_a, make_server,
    ):
        """password_b64 на create → reveal через GET декодится в исходный plaintext."""
        import base64

        srv = await make_server(department_id="dep_a")
        plaintext = "round-trip-pwd-9"
        create = await client.post(
            BASE,
            headers=_hdr(admin_role_token_a),
            json={
                "server_ids": [srv.id],
                "login": "root",
                "password_b64": b64(plaintext),
            },
        )
        assert create.status_code == 201, create.text
        account_id = create.json()["id"]
        get = await client.get(f"{BASE}/{account_id}", headers=_hdr(admin_role_token_a))
        assert get.status_code == 200
        revealed = get.json()["password_b64"]
        assert base64.b64decode(revealed).decode("utf-8") == plaintext

    async def test_operator_creates_with_generated_password(
        self, client, operator_token_a, make_server, db,
    ):
        """Если password не передан — сервер генерит его сам и шифрует."""
        from src.models import ServerAccount
        from src.services import secrets_service
        from sqlalchemy import select

        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            BASE,
            headers=_hdr(operator_token_a),
            json={"server_ids": [srv.id], "login": "deploy"},
        )
        assert resp.status_code == 201
        account_id = resp.json()["id"]
        row = (await db.execute(
            select(ServerAccount).where(ServerAccount.id == account_id)
        )).scalar_one()
        assert row.password_encrypted is not None
        # Сгенерённый пароль декодируется обратно через secrets_service.
        plain = secrets_service.decrypt(
            row.password_encrypted,
            aad=secrets_service.aad_for_server_account_password(row.id),
        )
        assert len(plain) >= 16

    async def test_reader_cannot_create(
        self, client, reader_token_a, make_server,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            BASE,
            headers=_hdr(reader_token_a),
            json={"server_ids": [srv.id], "login": "root"},
        )
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_guest_cannot_create(self, client, guest_token_a, make_server):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            BASE,
            headers=_hdr(guest_token_a),
            json={"server_ids": [srv.id], "login": "root"},
        )
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_no_token_returns_401(self, client, make_server):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            BASE,
            json={"server_ids": [srv.id], "login": "root"},
        )
        assert_error(resp, 401, "ACCESS_TOKEN_MISSING")

    async def test_cross_dept_server_returns_404_hidden(
        self, client, operator_token_a, make_server,
    ):
        """Сервер чужого dept — даже факт существования не должен утекать."""
        srv = await make_server(department_id="dep_b")
        resp = await client.post(
            BASE,
            headers=_hdr(operator_token_a),
            json={"server_ids": [srv.id], "login": "root"},
        )
        assert_error(resp, 404, "SERVER_NOT_FOUND")

    async def test_has_sudo_without_grant_sudo_role_denied(
        self, client, operator_token_a, make_server,
    ):
        """operator имеет create, но НЕ имеет grant_sudo — 403."""
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            BASE,
            headers=_hdr(operator_token_a),
            json={"server_ids": [srv.id], "login": "rooty", "has_sudo": True},
        )
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_admin_creates_with_sudo(
        self, client, admin_role_token_a, make_server,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            BASE,
            headers=_hdr(admin_role_token_a),
            json={"server_ids": [srv.id], "login": "rooty", "has_sudo": True},
        )
        assert resp.status_code == 201
        assert resp.json()["has_sudo"] is True

    async def test_create_with_sudo_group_without_grant_denied(
        self, client, operator_token_a, make_server,
    ):
        """Создание учётки сразу в sudo-дающей группе без grant_sudo → 403."""
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            BASE,
            headers=_hdr(operator_token_a),
            json={
                "server_ids": [srv.id], "login": "rooty",
                "unix_groups": ["astra-admin"],
            },
        )
        assert_error(resp, 403, "SUDO_GROUP_REQUIRES_GRANT_SUDO")

    async def test_admin_creates_with_sudo_group(
        self, client, admin_role_token_a, make_server,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            BASE,
            headers=_hdr(admin_role_token_a),
            json={
                "server_ids": [srv.id], "login": "rooty",
                "unix_groups": ["wheel", "docker"],
            },
        )
        assert resp.status_code == 201
        assert set(resp.json()["unix_groups"]) == {"wheel", "docker"}

    async def test_duplicate_login_on_same_server_conflict(
        self, client, admin_token, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_a")
        await make_account(server_id=srv.id, login="root")
        resp = await client.post(
            BASE,
            headers=_hdr(admin_token),
            json={"server_ids": [srv.id], "login": "root"},
        )
        assert_error(resp, 409, "ACCOUNT_DUPLICATE")

    async def test_nonexistent_server_returns_404(
        self, client, admin_token,
    ):
        resp = await client.post(
            BASE,
            headers=_hdr(admin_token),
            json={"server_ids": ["srv_ghost"], "login": "root"},
        )
        assert_error(resp, 404, "SERVER_NOT_FOUND")

    async def test_valid_login_accepted(
        self, client, admin_token, make_server,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            BASE,
            headers=_hdr(admin_token),
            json={"server_ids": [srv.id], "login": "root"},
        )
        assert resp.status_code == 201

    @pytest.mark.parametrize(
        "bad_login",
        [
            "root\n",
            "root;rm -rf /",
            "root password",
            "root:postgres",
            "root\r\n",
        ],
        ids=["newline", "semicolon", "space", "colon", "crlf"],
    )
    async def test_login_pattern_rejects_unsafe_chars(
        self, client, admin_token, make_server, bad_login,
    ):
        # Защита от log injection (CRLF в audit details) и от любых
        # shell-метасимволов на пути к chpasswd. Pydantic возвращает 422
        # до того, как login попадает в сервис/репозиторий/аудит.
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            BASE,
            headers=_hdr(admin_token),
            json={"server_ids": [srv.id], "login": bad_login},
        )
        assert_error(resp, 422, "VALIDATION_ERROR")


# ── GET / (list) ─────────────────────────────────────────────────────────────

class TestListAccounts:
    async def test_reader_lists_own_server_accounts(
        self, client, reader_token_a, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_a")
        await make_account(server_id=srv.id, login="root")
        await make_account(server_id=srv.id, login="deploy")
        resp = await client.get(
            BASE, headers=_hdr(reader_token_a), params={"server_id": srv.id},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        logins = {a["login"] for a in body["items"]}
        assert logins == {"root", "deploy"}
        # Ни в одной карточке не должно быть пароля.
        for item in body["items"]:
            assert "password" not in item
            assert "password_encrypted" not in item

    async def test_list_other_dept_server_returns_404_hidden(
        self, client, reader_token_a, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_b")
        await make_account(server_id=srv.id, login="root")
        resp = await client.get(
            BASE, headers=_hdr(reader_token_a), params={"server_id": srv.id},
        )
        assert_error(resp, 404, "SERVER_NOT_FOUND")

    async def test_list_pagination(
        self, client, admin_token, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_a")
        for i in range(5):
            await make_account(server_id=srv.id, login=f"user{i}")
        resp = await client.get(
            BASE,
            headers=_hdr(admin_token),
            params={"server_id": srv.id, "limit": 2, "offset": 1},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["limit"] == 2
        assert body["offset"] == 1
        assert body["total"] == 5
        assert len(body["items"]) == 2

    async def test_list_requires_server_id_param(
        self, client, reader_token_a,
    ):
        resp = await client.get(BASE, headers=_hdr(reader_token_a))
        assert_error(resp, 422, "VALIDATION_ERROR")

    async def test_no_role_user_returns_403(
        self, client, no_role_token_a, make_server,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.get(
            BASE, headers=_hdr(no_role_token_a), params={"server_id": srv.id},
        )
        assert_error(resp, 403, "PERMISSION_DENIED")


# ── GET /{id} ────────────────────────────────────────────────────────────────

class TestGetAccount:
    async def test_reader_sees_own_dept(
        self, client, reader_token_a, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="root")
        resp = await client.get(f"{BASE}/{acc.id}", headers=_hdr(reader_token_a))
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == acc.id
        assert body["login"] == "root"
        assert "password" not in body
        assert "password_encrypted" not in body

    async def test_cross_dept_returns_404_hidden(
        self, client, reader_token_a, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_b")
        acc = await make_account(server_id=srv.id, login="root")
        resp = await client.get(f"{BASE}/{acc.id}", headers=_hdr(reader_token_a))
        assert_error(resp, 404, "ACCOUNT_NOT_FOUND")

    async def test_nonexistent_id_returns_404(self, client, reader_token_a):
        resp = await client.get(f"{BASE}/acc_ghost", headers=_hdr(reader_token_a))
        assert_error(resp, 404, "ACCOUNT_NOT_FOUND")

    async def test_no_role_returns_403(
        self, client, no_role_token_a, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id)
        resp = await client.get(f"{BASE}/{acc.id}", headers=_hdr(no_role_token_a))
        assert_error(resp, 403, "PERMISSION_DENIED")


# ── PATCH /{id} ──────────────────────────────────────────────────────────────

class TestUpdateAccount:
    async def test_operator_updates_unix_groups(
        self, client, operator_token_a, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="deploy")
        resp = await client.patch(
            f"{BASE}/{acc.id}",
            headers=_hdr(operator_token_a),
            json={"unix_groups": ["staff", "docker"], "shell": "/bin/zsh"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["unix_groups"] == ["staff", "docker"]
        assert body["shell"] == "/bin/zsh"

    async def test_reader_cannot_update(
        self, client, reader_token_a, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id)
        resp = await client.patch(
            f"{BASE}/{acc.id}",
            headers=_hdr(reader_token_a),
            json={"shell": "/bin/zsh"},
        )
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_cross_dept_returns_404_hidden(
        self, client, operator_token_a, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_b")
        acc = await make_account(server_id=srv.id)
        resp = await client.patch(
            f"{BASE}/{acc.id}",
            headers=_hdr(operator_token_a),
            json={"shell": "/bin/zsh"},
        )
        assert_error(resp, 404, "ACCOUNT_NOT_FOUND")

    async def test_empty_update_is_noop(
        self, client, operator_token_a, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="orig")
        resp = await client.patch(
            f"{BASE}/{acc.id}",
            headers=_hdr(operator_token_a),
            json={},
        )
        assert resp.status_code == 200
        assert resp.json()["login"] == "orig"

    async def test_raise_sudo_without_grant_role_denied(
        self, client, operator_token_a, make_server, make_account,
    ):
        """operator может update, но подъём has_sudo=True требует grant_sudo."""
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, has_sudo=False)
        resp = await client.patch(
            f"{BASE}/{acc.id}",
            headers=_hdr(operator_token_a),
            json={"has_sudo": True},
        )
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_admin_can_raise_sudo(
        self, client, admin_role_token_a, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, has_sudo=False)
        resp = await client.patch(
            f"{BASE}/{acc.id}",
            headers=_hdr(admin_role_token_a),
            json={"has_sudo": True},
        )
        assert resp.status_code == 200
        assert resp.json()["has_sudo"] is True

    async def test_operator_can_drop_sudo(
        self, client, operator_token_a, make_server, make_account,
    ):
        """Снятие has_sudo — обычный update, grant_sudo не требуется."""
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, has_sudo=True)
        resp = await client.patch(
            f"{BASE}/{acc.id}",
            headers=_hdr(operator_token_a),
            json={"has_sudo": False},
        )
        assert resp.status_code == 200
        assert resp.json()["has_sudo"] is False

    async def test_add_sudo_group_without_grant_role_denied(
        self, client, operator_token_a, make_server, make_account,
    ):
        """Добавление sudo-дающей группы под обычным update без grant_sudo → 403.

        Закрывает эскалацию через unix_groups: has_sudo остаётся False, но
        попадание в `sudo`/`astra-admin`/`wheel` даёт sudo на боксе.
        """
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, has_sudo=False, unix_groups=["docker"])
        resp = await client.patch(
            f"{BASE}/{acc.id}",
            headers=_hdr(operator_token_a),
            json={"unix_groups": ["docker", "sudo"]},
        )
        assert_error(resp, 403, "SUDO_GROUP_REQUIRES_GRANT_SUDO")

    async def test_admin_can_add_sudo_group(
        self, client, admin_role_token_a, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, has_sudo=False, unix_groups=["docker"])
        resp = await client.patch(
            f"{BASE}/{acc.id}",
            headers=_hdr(admin_role_token_a),
            json={"unix_groups": ["docker", "wheel"]},
        )
        assert resp.status_code == 200
        assert "wheel" in resp.json()["unix_groups"]

    async def test_operator_can_drop_sudo_group(
        self, client, operator_token_a, make_server, make_account,
    ):
        """Снятие sudo-дающей группы — обычный update (понижение), без grant_sudo."""
        srv = await make_server(department_id="dep_a")
        acc = await make_account(
            server_id=srv.id, has_sudo=False, unix_groups=["docker", "astra-admin"],
        )
        resp = await client.patch(
            f"{BASE}/{acc.id}",
            headers=_hdr(operator_token_a),
            json={"unix_groups": ["docker"]},
        )
        assert resp.status_code == 200
        assert resp.json()["unix_groups"] == ["docker"]

    async def test_keep_existing_sudo_group_no_grant_needed(
        self, client, operator_token_a, make_server, make_account,
    ):
        """Сохранение уже имеющейся sudo-группы (не добавление) под update — ok."""
        srv = await make_server(department_id="dep_a")
        acc = await make_account(
            server_id=srv.id, has_sudo=False, unix_groups=["sudo"],
        )
        resp = await client.patch(
            f"{BASE}/{acc.id}",
            headers=_hdr(operator_token_a),
            json={"unix_groups": ["sudo", "docker"], "shell": "/bin/bash"},
        )
        assert resp.status_code == 200
        assert set(resp.json()["unix_groups"]) == {"sudo", "docker"}


# ── PATCH /{id} fan-out → update_on_host ─────────────────────────────────────

class TestUpdateAccountFanout:
    async def test_managed_attr_edit_fans_out_to_all_present_servers(
        self, client, admin_role_token_a, make_server, make_account,
        captured_dispatch,
    ):
        srv_a = await make_server(department_id="dep_a")
        srv_b = await make_server(department_id="dep_a")
        acc = await make_account(
            server_ids=[srv_a.id, srv_b.id], login="shared", has_sudo=False,
        )
        resp = await client.patch(
            f"{BASE}/{acc.id}",
            headers=_hdr(admin_role_token_a),
            json={"has_sudo": True, "unix_groups": ["sudo"]},
        )
        assert resp.status_code == 200, resp.text
        # Диспатч update_on_host ушёл на ОБА привязанных сервера.
        assert len(captured_dispatch) == 2
        assert {c["task_kind"] for c in captured_dispatch} == {"account.update_on_host"}
        assert {c["target_server_id"] for c in captured_dispatch} == {srv_a.id, srv_b.id}
        for c in captured_dispatch:
            assert c["payload"]["has_sudo"] is True
            assert c["payload"]["unix_groups"] == ["sudo"]
            assert c["target_resource_id"] == acc.id

    async def test_metadata_only_edit_no_fanout(
        self, client, operator_token_a, make_server, make_account,
        captured_dispatch,
    ):
        # linked_user_id — не OS-управляемое поле, fan-out не нужен.
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="meta")
        resp = await client.patch(
            f"{BASE}/{acc.id}",
            headers=_hdr(operator_token_a),
            json={"linked_user_id": "usr_meta_owner"},
        )
        assert resp.status_code == 200, resp.text
        assert captured_dispatch == []

    async def test_empty_update_no_fanout(
        self, client, operator_token_a, make_server, make_account,
        captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="noop")
        resp = await client.patch(
            f"{BASE}/{acc.id}", headers=_hdr(operator_token_a), json={},
        )
        assert resp.status_code == 200
        assert captured_dispatch == []

    async def test_noop_managed_field_patch_does_not_fan_out(
        self, client, admin_role_token_a, make_server, make_account,
        captured_dispatch,
    ):
        # PATCH `{has_sudo: True}` на уже-True аккаунт — value не меняется,
        # репозиторий не делает UPDATE, fanout запускать не за чем.
        srv = await make_server(department_id="dep_a")
        acc = await make_account(
            server_id=srv.id, login="already_sudo", has_sudo=True,
        )
        resp = await client.patch(
            f"{BASE}/{acc.id}",
            headers=_hdr(admin_role_token_a),
            json={"has_sudo": True},
        )
        assert resp.status_code == 200, resp.text
        assert captured_dispatch == []

    async def test_noop_unix_groups_same_set_does_not_fan_out(
        self, client, admin_role_token_a, make_server, make_account,
        captured_dispatch,
    ):
        # Передаём те же группы, но в другом порядке — `set(...)`-сравнение
        # должно считать это no-op.
        srv = await make_server(department_id="dep_a")
        acc = await make_account(
            server_id=srv.id, login="grp", unix_groups=["a", "b"],
        )
        resp = await client.patch(
            f"{BASE}/{acc.id}",
            headers=_hdr(admin_role_token_a),
            json={"unix_groups": ["b", "a"]},
        )
        assert resp.status_code == 200, resp.text
        assert captured_dispatch == []

    async def test_fanout_skips_absent_links(
        self, client, admin_role_token_a, make_server, make_account, db,
        captured_dispatch,
    ):
        from sqlalchemy import select

        from src.models import ServerAccountServer

        srv_present = await make_server(department_id="dep_a")
        srv_absent = await make_server(department_id="dep_a")
        acc = await make_account(
            server_ids=[srv_present.id, srv_absent.id], login="partial",
        )
        # Аккаунт ушёл с одного из боксов (present_on_server=False).
        link = (await db.execute(
            select(ServerAccountServer).where(
                ServerAccountServer.account_id == acc.id,
                ServerAccountServer.server_id == srv_absent.id,
            )
        )).scalar_one()
        link.present_on_server = False
        await db.commit()

        resp = await client.patch(
            f"{BASE}/{acc.id}",
            headers=_hdr(admin_role_token_a),
            json={"shell": "/bin/zsh"},
        )
        assert resp.status_code == 200, resp.text
        # Только сервер с present-связкой получил задачу.
        assert len(captured_dispatch) == 1
        assert captured_dispatch[0]["target_server_id"] == srv_present.id


# ── DELETE /{id} ─────────────────────────────────────────────────────────────

class TestDeleteAccount:
    async def test_admin_deletes(
        self, client, admin_role_token_a, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id)
        resp = await client.delete(f"{BASE}/{acc.id}", headers=_hdr(admin_role_token_a))
        assert resp.status_code == 200

    async def test_operator_cannot_delete(
        self, client, operator_token_a, make_server, make_account,
    ):
        """operator не имеет default `delete` на server_account."""
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id)
        resp = await client.delete(f"{BASE}/{acc.id}", headers=_hdr(operator_token_a))
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_reader_cannot_delete(
        self, client, reader_token_a, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id)
        resp = await client.delete(f"{BASE}/{acc.id}", headers=_hdr(reader_token_a))
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_cross_dept_returns_404_hidden(
        self, client, admin_role_token_a, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_b")
        acc = await make_account(server_id=srv.id)
        resp = await client.delete(f"{BASE}/{acc.id}", headers=_hdr(admin_role_token_a))
        assert_error(resp, 404, "ACCOUNT_NOT_FOUND")

    async def test_nonexistent_returns_404(self, client, admin_token):
        resp = await client.delete(f"{BASE}/acc_ghost", headers=_hdr(admin_token))
        assert_error(resp, 404, "ACCOUNT_NOT_FOUND")


# ── POST /{id}/rotate_password ──────────────────────────────────────────────

class TestRotatePassword:
    async def test_operator_rotates(
        self, client, operator_token_a, make_server, make_account, db,
    ):
        from src.models import ServerAccount
        from src.services import secrets_service
        from sqlalchemy import select

        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, password="old-secret")
        resp = await client.post(
            f"{BASE}/{acc.id}/rotate_password",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == acc.id
        assert body["login"] == acc.login
        assert "rotated_at" in body
        # Plaintext не светится в ответе.
        assert "password" not in body
        assert "old-secret" not in resp.text

        row = (await db.execute(
            select(ServerAccount).where(ServerAccount.id == acc.id)
        )).scalar_one()
        new_plain = secrets_service.decrypt(
            row.password_encrypted,
            aad=secrets_service.aad_for_server_account_password(row.id),
        )
        assert new_plain != "old-secret"
        assert len(new_plain) >= 16
        assert row.password_rotated_at is not None

    async def test_reader_cannot_rotate(
        self, client, reader_token_a, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id)
        resp = await client.post(
            f"{BASE}/{acc.id}/rotate_password",
            headers=_hdr(reader_token_a),
        )
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_cross_dept_returns_404_hidden(
        self, client, operator_token_a, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_b")
        acc = await make_account(server_id=srv.id)
        resp = await client.post(
            f"{BASE}/{acc.id}/rotate_password",
            headers=_hdr(operator_token_a),
        )
        assert_error(resp, 404, "ACCOUNT_NOT_FOUND")

    async def test_nonexistent_returns_404(self, client, operator_token_a):
        resp = await client.post(
            f"{BASE}/acc_ghost/rotate_password",
            headers=_hdr(operator_token_a),
        )
        assert_error(resp, 404, "ACCOUNT_NOT_FOUND")

    async def test_manual_password_is_applied(
        self, client, operator_token_a, make_server, make_account, db,
    ):
        """Переданный в body пароль (проходящий политику) сохраняется как есть."""
        from src.models import ServerAccount
        from src.services import secrets_service
        from sqlalchemy import select

        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, password="old-secret1")
        resp = await client.post(
            f"{BASE}/{acc.id}/rotate_password",
            headers=_hdr(operator_token_a),
            json={"password_b64": b64("manual-rotate-7")},
        )
        assert resp.status_code == 200
        assert "manual-rotate-7" not in resp.text

        row = (await db.execute(
            select(ServerAccount).where(ServerAccount.id == acc.id)
        )).scalar_one()
        assert secrets_service.decrypt(
            row.password_encrypted,
            aad=secrets_service.aad_for_server_account_password(row.id),
        ) == "manual-rotate-7"

    async def test_no_body_falls_back_to_generation(
        self, client, operator_token_a, make_server, make_account, db,
    ):
        """Без body (и без password) пароль генерится сервером."""
        from src.models import ServerAccount
        from src.services import secrets_service
        from sqlalchemy import select

        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, password="old-secret1")
        resp = await client.post(
            f"{BASE}/{acc.id}/rotate_password",
            headers=_hdr(operator_token_a),
            json={},
        )
        assert resp.status_code == 200
        row = (await db.execute(
            select(ServerAccount).where(ServerAccount.id == acc.id)
        )).scalar_one()
        generated = secrets_service.decrypt(
            row.password_encrypted,
            aad=secrets_service.aad_for_server_account_password(row.id),
        )
        assert generated != "old-secret1"
        assert len(generated) >= 16


# ── Парольная политика на create / rotate ────────────────────────────────────

class TestPasswordPolicy:
    """create и rotate с ручным паролем гейтятся политикой: ≥8, буквы и цифры."""

    @pytest.mark.parametrize(
        "bad_password",
        ["short1", "nodigitshere", "12345678", "ab12"],
        ids=["too_short", "no_digit", "no_letter", "short_no_min"],
    )
    async def test_create_rejects_weak_password(
        self, client, admin_token, make_server, bad_password,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            BASE,
            headers=_hdr(admin_token),
            json={"server_ids": [srv.id], "login": "root", "password_b64": b64(bad_password)},
        )
        assert_error(resp, 422, "VALIDATION_ERROR")

    async def test_create_rejects_broken_base64(
        self, client, admin_token, make_server,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            BASE,
            headers=_hdr(admin_token),
            json={"server_ids": [srv.id], "login": "root", "password_b64": "!!!notb64!!!"},
        )
        assert_error(resp, 422, "VALIDATION_ERROR")

    async def test_create_accepts_compliant_password(
        self, client, admin_token, make_server,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            BASE,
            headers=_hdr(admin_token),
            json={"server_ids": [srv.id], "login": "root", "password_b64": b64("valid-pass-9")},
        )
        assert resp.status_code == 201

    async def test_create_without_password_skips_policy(
        self, client, admin_token, make_server,
    ):
        """Без password политика не применяется — сервер генерит сам."""
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            BASE,
            headers=_hdr(admin_token),
            json={"server_ids": [srv.id], "login": "deploy"},
        )
        assert resp.status_code == 201

    @pytest.mark.parametrize(
        "bad_password",
        ["short1", "nodigitshere", "12345678"],
        ids=["too_short", "no_digit", "no_letter"],
    )
    async def test_rotate_rejects_weak_password(
        self, client, operator_token_a, make_server, make_account, bad_password,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id)
        resp = await client.post(
            f"{BASE}/{acc.id}/rotate_password",
            headers=_hdr(operator_token_a),
            json={"password_b64": b64(bad_password)},
        )
        assert_error(resp, 422, "VALIDATION_ERROR")

    async def test_rotate_rejects_broken_base64(
        self, client, operator_token_a, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id)
        resp = await client.post(
            f"{BASE}/{acc.id}/rotate_password",
            headers=_hdr(operator_token_a),
            json={"password_b64": "!!!notb64!!!"},
        )
        assert_error(resp, 422, "VALIDATION_ERROR")


# ── GET /{id} — раскрытие пароля через view_password ────────────────────────

class TestGetAccountPassword:
    """Градуированное раскрытие пароля через обычный GET карточки.

    Держатель `view_password` (по дефолту только `admin` + `worker_bot`)
    получает `password_b64`; reader/operator с `view`, но без `view_password`,
    — карточку без пароля (`null`). Plaintext в audit details не пишется.
    """

    async def test_admin_with_view_password_sees_password(
        self, client, admin_role_token_a, make_server, make_account,
    ):
        import base64

        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, password="reveal-me-please")
        resp = await client.get(f"{BASE}/{acc.id}", headers=_hdr(admin_role_token_a))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["password_b64"] is not None
        decoded = base64.b64decode(body["password_b64"]).decode("utf-8")
        assert decoded == "reveal-me-please"
        # Plaintext не должен утечь в незакодированном виде.
        assert "reveal-me-please" not in body["password_b64"]
        # password_encrypted наружу не отдаётся.
        assert "password_encrypted" not in body

    async def test_worker_bot_with_view_password_sees_password(
        self, client, worker_bot_token_a, make_server, make_account,
    ):
        import base64

        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, password="worker-sees-this")
        resp = await client.get(f"{BASE}/{acc.id}", headers=_hdr(worker_bot_token_a))
        assert resp.status_code == 200
        decoded = base64.b64decode(resp.json()["password_b64"]).decode("utf-8")
        assert decoded == "worker-sees-this"

    async def test_operator_with_only_view_gets_no_password(
        self, client, operator_token_a, make_server, make_account,
    ):
        """operator держит `view`, но не `view_password` — карточка без пароля."""
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, password="hidden-from-operator")
        resp = await client.get(f"{BASE}/{acc.id}", headers=_hdr(operator_token_a))
        assert resp.status_code == 200
        body = resp.json()
        assert body["login"] == "root"
        assert body["password_b64"] is None
        assert "hidden-from-operator" not in resp.text

    async def test_reader_with_only_view_gets_no_password(
        self, client, reader_token_a, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, password="hidden-from-reader")
        resp = await client.get(f"{BASE}/{acc.id}", headers=_hdr(reader_token_a))
        assert resp.status_code == 200
        body = resp.json()
        assert body["password_b64"] is None
        assert "hidden-from-reader" not in resp.text

    async def test_account_without_stored_password_returns_null(
        self, client, admin_role_token_a, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, password=None)
        resp = await client.get(f"{BASE}/{acc.id}", headers=_hdr(admin_role_token_a))
        assert resp.status_code == 200
        assert resp.json()["password_b64"] is None

    async def test_audit_emit_on_reveal_success(
        self, client, admin_role_token_a, make_server, make_account, monkeypatch,
    ):
        """`server_account.password_revealed` пишется со status=success."""
        captured: list[dict] = []

        def fake_emit(action, actor_id=None, **kwargs):
            captured.append({"action": action, "actor_id": actor_id, **kwargs})

        import src.services.audit_service as audit_mod
        monkeypatch.setattr(audit_mod, "emit", fake_emit)
        monkeypatch.setattr(
            "src.services.server_account.audit_service.emit", fake_emit,
        )

        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, password="audit-me")
        resp = await client.get(f"{BASE}/{acc.id}", headers=_hdr(admin_role_token_a))
        assert resp.status_code == 200

        reveals = [
            e for e in captured if e["action"] == "server_account.password_revealed"
        ]
        assert len(reveals) >= 1
        success = [e for e in reveals if e.get("status") == "success"]
        assert success, f"expected success emit, got {reveals}"
        emit = success[0]
        assert emit["target_id"] == acc.id
        assert emit["target_type"] == "server_account"
        assert emit["allowed"] is True
        details = emit.get("details") or {}
        assert "audit-me" not in str(details)
        assert details.get("department_id") == "dep_a"
        assert details.get("login") == acc.login

    async def test_no_reveal_audit_when_only_view(
        self, client, reader_token_a, make_server, make_account, monkeypatch,
    ):
        """reader без view_password не должен триггерить password_revealed."""
        captured: list[dict] = []

        def fake_emit(action, actor_id=None, **kwargs):
            captured.append({"action": action, "actor_id": actor_id, **kwargs})

        import src.services.audit_service as audit_mod
        monkeypatch.setattr(audit_mod, "emit", fake_emit)
        monkeypatch.setattr(
            "src.services.server_account.audit_service.emit", fake_emit,
        )

        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, password="not-revealed")
        resp = await client.get(f"{BASE}/{acc.id}", headers=_hdr(reader_token_a))
        assert resp.status_code == 200
        assert not [
            e for e in captured if e["action"] == "server_account.password_revealed"
        ]

    async def test_decrypt_internal_error_emits_failure_audit(
        self, client, admin_role_token_a, make_server, make_account, monkeypatch,
    ):
        """Crypto-стек ронит непредвиденный exception — secrets_service.decrypt
        оборачивает в DECRYPT_INTERNAL_ERROR (500), reveal-обвязка эмитит
        `server_account.password_revealed` со status=failure и не раскрывает plaintext.
        """
        from src.core.exceptions import AppException
        from src.services import secrets_service

        def boom(*_a, **_kw):
            raise AppException(
                http_status=500,
                error_code="DECRYPT_INTERNAL_ERROR",
                message="forced crypto stack failure",
            )

        monkeypatch.setattr(secrets_service, "decrypt", boom)
        monkeypatch.setattr(secrets_service, "decrypt_with_meta", boom)
        monkeypatch.setattr(
            "src.services.server_account.secrets_service.decrypt", boom,
        )
        monkeypatch.setattr(
            "src.services.server_account.secrets_service.decrypt_with_meta", boom,
        )

        captured: list[dict] = []

        def fake_emit(action, actor_id=None, **kwargs):
            captured.append({"action": action, "actor_id": actor_id, **kwargs})

        import src.services.audit_service as audit_mod
        monkeypatch.setattr(audit_mod, "emit", fake_emit)
        monkeypatch.setattr(
            "src.services.server_account.audit_service.emit", fake_emit,
        )

        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, password="will-be-broken-internally")
        resp = await client.get(f"{BASE}/{acc.id}", headers=_hdr(admin_role_token_a))
        assert resp.status_code == 500, resp.text
        body = resp.json()
        assert body["error_code"] == "DECRYPT_INTERNAL_ERROR"
        assert "will-be-broken-internally" not in resp.text

        failures = [
            e for e in captured
            if e["action"] == "server_account.password_revealed"
            and e.get("status") == "failure"
        ]
        assert failures, captured
        assert failures[0]["details"]["reason"] == "decrypt_failed"
        # success-emit'а после fail быть не должно.
        successes = [
            e for e in captured
            if e["action"] == "server_account.password_revealed"
            and e.get("status") == "success"
        ]
        assert not successes

    async def test_first_reveal_critical_repeat_throttled_to_info(
        self, client, admin_role_token_a, make_server, make_account, monkeypatch,
    ):
        """Первый reveal в окне → CRITICAL `server_account.password_revealed`,
        повтор в том же окне → INFO `server_account.password_revealed_throttled`.
        """
        from src.services import server_account as sa_mod

        sa_mod._REVEAL_AUDIT_WINDOW.clear()

        captured: list[dict] = []

        def fake_emit(action, actor_id=None, **kwargs):
            captured.append({"action": action, "actor_id": actor_id, **kwargs})

        import src.services.audit_service as audit_mod
        monkeypatch.setattr(audit_mod, "emit", fake_emit)
        monkeypatch.setattr(
            "src.services.server_account.audit_service.emit", fake_emit,
        )

        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, password="throttle-pw")

        r1 = await client.get(f"{BASE}/{acc.id}", headers=_hdr(admin_role_token_a))
        assert r1.status_code == 200
        first = [
            e for e in captured
            if e["action"] == "server_account.password_revealed"
        ]
        assert len(first) == 1, captured
        assert first[0]["status"] == "success"
        assert first[0]["details"]["total_reveals_in_window"] == 1

        r2 = await client.get(f"{BASE}/{acc.id}", headers=_hdr(admin_role_token_a))
        assert r2.status_code == 200
        throttled = [
            e for e in captured
            if e["action"] == "server_account.password_revealed_throttled"
        ]
        assert len(throttled) == 1, captured
        assert throttled[0]["details"]["total_reveals_in_window"] == 2
        assert throttled[0]["details"]["window_seconds"] >= 1
        still_first = [
            e for e in captured
            if e["action"] == "server_account.password_revealed"
        ]
        assert len(still_first) == 1

    async def test_broken_encrypted_password_returns_422(
        self, client, admin_role_token_a, make_server, make_account, db,
    ):
        """Сломанный ciphertext + view_password → DECRYPT_FAILED (http 422)."""
        from sqlalchemy import update

        from src.models import ServerAccount

        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, password="will-be-broken")
        # Подменим ciphertext на заведомо невалидный (правильный version-префикс,
        # но nonce/ct - мусор → AEAD decrypt бросит InvalidTag → DECRYPT_FAILED).
        await db.execute(
            update(ServerAccount)
            .where(ServerAccount.id == acc.id)
            .values(password_encrypted="v2$AAAAAAAAAAAAAAAA$BBBBBBBBBBBBBBBBBBBBBB")
        )
        await db.flush()
        resp = await client.get(f"{BASE}/{acc.id}", headers=_hdr(admin_role_token_a))
        assert_error(resp, 422, "DECRYPT_FAILED")

    async def test_broken_ciphertext_invisible_to_reader(
        self, client, reader_token_a, make_server, make_account, db,
    ):
        """reader без view_password не декодирует — даже битый ciphertext не 500."""
        from sqlalchemy import update

        from src.models import ServerAccount

        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, password="will-be-broken")
        await db.execute(
            update(ServerAccount)
            .where(ServerAccount.id == acc.id)
            .values(password_encrypted="v2$AAAAAAAAAAAAAAAA$BBBBBBBBBBBBBBBBBBBBBB")
        )
        await db.flush()
        resp = await client.get(f"{BASE}/{acc.id}", headers=_hdr(reader_token_a))
        assert resp.status_code == 200
        assert resp.json()["password_b64"] is None


# ── M2M: аккаунт на нескольких серверах ──────────────────────────────────────

class TestMultiServerCreate:
    """create со списком server_ids — аккаунт привязан сразу к нескольким серверам."""

    async def test_create_links_multiple_servers(
        self, client, admin_token, make_server,
    ):
        srv1 = await make_server(department_id="dep_a")
        srv2 = await make_server(department_id="dep_a")
        resp = await client.post(
            BASE,
            headers=_hdr(admin_token),
            json={"server_ids": [srv1.id, srv2.id], "login": "shared", "password_b64": b64("shared-pwd-9")},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert set(body["server_ids"]) == {srv1.id, srv2.id}
        # Виден в листинге обоих серверов.
        for srv in (srv1, srv2):
            lst = await client.get(BASE, headers=_hdr(admin_token), params={"server_id": srv.id})
            assert "shared" in {a["login"] for a in lst.json()["items"]}

    async def test_create_dedupes_repeated_server_ids(
        self, client, admin_token, make_server,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            BASE,
            headers=_hdr(admin_token),
            json={"server_ids": [srv.id, srv.id], "login": "dd", "password_b64": b64("valid-pass-9")},
        )
        assert resp.status_code == 201
        assert resp.json()["server_ids"] == [srv.id]

    async def test_create_empty_server_ids_rejected(
        self, client, admin_token,
    ):
        resp = await client.post(
            BASE,
            headers=_hdr(admin_token),
            json={"server_ids": [], "login": "x"},
        )
        assert_error(resp, 422, "VALIDATION_ERROR")

    async def test_create_cross_dept_server_in_list_404(
        self, client, operator_token_a, make_server,
    ):
        """Если хоть один сервер чужого dept — 404, аккаунт не создаётся."""
        srv_a = await make_server(department_id="dep_a")
        srv_b = await make_server(department_id="dep_b")
        resp = await client.post(
            BASE,
            headers=_hdr(operator_token_a),
            json={"server_ids": [srv_a.id, srv_b.id], "login": "mixed"},
        )
        assert_error(resp, 404, "SERVER_NOT_FOUND")

    async def test_login_unique_per_server_across_accounts(
        self, client, admin_token, make_server, make_account,
    ):
        """Два аккаунта с одинаковым login на одном сервере — 409 (инвариант)."""
        srv = await make_server(department_id="dep_a")
        await make_account(server_id=srv.id, login="root")
        resp = await client.post(
            BASE,
            headers=_hdr(admin_token),
            json={"server_ids": [srv.id], "login": "root"},
        )
        assert_error(resp, 409, "ACCOUNT_DUPLICATE")

    async def test_same_login_different_servers_allowed(
        self, client, admin_token, make_server,
    ):
        """Один login на разных серверах — ок (это разные машины)."""
        srv1 = await make_server(department_id="dep_a")
        srv2 = await make_server(department_id="dep_a")
        r1 = await client.post(
            BASE, headers=_hdr(admin_token),
            json={"server_ids": [srv1.id], "login": "root"},
        )
        r2 = await client.post(
            BASE, headers=_hdr(admin_token),
            json={"server_ids": [srv2.id], "login": "root"},
        )
        assert r1.status_code == 201
        assert r2.status_code == 201


class TestLinkUnlinkServers:
    """POST/DELETE /server-accounts/{id}/servers."""

    async def test_link_adds_server(
        self, client, operator_token_a, make_server, make_account,
    ):
        srv1 = await make_server(department_id="dep_a")
        srv2 = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv1.id, login="ops")
        resp = await client.post(
            f"{BASE}/{acc.id}/servers",
            headers=_hdr(operator_token_a),
            json={"server_ids": [srv2.id]},
        )
        assert resp.status_code == 200, resp.text
        assert set(resp.json()["server_ids"]) == {srv1.id, srv2.id}

    async def test_link_is_idempotent(
        self, client, operator_token_a, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops")
        resp = await client.post(
            f"{BASE}/{acc.id}/servers",
            headers=_hdr(operator_token_a),
            json={"server_ids": [srv.id]},
        )
        assert resp.status_code == 200
        assert resp.json()["server_ids"] == [srv.id]

    async def test_link_cross_dept_server_404(
        self, client, operator_token_a, make_server, make_account,
    ):
        srv_a = await make_server(department_id="dep_a")
        srv_b = await make_server(department_id="dep_b")
        acc = await make_account(server_id=srv_a.id, login="ops")
        resp = await client.post(
            f"{BASE}/{acc.id}/servers",
            headers=_hdr(operator_token_a),
            json={"server_ids": [srv_b.id]},
        )
        assert_error(resp, 404, "SERVER_NOT_FOUND")

    async def test_link_login_taken_on_target_409(
        self, client, operator_token_a, make_server, make_account,
    ):
        srv1 = await make_server(department_id="dep_a")
        srv2 = await make_server(department_id="dep_a")
        # На srv2 уже есть root от другого аккаунта.
        await make_account(server_id=srv2.id, login="root")
        acc = await make_account(server_id=srv1.id, login="root")
        resp = await client.post(
            f"{BASE}/{acc.id}/servers",
            headers=_hdr(operator_token_a),
            json={"server_ids": [srv2.id]},
        )
        assert_error(resp, 409, "ACCOUNT_DUPLICATE")

    async def test_reader_cannot_link(
        self, client, reader_token_a, make_server, make_account,
    ):
        srv1 = await make_server(department_id="dep_a")
        srv2 = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv1.id)
        resp = await client.post(
            f"{BASE}/{acc.id}/servers",
            headers=_hdr(reader_token_a),
            json={"server_ids": [srv2.id]},
        )
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_unlink_removes_server(
        self, client, operator_token_a, make_server, make_account,
    ):
        srv1 = await make_server(department_id="dep_a")
        srv2 = await make_server(department_id="dep_a")
        acc = await make_account(server_ids=[srv1.id, srv2.id], login="ops")
        resp = await client.request(
            "DELETE",
            f"{BASE}/{acc.id}/servers",
            headers=_hdr(operator_token_a),
            json={"server_ids": [srv2.id]},
        )
        assert resp.status_code == 200
        assert resp.json()["server_ids"] == [srv1.id]

    async def test_unlink_last_server_409(
        self, client, operator_token_a, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops")
        resp = await client.request(
            "DELETE",
            f"{BASE}/{acc.id}/servers",
            headers=_hdr(operator_token_a),
            json={"server_ids": [srv.id]},
        )
        assert_error(resp, 409, "ACCOUNT_NO_SERVERS")

    async def test_unlink_cross_dept_account_404(
        self, client, operator_token_a, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_b")
        acc = await make_account(server_id=srv.id)
        resp = await client.request(
            "DELETE",
            f"{BASE}/{acc.id}/servers",
            headers=_hdr(operator_token_a),
            json={"server_ids": [srv.id]},
        )
        assert_error(resp, 404, "ACCOUNT_NOT_FOUND")

    async def test_unlink_unrelated_server_404(
        self, client, operator_token_a, make_server, make_account,
    ):
        """Сервер существует, но к аккаунту НЕ привязан — должно быть 404,
        не молчаливое success без изменения связок."""
        srv_linked = await make_server(department_id="dep_a")
        srv_other = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv_linked.id, login="ops")
        resp = await client.request(
            "DELETE",
            f"{BASE}/{acc.id}/servers",
            headers=_hdr(operator_token_a),
            json={"server_ids": [srv_other.id]},
        )
        body = assert_error(resp, 404, "ACCOUNT_SERVER_LINK_NOT_FOUND")
        assert srv_other.id in body.get("details", {}).get("unknown_server_ids", [])

    async def test_unlink_mixed_known_and_unknown_404(
        self, client, operator_token_a, make_server, make_account,
    ):
        """Если в payload есть и привязанный, и непривязанный server_id — 404,
        связки НЕ трогаются (атомарно)."""
        srv1 = await make_server(department_id="dep_a")
        srv2 = await make_server(department_id="dep_a")
        srv_other = await make_server(department_id="dep_a")
        acc = await make_account(server_ids=[srv1.id, srv2.id], login="ops")
        resp = await client.request(
            "DELETE",
            f"{BASE}/{acc.id}/servers",
            headers=_hdr(operator_token_a),
            json={"server_ids": [srv2.id, srv_other.id]},
        )
        assert_error(resp, 404, "ACCOUNT_SERVER_LINK_NOT_FOUND")
        # Подтверждаем, что обе исходные связки на месте — атомарность операции.
        resp_get = await client.get(
            f"{BASE}/{acc.id}", headers=_hdr(operator_token_a),
        )
        assert resp_get.status_code == 200
        assert set(resp_get.json()["server_ids"]) == {srv1.id, srv2.id}


# ── POST /{id}/adopt_from_host — принять факт-состояние хоста в БД ────────────

class TestAdoptFromHost:
    """`POST /server-accounts/{id}/adopt_from_host` — DB-only приём drift'а,
    без fan-out на серверы. Поля применяются пополевно (only-present)."""

    async def test_operator_adopts_fields_db_only_no_dispatch(
        self, client, operator_token_a, make_server, make_account, db,
        captured_dispatch,
    ):
        from sqlalchemy import select

        from src.models import ServerAccount

        srv = await make_server(department_id="dep_a")
        acc = await make_account(
            server_id=srv.id, login="postgres", has_sudo=False,
            shell="/bin/bash", unix_groups=["postgres"],
        )
        resp = await client.post(
            f"{BASE}/{acc.id}/adopt_from_host",
            headers=_hdr(operator_token_a),
            json={
                "server_id": srv.id,
                "shell": "/bin/sh",
                "unix_groups": ["wheel"],
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Применены только присутствующие поля; has_sudo не трогали.
        assert body["shell"] == "/bin/sh"
        assert body["unix_groups"] == ["wheel"]
        assert body["has_sudo"] is False
        # Ключевое отличие от PATCH: на серверы НИЧЕГО не диспатчим.
        assert captured_dispatch == []

        await db.commit()
        refreshed = (await db.execute(
            select(ServerAccount).where(ServerAccount.id == acc.id)
        )).scalar_one()
        assert refreshed.shell == "/bin/sh"
        assert refreshed.unix_groups == ["wheel"]
        assert refreshed.has_sudo is False

    async def test_adopt_has_sudo_without_grant_sudo_ok(
        self, client, operator_token_a, make_server, make_account,
        captured_dispatch,
    ):
        """adopt принимает факт с бокса — отдельного grant_sudo не требует
        (это не подъём привилегии через API, а фиксация уже-существующего
        состояния хоста). Гейт — только сам action `adopt_from_host`."""
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="ops", has_sudo=False)
        resp = await client.post(
            f"{BASE}/{acc.id}/adopt_from_host",
            headers=_hdr(operator_token_a),
            json={"server_id": srv.id, "has_sudo": True},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["has_sudo"] is True
        assert captured_dispatch == []

    async def test_reader_cannot_adopt(
        self, client, reader_token_a, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id)
        resp = await client.post(
            f"{BASE}/{acc.id}/adopt_from_host",
            headers=_hdr(reader_token_a),
            json={"server_id": srv.id, "shell": "/bin/zsh"},
        )
        assert_error(resp, 403, "PERMISSION_DENIED")

    async def test_cross_dept_returns_404_hidden(
        self, client, operator_token_a, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_b")
        acc = await make_account(server_id=srv.id)
        resp = await client.post(
            f"{BASE}/{acc.id}/adopt_from_host",
            headers=_hdr(operator_token_a),
            json={"server_id": srv.id, "shell": "/bin/zsh"},
        )
        assert_error(resp, 404, "ACCOUNT_NOT_FOUND")

    async def test_server_not_linked_returns_404(
        self, client, operator_token_a, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_a")
        other = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="lonely")
        resp = await client.post(
            f"{BASE}/{acc.id}/adopt_from_host",
            headers=_hdr(operator_token_a),
            json={"server_id": other.id, "shell": "/bin/zsh"},
        )
        assert_error(resp, 404, "ACCOUNT_NOT_FOUND")

    async def test_empty_fields_returns_422(
        self, client, operator_token_a, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="nofield")
        resp = await client.post(
            f"{BASE}/{acc.id}/adopt_from_host",
            headers=_hdr(operator_token_a),
            json={"server_id": srv.id},
        )
        assert_error(resp, 422, "NO_FIELDS_TO_ADOPT")

    async def test_invalid_unix_groups_rejected(
        self, client, operator_token_a, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, login="badgrp")
        resp = await client.post(
            f"{BASE}/{acc.id}/adopt_from_host",
            headers=_hdr(operator_token_a),
            json={"server_id": srv.id, "unix_groups": ["Bad Group!"]},
        )
        assert resp.status_code == 422

    async def test_audit_records_old_new(
        self, client, operator_token_a, make_server, make_account, monkeypatch,
    ):
        from tests._helpers import make_emit_capture

        captured = make_emit_capture(monkeypatch)
        srv = await make_server(department_id="dep_a")
        acc = await make_account(
            server_id=srv.id, login="pg", shell="/bin/bash", has_sudo=False,
        )
        resp = await client.post(
            f"{BASE}/{acc.id}/adopt_from_host",
            headers=_hdr(operator_token_a),
            json={"server_id": srv.id, "shell": "/bin/sh", "has_sudo": True},
        )
        assert resp.status_code == 200, resp.text
        adopted = [
            e for e in captured
            if e["action"] == "server_account.adopted_from_host"
            and e.get("status") == "success"
        ]
        assert len(adopted) == 1
        details = adopted[0]["details"]
        assert details["server_id"] == srv.id
        assert set(details["adopted_fields"]) == {"shell", "has_sudo"}
        assert details["changes"]["shell"] == {"old": "/bin/bash", "new": "/bin/sh"}
        assert details["changes"]["has_sudo"] == {"old": False, "new": True}

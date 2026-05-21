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


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


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
                "server_id": srv.id,
                "login": "root",
                "password": "s3cret-explicit",
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["login"] == "root"
        assert body["server_id"] == srv.id
        # Пароль никогда не должен утекать наружу.
        assert "password" not in body
        assert "password_encrypted" not in body
        assert "s3cret-explicit" not in resp.text

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
            json={"server_id": srv.id, "login": "deploy"},
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
            json={"server_id": srv.id, "login": "root"},
        )
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "PERMISSION_DENIED"

    async def test_guest_cannot_create(self, client, guest_token_a, make_server):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            BASE,
            headers=_hdr(guest_token_a),
            json={"server_id": srv.id, "login": "root"},
        )
        assert resp.status_code == 403

    async def test_no_token_returns_401(self, client, make_server):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            BASE,
            json={"server_id": srv.id, "login": "root"},
        )
        assert resp.status_code == 401

    async def test_cross_dept_server_returns_404_hidden(
        self, client, operator_token_a, make_server,
    ):
        """Сервер чужого dept — даже факт существования не должен утекать."""
        srv = await make_server(department_id="dep_b")
        resp = await client.post(
            BASE,
            headers=_hdr(operator_token_a),
            json={"server_id": srv.id, "login": "root"},
        )
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "SERVER_NOT_FOUND"

    async def test_has_sudo_without_grant_sudo_role_denied(
        self, client, operator_token_a, make_server,
    ):
        """operator имеет create, но НЕ имеет grant_sudo — 403."""
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            BASE,
            headers=_hdr(operator_token_a),
            json={"server_id": srv.id, "login": "rooty", "has_sudo": True},
        )
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "PERMISSION_DENIED"

    async def test_admin_creates_with_sudo(
        self, client, admin_role_token_a, make_server,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            BASE,
            headers=_hdr(admin_role_token_a),
            json={"server_id": srv.id, "login": "rooty", "has_sudo": True},
        )
        assert resp.status_code == 201
        assert resp.json()["has_sudo"] is True

    async def test_duplicate_login_on_same_server_conflict(
        self, client, admin_token, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_a")
        await make_account(server_id=srv.id, login="root")
        resp = await client.post(
            BASE,
            headers=_hdr(admin_token),
            json={"server_id": srv.id, "login": "root"},
        )
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "ACCOUNT_DUPLICATE"

    async def test_nonexistent_server_returns_404(
        self, client, admin_token,
    ):
        resp = await client.post(
            BASE,
            headers=_hdr(admin_token),
            json={"server_id": "srv_ghost", "login": "root"},
        )
        assert resp.status_code == 404

    async def test_valid_login_accepted(
        self, client, admin_token, make_server,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            BASE,
            headers=_hdr(admin_token),
            json={"server_id": srv.id, "login": "root"},
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
            json={"server_id": srv.id, "login": bad_login},
        )
        assert resp.status_code == 422


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
        assert resp.status_code == 404

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
        assert resp.status_code == 422

    async def test_no_role_user_returns_403(
        self, client, no_role_token_a, make_server,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.get(
            BASE, headers=_hdr(no_role_token_a), params={"server_id": srv.id},
        )
        assert resp.status_code == 403


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
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "ACCOUNT_NOT_FOUND"

    async def test_nonexistent_id_returns_404(self, client, reader_token_a):
        resp = await client.get(f"{BASE}/acc_ghost", headers=_hdr(reader_token_a))
        assert resp.status_code == 404

    async def test_no_role_returns_403(
        self, client, no_role_token_a, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id)
        resp = await client.get(f"{BASE}/{acc.id}", headers=_hdr(no_role_token_a))
        assert resp.status_code == 403


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
            json={"unix_groups": ["wheel", "docker"], "shell": "/bin/zsh"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["unix_groups"] == ["wheel", "docker"]
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
        assert resp.status_code == 403

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
        assert resp.status_code == 404

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
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "PERMISSION_DENIED"

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
        assert resp.status_code == 403

    async def test_reader_cannot_delete(
        self, client, reader_token_a, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id)
        resp = await client.delete(f"{BASE}/{acc.id}", headers=_hdr(reader_token_a))
        assert resp.status_code == 403

    async def test_cross_dept_returns_404_hidden(
        self, client, admin_role_token_a, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_b")
        acc = await make_account(server_id=srv.id)
        resp = await client.delete(f"{BASE}/{acc.id}", headers=_hdr(admin_role_token_a))
        assert resp.status_code == 404

    async def test_nonexistent_returns_404(self, client, admin_token):
        resp = await client.delete(f"{BASE}/acc_ghost", headers=_hdr(admin_token))
        assert resp.status_code == 404


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
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "PERMISSION_DENIED"

    async def test_cross_dept_returns_404_hidden(
        self, client, operator_token_a, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_b")
        acc = await make_account(server_id=srv.id)
        resp = await client.post(
            f"{BASE}/{acc.id}/rotate_password",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 404

    async def test_nonexistent_returns_404(self, client, operator_token_a):
        resp = await client.post(
            f"{BASE}/acc_ghost/rotate_password",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 404


# ── POST /{id}/reveal-password ──────────────────────────────────────────────

class TestRevealPassword:
    """Расшифровка пароля в base64 для UI/CLI.

    По дефолту имеют доступ только `admin` и `operator`. Сам plaintext
    в audit details никогда не пишется — `redaction` затирает password-like
    ключи, а наш emit и так не передаёт plaintext в `details`.
    """

    async def test_operator_reveals(
        self, client, operator_token_a, make_server, make_account,
    ):
        import base64

        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, password="reveal-me-please")
        resp = await client.post(
            f"{BASE}/{acc.id}/reveal-password",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert set(body.keys()) == {"password_b64"}
        decoded = base64.b64decode(body["password_b64"]).decode("utf-8")
        assert decoded == "reveal-me-please"
        # Plaintext не должен утечь в незакодированном виде в response.
        assert "reveal-me-please" not in body["password_b64"]

    async def test_admin_reveals(
        self, client, admin_role_token_a, make_server, make_account,
    ):
        import base64

        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, password="admin-pass-42")
        resp = await client.post(
            f"{BASE}/{acc.id}/reveal-password",
            headers=_hdr(admin_role_token_a),
        )
        assert resp.status_code == 200
        decoded = base64.b64decode(resp.json()["password_b64"]).decode("utf-8")
        assert decoded == "admin-pass-42"

    async def test_reader_cannot_reveal(
        self, client, reader_token_a, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id)
        resp = await client.post(
            f"{BASE}/{acc.id}/reveal-password",
            headers=_hdr(reader_token_a),
        )
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "PERMISSION_DENIED"

    async def test_cross_dept_returns_404_hidden(
        self, client, operator_token_a, make_server, make_account,
    ):
        srv = await make_server(department_id="dep_b")
        acc = await make_account(server_id=srv.id, password="other-dept-secret")
        resp = await client.post(
            f"{BASE}/{acc.id}/reveal-password",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "ACCOUNT_NOT_FOUND"

    async def test_nonexistent_returns_404(self, client, operator_token_a):
        resp = await client.post(
            f"{BASE}/acc_ghost/reveal-password",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 404

    async def test_audit_emit_on_success(
        self, client, operator_token_a, make_server, make_account, monkeypatch,
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
        resp = await client.post(
            f"{BASE}/{acc.id}/reveal-password",
            headers=_hdr(operator_token_a),
        )
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
        # Plaintext в details не должен утечь даже под redaction'ом — мы и не
        # кладём его, проверяем явно.
        details = emit.get("details") or {}
        assert "audit-me" not in str(details)
        assert details.get("department_id") == "dep_a"
        assert details.get("login") == acc.login

    async def test_broken_encrypted_password_returns_500(
        self, client, operator_token_a, make_server, make_account, db,
    ):
        """Сломанный ciphertext → AppException DECRYPT_FAILED (http 500)."""
        from sqlalchemy import update

        from src.models import ServerAccount

        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, password="will-be-broken")
        # Подменим ciphertext на заведомо невалидный (правильный version-префикс,
        # но nonce/ct - мусор → AEAD decrypt бросит InvalidTag → DECRYPT_FAILED).
        await db.execute(
            update(ServerAccount)
            .where(ServerAccount.id == acc.id)
            .values(password_encrypted="v1$AAAAAAAAAAAAAAAA$BBBBBBBBBBBBBBBBBBBBBB")
        )
        await db.flush()
        resp = await client.post(
            f"{BASE}/{acc.id}/reveal-password",
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 500
        assert resp.json()["error_code"] == "DECRYPT_FAILED"

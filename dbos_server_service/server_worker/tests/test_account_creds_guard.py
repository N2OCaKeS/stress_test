"""`_account_creds(is_managed=True, login=None)` — guard от TypeError.

До правки: при `is_managed=True` и пустом / None login'е `_account_creds`
возвращал `creds = {"login": None}` (либо ходил в server_service по
не-managed ветке через `else`). Дальше `ssh_client.modify_user(login=None)`
вызывал `_validate_login(None)` → `_LOGIN_RE.match(None)` → TypeError —
voodoo для оператора.

Сейчас отбиваем сразу `SshError(SSH_INVALID_ARG)` со стабильным
error_code'ом.
"""

from __future__ import annotations

import pytest

from src.clients.ssh import SshError
from src.tasks.users import _account_creds


class TestAccountCredsGuard:
    async def test_managed_without_login_raises_ssh_invalid_arg(self, monkeypatch):
        """`is_managed=True, login=None` → `SshError(SSH_INVALID_ARG)`."""
        # fetch_account_password не должен дёргаться — guard срабатывает раньше.
        async def fail_fetch(*a, **kw):
            raise AssertionError(
                "fetch_account_password не должен вызываться для managed"
            )

        monkeypatch.setattr(
            "src.tasks.users.server_service_client.fetch_account_password",
            fail_fetch,
        )

        with pytest.raises(SshError) as ei:
            await _account_creds(
                {"is_managed": True, "login": None},
                "srv_1", "acc_1", target_dept=None,
            )
        assert ei.value.error_code == "SSH_INVALID_ARG"

    async def test_managed_with_empty_login_raises(self, monkeypatch):
        async def fail_fetch(*a, **kw):
            raise AssertionError("should not be called")

        monkeypatch.setattr(
            "src.tasks.users.server_service_client.fetch_account_password",
            fail_fetch,
        )

        with pytest.raises(SshError) as ei:
            await _account_creds(
                {"is_managed": True, "login": ""},
                "srv_1", "acc_1", target_dept=None,
            )
        assert ei.value.error_code == "SSH_INVALID_ARG"

    async def test_managed_with_login_returns_managed_creds(self, monkeypatch):
        """Happy path: `is_managed=True, login='ops'` → managed-сессия без пароля."""
        async def fail_fetch(*a, **kw):
            raise AssertionError("managed-ветка не должна тянуть пароль")

        monkeypatch.setattr(
            "src.tasks.users.server_service_client.fetch_account_password",
            fail_fetch,
        )

        creds = await _account_creds(
            {"is_managed": True, "login": "ops"},
            "srv_1", "acc_1", target_dept=None,
        )
        assert creds["login"] == "ops"
        assert "password" not in creds

    async def test_self_session_fetches_password(self, monkeypatch):
        """`is_managed=False` → fetch_account_password (пароль обязателен)."""
        fetched: list = []

        async def ok_fetch(server_id, account_id, target_dept):
            fetched.append((server_id, account_id))
            return {"login": "ops", "password": "secret"}

        monkeypatch.setattr(
            "src.tasks.users.server_service_client.fetch_account_password",
            ok_fetch,
        )

        creds = await _account_creds(
            {"is_managed": False, "login": None},
            "srv_2", "acc_2", target_dept=None,
        )
        assert creds["login"] == "ops"
        assert creds["password"] == "secret"
        assert fetched == [("srv_2", "acc_2")]

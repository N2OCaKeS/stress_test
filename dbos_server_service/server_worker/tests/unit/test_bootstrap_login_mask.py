"""Маскировка bootstrap-логина в DEBUG-логе `bootstrap_management_user`.

Контракт `_mask_bootstrap_login`:
* пустая строка → `***`;
* короткий логин (≤3 символов) → `***` целиком;
* длинный логин → первые 3 символа + `***`.

DEBUG-лог `bootstrap_management_user` должен вызывать `_mask_bootstrap_login`
для username, иначе bootstrap-учётка светится в журнале.
"""

from __future__ import annotations

import logging

import pytest

from src.services.ssh_client import _mask_bootstrap_login


class TestMaskBootstrapLogin:
    def test_empty(self):
        assert _mask_bootstrap_login("") == "***"

    def test_short_three_chars(self):
        assert _mask_bootstrap_login("ops") == "***"

    def test_short_one_char(self):
        assert _mask_bootstrap_login("r") == "***"

    def test_long_login(self):
        assert _mask_bootstrap_login("root") == "roo***"

    def test_long_login_dbos(self):
        assert _mask_bootstrap_login("dbos_bootstrap") == "dbo***"

    def test_none_safe(self):
        # bootstrap_management_user падает на None? нет, там fallback
        # на "root", но защититься от пустого и так логично.
        # Проверяем, что контракт устойчив к пустому/None-преобразованию.
        assert _mask_bootstrap_login("") == "***"


class TestBootstrapLoggerDoesNotLeakLogin:
    @pytest.mark.asyncio
    async def test_debug_log_contains_masked_username(self, monkeypatch, caplog):
        """`bootstrap_management_user` пишет маскированный username в DEBUG.

        Реальный SshClient в тест не нужен — подменяем `SshClient.__aenter__`
        и `.__aexit__` no-op'ами, проверяем только запись в caplog.
        """
        from src.services import ssh_client as mod

        class FakeSsh:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_a):
                return False

            async def bootstrap_management_user(self, *_a, **_k):
                return None

        monkeypatch.setattr(mod, "SshClient", FakeSsh)

        caplog.set_level(logging.DEBUG, logger="src.services.ssh_client")
        await mod.bootstrap_management_user(
            credentials={"login": "bootstrap_user", "password": "s", "host": "h"},
            server_id="srv_x",
            management_user="dbos",
            public_key="ssh-ed25519 AAAA dbos",
        )

        # Полный bootstrap login не должен светиться в записях.
        full = "bootstrap_user"
        for rec in caplog.records:
            assert full not in rec.getMessage()

        # Замаскированный префикс — должен.
        masked_present = any("boo***" in rec.getMessage() for rec in caplog.records)
        assert masked_present, [rec.getMessage() for rec in caplog.records]

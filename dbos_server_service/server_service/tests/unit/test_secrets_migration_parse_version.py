"""Юнит-тест на `_parse_version` из `secrets_migration_service.py`.

Функция достаёт `N` из префикса `v<N>$...` зашифрованного токена.
Edge case `"v0$..."` валиден по regex и возвращает 0 — это не падение,
но семантически нулевая версия ключа в проекте не используется. Фиксируем
текущее поведение тестом, чтобы любое будущее изменение (исключить 0,
поднимать ошибку) ловилось явно.
"""

from __future__ import annotations

from src.services.secrets_migration_service import _parse_version


class TestParseVersion:
    def test_v1_prefix(self):
        assert _parse_version("v1$nonce$ct") == 1

    def test_v2_prefix(self):
        assert _parse_version("v2$nonce$ct") == 2

    def test_multidigit_version(self):
        assert _parse_version("v42$nonce$ct") == 42

    def test_zero_version_accepted(self):
        # `v0$...` regex'у не противоречит — фиксируем, что функция возвращает 0,
        # а не None. Если поведение изменится (нулевую версию отсечь),
        # тест надо обновлять явно, а не молча.
        assert _parse_version("v0$nonce$ct") == 0

    def test_none_input(self):
        assert _parse_version(None) is None

    def test_empty_string(self):
        assert _parse_version("") is None

    def test_missing_prefix(self):
        assert _parse_version("nonce$ct") is None

    def test_no_dollar(self):
        assert _parse_version("v1nonce") is None

    def test_non_numeric(self):
        assert _parse_version("vXYZ$nonce$ct") is None

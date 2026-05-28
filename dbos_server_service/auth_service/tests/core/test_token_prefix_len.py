"""Юнит-тесты: `TOKEN_PREFIX_LEN` применён в `generate_pat` / `generate_bot_token`.

Раньше `raw[:12]` дублировался магическим числом в двух местах.
Теперь длина — именованная константа.
"""

from src.core import constants, security


def test_token_prefix_len_constant_is_12():
    assert constants.TOKEN_PREFIX_LEN == 12


def test_generate_pat_prefix_length_matches_constant():
    _, prefix, _ = security.generate_pat()
    assert len(prefix) == constants.TOKEN_PREFIX_LEN


def test_generate_bot_token_prefix_length_matches_constant():
    _, prefix, _ = security.generate_bot_token()
    assert len(prefix) == constants.TOKEN_PREFIX_LEN


def test_generate_pat_prefix_respects_runtime_constant(monkeypatch):
    """Если кто-то поменяет константу — длина префикса должна последовать.
    Защита от регрессии «hard-coded 12 вернулся в `security.py`».
    """
    monkeypatch.setattr(security, "TOKEN_PREFIX_LEN", 8)
    raw, prefix, _ = security.generate_pat()
    assert prefix == raw[:8]


def test_generate_bot_token_prefix_respects_runtime_constant(monkeypatch):
    monkeypatch.setattr(security, "TOKEN_PREFIX_LEN", 8)
    raw, prefix, _ = security.generate_bot_token()
    assert prefix == raw[:8]

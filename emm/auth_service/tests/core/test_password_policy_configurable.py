"""Настраиваемая парольная политика логина — процессный кэш + env-контракт.

`apply_policy`/`current_policy` меняют модульный кэш `_ACTIVE`, а синхронные
`is_compliant`/`validate_password` читают его. Проверяем, что дефолт совпадает
со старым поведением (12 + буква + цифра), а разные настройки реально меняют
вердикт валидатора. Плюс контракт env-полей (`AUTH_PASSWORD_POLICY_*`).

Кэш глобален на модуль — каждый тест восстанавливает исходную политику после
себя, иначе мутация протекла бы в другие тесты процесса.
"""

from __future__ import annotations

import pytest

from src.core.password_policy import (
    apply_policy,
    current_policy,
    is_compliant,
    validate_password,
)


@pytest.fixture(autouse=True)
def _restore_policy():
    saved = current_policy()
    yield
    apply_policy(saved)


def test_default_cache_matches_legacy_behaviour():
    assert current_policy() == {
        "min_length": 12,
        "require_letter": True,
        "require_digit": True,
    }
    assert is_compliant("aaaaaaaaaaa1") is True   # 12, буква + цифра
    assert is_compliant("aaaaaaaaaaaa") is False   # нет цифры
    assert is_compliant("123456789012") is False   # нет буквы
    assert is_compliant("abc123") is False         # короче 12


def test_apply_policy_relaxes_verdict():
    apply_policy({"min_length": 4, "require_letter": False, "require_digit": False})
    assert current_policy() == {
        "min_length": 4,
        "require_letter": False,
        "require_digit": False,
    }
    assert is_compliant("abcd") is True    # 4 символа, без требований
    assert is_compliant("a") is False      # короче min_length
    # validate_password возвращает значение при успехе и бросает при нарушении
    assert validate_password("abcd") == "abcd"
    with pytest.raises(ValueError):
        validate_password("a")


def test_apply_policy_tightens_min_length():
    apply_policy({"min_length": 20, "require_letter": True, "require_digit": True})
    assert is_compliant("Short1") is False
    assert is_compliant("abcdefghijklmno12345") is True  # 20, буква + цифра


def test_env_policy_fields_defined():
    """Начальная политика читается из env-алиасов с дефолтами 12/true/true."""
    from src.core.config import Settings

    fields = Settings.model_fields
    ml = fields["auth_password_policy_min_length"]
    assert ml.default == 12 and ml.alias == "AUTH_PASSWORD_POLICY_MIN_LENGTH"
    rl = fields["auth_password_policy_require_letter"]
    assert rl.default is True and rl.alias == "AUTH_PASSWORD_POLICY_REQUIRE_LETTER"
    rd = fields["auth_password_policy_require_digit"]
    assert rd.default is True and rd.alias == "AUTH_PASSWORD_POLICY_REQUIRE_DIGIT"

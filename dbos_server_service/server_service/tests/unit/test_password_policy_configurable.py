"""Настраиваемая базовая парольная политика — процессный кэш.

`apply_policy` / `current_policy` меняют модульный кэш, а синхронные
`is_compliant` / `validate_password` читают его. Проверяем, что дефолт кэша
совпадает со старым поведением (8 + буква + цифра), а разные настройки реально
меняют вердикт валидатора и текст ошибки.

Кэш глобален на модуль, поэтому каждый тест восстанавливает исходную политику
после себя — иначе мутация протекла бы в другие тесты процесса.
"""

from __future__ import annotations

import pytest
from pydantic_core import PydanticCustomError

from src.core import password_policy
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
        "min_length": 8,
        "require_letter": True,
        "require_digit": True,
    }
    # Старое поведение: 8+ с буквой и цифрой проходит, без цифры/буквы — нет.
    assert is_compliant("abcd1234") is True
    assert is_compliant("abcdefgh") is False
    assert is_compliant("12345678") is False
    assert is_compliant("short1") is False


def test_apply_shorter_length_relaxes_check():
    apply_policy({"min_length": 4, "require_letter": True, "require_digit": True})
    assert is_compliant("ab12") is True
    assert is_compliant("a1") is False


def test_apply_longer_length_tightens_check():
    apply_policy({"min_length": 12, "require_letter": True, "require_digit": True})
    assert is_compliant("abcd1234") is False
    assert is_compliant("abcdefgh1234") is True


def test_require_flags_off_allow_single_class():
    apply_policy({"min_length": 6, "require_letter": False, "require_digit": False})
    assert is_compliant("aaaaaa") is True
    assert is_compliant("111111") is True
    assert is_compliant("aaaa") is False  # длина всё ещё enforce'ится


def test_require_letter_only():
    apply_policy({"min_length": 8, "require_letter": True, "require_digit": False})
    assert is_compliant("abcdefgh") is True
    assert is_compliant("12345678") is False


def test_require_digit_only():
    apply_policy({"min_length": 8, "require_letter": False, "require_digit": True})
    assert is_compliant("12345678") is True
    assert is_compliant("abcdefgh") is False


def test_apply_coerces_types():
    # Значения из БД приходят как str/int/bool — apply приводит к нужным типам.
    apply_policy({"min_length": "10", "require_letter": 0, "require_digit": 1})
    policy = current_policy()
    assert policy == {"min_length": 10, "require_letter": False, "require_digit": True}


def test_validate_message_reflects_active_policy():
    apply_policy({"min_length": 20, "require_letter": True, "require_digit": False})
    with pytest.raises(PydanticCustomError) as exc:
        validate_password("short")
    assert exc.value.type == "WEAK_PASSWORD"
    message = str(exc.value)
    assert "at least 20 characters" in message
    assert "a letter" in message
    assert "digits" not in message


def test_current_policy_returns_copy():
    snapshot = current_policy()
    snapshot["min_length"] = 999
    assert current_policy()["min_length"] != 999


def test_strong_policy_untouched_by_config():
    # Усиленная политика не читает кэш — остаётся 16 + три класса.
    apply_policy({"min_length": 4, "require_letter": False, "require_digit": False})
    assert password_policy.is_strong("ab12") is False
    assert password_policy.is_strong("Aa1!Aa1!Aa1!Aa1!") is True

"""Парольная политика: ≥8 символов, обязательно буквы и цифры.

Один валидатор переиспользуется на ручном вводе пароля в create/rotate
для server-аккаунтов и IPMI-credentials. Автогенерированные секреты
(`secrets.token_urlsafe`) обязаны проходить политику by design.
"""

from __future__ import annotations

import secrets

import pytest

from pydantic_core import PydanticCustomError

from src.core.password_policy import (
    is_compliant,
    is_strong,
    validate_password,
    validate_strong_password,
)


@pytest.mark.parametrize(
    "password",
    ["abcd1234", "Calvin01", "p4ssword", "x" * 7 + "1", "valid-pass-9"],
)
def test_compliant_passwords_pass(password):
    assert is_compliant(password) is True
    assert validate_password(password) == password


@pytest.mark.parametrize(
    "password",
    ["short1", "1234567", "abcdefgh", "12345678", "ab12", ""],
    ids=["too_short_with_digit", "too_short_digits", "letters_only",
         "digits_only", "short_both", "empty"],
)
def test_noncompliant_passwords_fail(password):
    assert is_compliant(password) is False
    with pytest.raises(ValueError):
        validate_password(password)


def test_generated_tokens_meet_length_floor():
    """token_urlsafe(32) всегда длиннее минимума политики.

    32 байта энтропии дают ~43 base64url-символа, поэтому длина никогда не
    упирается в минимум. Наличие буквы и цифры — статистически почти всегда,
    но не гарантировано теорией, поэтому строгий пер-токен assert не делаем.
    """
    for _ in range(50):
        assert len(secrets.token_urlsafe(32)) >= 8


@pytest.mark.parametrize(
    "password",
    [
        "Aa1!Aa1!Aa1!Aa1!",          # 16, all four classes
        "Boot1234!StrongPwd",        # 18
        "P@ssw0rd-with-len-32-or-so",
    ],
)
def test_strong_passwords_pass(password):
    assert is_strong(password) is True
    assert validate_strong_password(password) == password


@pytest.mark.parametrize(
    "password",
    [
        "Aa1!Aa1!Aa1!Aa1",     # 15 chars, otherwise compliant
        "1234567890!@#$%^",    # no letter
        "AbcdEfghIjkl!@#$",    # no digit
        "Abcd1234Efgh5678",    # no symbol
        "",
        "Boot1234!Short",      # 14 chars
    ],
    ids=["too_short_15", "no_letter", "no_digit", "no_symbol", "empty",
         "short_with_all_classes"],
)
def test_strong_policy_rejects(password):
    assert is_strong(password) is False
    with pytest.raises(PydanticCustomError) as exc_info:
        validate_strong_password(password)
    assert exc_info.value.type == "WEAK_PASSWORD"


def test_generated_tokens_are_overwhelmingly_compliant():
    """Подавляющее большинство сгенерённых токенов проходит политику.

    Допускаем единичные выпадения набора без цифры (вероятность мизерная),
    но если их много — генератор или политика разошлись.
    """
    sample = [secrets.token_urlsafe(32) for _ in range(200)]
    compliant = sum(is_compliant(s) for s in sample)
    assert compliant >= 195

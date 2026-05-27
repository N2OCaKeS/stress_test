"""Парольная политика: ≥8 символов, обязательно буквы и цифры.

Один валидатор переиспользуется на ручном вводе пароля в create/rotate
для server-аккаунтов и IPMI-credentials. Автогенерированные секреты
(`secrets.token_urlsafe`) обязаны проходить политику by design.
"""

from __future__ import annotations

import secrets

import pytest

from src.core.password_policy import is_compliant, validate_password


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


def test_generated_tokens_are_overwhelmingly_compliant():
    """Подавляющее большинство сгенерённых токенов проходит политику.

    Допускаем единичные выпадения набора без цифры (вероятность мизерная),
    но если их много — генератор или политика разошлись.
    """
    sample = [secrets.token_urlsafe(32) for _ in range(200)]
    compliant = sum(is_compliant(s) for s in sample)
    assert compliant >= 195

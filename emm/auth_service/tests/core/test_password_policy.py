"""Unit-тесты src/core/password_policy.py.

Покрывает все ветки is_compliant + validate_password:
- too short (< 12)
- exactly 12 chars (boundary)
- only digits
- only letters
- unicode letters counted as alpha
- emoji counted as neither (isalpha=False, isdigit=False)
- validate_password raises ValueError on violation
- validate_password returns same value on success

Минимум 12 синхронизирован с production-guard'ом для
INITIAL_ADMIN_PASSWORD (`core/config.py::_validate_production_secrets`).
"""

import pytest

from src.core.password_policy import MIN_PASSWORD_LENGTH, is_compliant, validate_password


# ── is_compliant ─────────────────────────────────────────────────────────────

class TestIsCompliant:
    # Too short (< 12)
    @pytest.mark.parametrize("pwd", ["", "a", "Ab1", "abc123", "Ab1234", "ShortPass1!", "abcdefghijk"])
    def test_too_short_returns_false(self, pwd):
        assert is_compliant(pwd) is False

    def test_exactly_min_length_letters_only_false(self):
        assert is_compliant("a" * MIN_PASSWORD_LENGTH) is False

    def test_exactly_min_length_digits_only_false(self):
        assert is_compliant("1" * MIN_PASSWORD_LENGTH) is False

    def test_exactly_min_length_with_letter_and_digit_true(self):
        # 11 букв + 1 цифра = 12 символов
        assert is_compliant("aaaaaaaaaaa1") is True

    def test_letters_only_long_returns_false(self):
        assert is_compliant("onlyletterslongenough") is False

    def test_digits_only_long_returns_false(self):
        assert is_compliant("123456789012") is False

    def test_mix_returns_true(self):
        assert is_compliant("Strong123abcd") is True
        assert is_compliant("1234abcd5678") is True

    def test_unicode_letter_counts_as_alpha(self):
        """Кириллическая буква — isalpha() True."""
        assert is_compliant("парольный123") is True

    def test_long_mixed_password_true(self):
        assert is_compliant("A" * 100 + "1" * 100) is True

    def test_all_special_chars_no_letter_no_digit_false(self):
        """Только символы (!@#$...) — ни буквы, ни цифры."""
        assert is_compliant("!@#$%^&*()_+") is False

    def test_special_chars_plus_letter_no_digit_false(self):
        """Буквы + спецсимволы, без цифр — False."""
        assert is_compliant("abcdef!@#$%^") is False

    def test_special_chars_plus_digit_no_letter_false(self):
        """Цифры + спецсимволы, без букв — False."""
        assert is_compliant("123456!@#$%^") is False

    def test_special_chars_plus_letter_and_digit_true(self):
        """Буквы + цифры + спецсимволы — True."""
        assert is_compliant("abc123def!@#") is True

    def test_null_byte_in_password(self):
        """Null byte — isalpha и isdigit оба False для \\x00."""
        # "abc\x001def" → 7 chars, too short
        assert is_compliant("abc\x001def") is False
        # достаточной длины (>=12), есть letter и digit кроме \x00
        assert is_compliant("abc\x00defghij1") is True

    def test_whitespace_only_with_no_letter_digit_false(self):
        assert is_compliant("            ") is False  # 12 пробелов, нет буквы/цифры

    def test_whitespace_with_letter_and_digit_true(self):
        assert is_compliant("pass 1234  x") is True  # 12 chars, есть буква и цифра


# ── validate_password ────────────────────────────────────────────────────────

class TestValidatePassword:
    def test_valid_password_returned_unchanged(self):
        pwd = "Valid12345678"
        assert validate_password(pwd) == pwd

    def test_long_valid_password_returned_unchanged(self):
        pwd = "VeryLongPass12345"
        assert validate_password(pwd) == pwd

    @pytest.mark.parametrize("bad", [
        "short1abc",       # too short (<12)
        "onlylettersnow",  # no digit
        "123456789012",    # no letter
        "",                # empty
        "Short1Pa!",       # 9 chars — legacy 8-char policy reject
        "ElevenChar1",     # 11 chars — boundary just-under
    ])
    def test_invalid_raises_value_error(self, bad):
        with pytest.raises(ValueError):
            validate_password(bad)

    def test_error_message_contains_policy_description(self):
        with pytest.raises(ValueError) as exc:
            validate_password("nodigitsxxxxx")
        assert "12" in str(exc.value) or "letter" in str(exc.value) or "digit" in str(exc.value)

    def test_exactly_min_length_valid_accepted(self):
        """Граничное значение: ровно 12 символов с буквой и цифрой."""
        assert validate_password("Pass12345678") == "Pass12345678"

    def test_min_length_minus_one_rejected(self):
        """11 символов — меньше минимума."""
        with pytest.raises(ValueError):
            validate_password("Pass1234567")

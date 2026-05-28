"""Unit-тесты src/core/password_policy.py.

Покрывает все ветки is_compliant + validate_password:
- too short (< 8)
- exactly 8 chars (boundary)
- only digits
- only letters
- unicode letters counted as alpha
- emoji counted as neither (isalpha=False, isdigit=False)
- validate_password raises ValueError on violation
- validate_password returns same value on success
"""

import pytest

from src.core.password_policy import MIN_PASSWORD_LENGTH, is_compliant, validate_password


# ── is_compliant ─────────────────────────────────────────────────────────────

class TestIsCompliant:
    # Too short
    @pytest.mark.parametrize("pwd", ["", "a", "Ab1", "abc123", "Ab1234"])
    def test_too_short_returns_false(self, pwd):
        assert is_compliant(pwd) is False

    def test_exactly_min_length_letters_only_false(self):
        assert is_compliant("a" * MIN_PASSWORD_LENGTH) is False

    def test_exactly_min_length_digits_only_false(self):
        assert is_compliant("1" * MIN_PASSWORD_LENGTH) is False

    def test_exactly_min_length_with_letter_and_digit_true(self):
        # "aaaaaaa1" — 8 символов, есть буква и цифра
        assert is_compliant("aaaaaaa1") is True

    def test_letters_only_long_returns_false(self):
        assert is_compliant("onlyletterslong") is False

    def test_digits_only_long_returns_false(self):
        assert is_compliant("123456789") is False

    def test_mix_returns_true(self):
        assert is_compliant("Strong12") is True
        assert is_compliant("1234abcd") is True

    def test_unicode_letter_counts_as_alpha(self):
        """Кириллическая буква — isalpha() True."""
        assert is_compliant("пароль12") is True

    def test_long_mixed_password_true(self):
        assert is_compliant("A" * 100 + "1" * 100) is True

    def test_all_special_chars_no_letter_no_digit_false(self):
        """Только символы (!@#$...) — ни буквы, ни цифры."""
        assert is_compliant("!@#$%^&*") is False

    def test_special_chars_plus_letter_no_digit_false(self):
        """Буквы + спецсимволы, без цифр — False."""
        assert is_compliant("abc!@#$%") is False

    def test_special_chars_plus_digit_no_letter_false(self):
        """Цифры + спецсимволы, без букв — False."""
        assert is_compliant("123!@#$%") is False

    def test_special_chars_plus_letter_and_digit_true(self):
        """Буквы + цифры + спецсимволы — True."""
        assert is_compliant("abc123!@") is True

    def test_null_byte_in_password(self):
        """Null byte — isalpha и isdigit оба False для \x00."""
        # "abc\x001" → 5 chars, too short
        assert is_compliant("abc\x001") is False
        # достаточной длины, есть letter и digit кроме \x00
        assert is_compliant("abc\x00def1") is True

    def test_whitespace_only_with_no_letter_digit_false(self):
        assert is_compliant("        ") is False  # 8 пробелов, нет буквы/цифры

    def test_whitespace_with_letter_and_digit_true(self):
        assert is_compliant("pass 1  ") is True  # 8 chars, есть буква и цифра


# ── validate_password ────────────────────────────────────────────────────────

class TestValidatePassword:
    def test_valid_password_returned_unchanged(self):
        pwd = "Valid123"
        assert validate_password(pwd) == pwd

    def test_long_valid_password_returned_unchanged(self):
        pwd = "VeryLongPass12345"
        assert validate_password(pwd) == pwd

    @pytest.mark.parametrize("bad", [
        "short1",       # too short
        "onlyletters",  # no digit
        "12345678",     # no letter
        "",             # empty
    ])
    def test_invalid_raises_value_error(self, bad):
        with pytest.raises(ValueError):
            validate_password(bad)

    def test_error_message_contains_policy_description(self):
        with pytest.raises(ValueError) as exc:
            validate_password("nodigits")
        assert "8" in str(exc.value) or "letter" in str(exc.value) or "digit" in str(exc.value)

    def test_exactly_min_length_valid_accepted(self):
        """Граничное значение: ровно 8 символов с буквой и цифрой."""
        assert validate_password("Pass1234") == "Pass1234"

    def test_min_length_minus_one_rejected(self):
        """7 символов — меньше минимума."""
        with pytest.raises(ValueError):
            validate_password("Pass123")

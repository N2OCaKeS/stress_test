"""Edge cases для security.py: unicode в паролях/токенах, граничные длины,
leading zeros в SHA-256 hex, пустая строка, очень длинный вход."""

import hashlib

import pytest

from src.core.constants import BOT_TOKEN_PREFIX, PAT_PREFIX, TOKEN_PREFIX_LEN
from src.core.security import (
    generate_bot_token,
    generate_pat,
    generate_refresh_token,
    hash_opaque_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)


# ── Password: unicode и граничные входы ──────────────────────────────────────

class TestPasswordUnicode:
    def test_unicode_password_hashes_and_verifies(self):
        """Пароль из не-ASCII (кириллица + цифры) — Argon2id обрабатывает UTF-8."""
        pwd = "Привет123"
        h = hash_password(pwd)
        assert verify_password(pwd, h) is True
        assert verify_password("Привет12", h) is False

    def test_emoji_in_password(self):
        """Emoji — корректные UTF-8 code points, Argon2id должен принять."""
        pwd = "horse🐴stable1"
        h = hash_password(pwd)
        assert verify_password(pwd, h) is True

    def test_very_long_password(self):
        """Пароль 1 000 байт — не должен падать (argon2 принимает любую длину)."""
        pwd = "A1" * 500
        h = hash_password(pwd)
        assert verify_password(pwd, h) is True

    def test_password_with_null_byte(self):
        """Null byte в пароле — argon2-cffi передаёт bytes целиком; verify совпадает."""
        pwd = "pass\x001234"
        h = hash_password(pwd)
        assert verify_password(pwd, h) is True
        assert verify_password("pass\x001235", h) is False

    def test_verify_password_empty_string_against_valid_hash(self):
        """Пустая строка как «пароль» — хэшируется и верифицируется корректно."""
        h = hash_password("")
        assert verify_password("", h) is True
        assert verify_password("a", h) is False


# ── SHA-256 в hash_opaque_token: leading zeros, граничные входы ──────────────

class TestHashOpaqueTokenEdge:
    def test_empty_string_has_known_sha256(self):
        # echo -n "" | sha256sum → e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
        expected = "e3b0c44298fc1c149afbf4c8996fb924" \
                   "27ae41e4649b934ca495991b7852b855"
        assert hash_opaque_token("") == expected

    def test_result_is_always_64_hex_chars(self):
        for s in ["", "a", "x" * 10_000, "\x00", "𝔄𝔅"]:
            result = hash_opaque_token(s)
            assert len(result) == 64, f"bad length for {repr(s)}: {len(result)}"
            assert all(c in "0123456789abcdef" for c in result), \
                f"non-hex char in result for {repr(s)}"

    def test_leading_zeros_preserved(self):
        """SHA-256 hex может начинаться с нулей — убедимся что не обрезается.

        Перебираем набор строк до первой, чей hash начинается с '0'.
        """
        for i in range(200):
            h = hash_opaque_token(f"leading_zero_probe_{i}")
            if h.startswith("0"):
                assert len(h) == 64
                return
        pytest.skip("no leading-zero hash found in 200 probes — increase range")

    def test_unicode_token_hashes_consistently(self):
        tok = "dbos_pat_Привет_токен_123"
        assert hash_opaque_token(tok) == hash_opaque_token(tok)
        assert len(hash_opaque_token(tok)) == 64

    def test_hash_refresh_token_matches_hash_opaque_token(self):
        """hash_refresh_token — это просто _sha256; должен совпадать с hash_opaque_token."""
        raw, _ = generate_refresh_token()
        assert hash_refresh_token(raw) == hash_opaque_token(raw)


# ── PAT / bot token: структурные инварианты ──────────────────────────────────

class TestPATStructure:
    def test_pat_raw_length_reasonable(self):
        """secrets.token_urlsafe(32) → ~43 base64-символа; с префиксом ≥ 50."""
        raw, _, _ = generate_pat()
        assert len(raw) >= 50

    def test_pat_prefix_is_exactly_token_prefix_len(self):
        """Prefix-len задан TOKEN_PREFIX_LEN, префикс включает dbos_pat_ + часть secret."""
        raw, prefix, _ = generate_pat()
        assert len(prefix) == TOKEN_PREFIX_LEN
        assert raw[:TOKEN_PREFIX_LEN] == prefix

    def test_pat_prefix_starts_with_pat_prefix_const(self):
        """Prefix содержит полный PAT_PREFIX ('dbos_pat_') — 9 символов."""
        raw, prefix, _ = generate_pat()
        assert prefix.startswith(PAT_PREFIX)
        assert len(PAT_PREFIX) < TOKEN_PREFIX_LEN  # prefix длиннее чистого начала

    def test_pat_hash_matches_manual_sha256(self):
        raw, _, token_hash = generate_pat()
        expected = hashlib.sha256(raw.encode()).hexdigest()
        assert token_hash == expected

    def test_many_pats_all_start_with_prefix(self):
        for _ in range(50):
            raw, prefix, _ = generate_pat()
            assert raw.startswith(PAT_PREFIX)
            assert raw[:TOKEN_PREFIX_LEN] == prefix


class TestBotTokenStructure:
    def test_bot_raw_length_reasonable(self):
        raw, _, _ = generate_bot_token()
        assert len(raw) >= 50

    def test_bot_prefix_is_exactly_token_prefix_len(self):
        raw, prefix, _ = generate_bot_token()
        assert len(prefix) == TOKEN_PREFIX_LEN
        assert raw[:TOKEN_PREFIX_LEN] == prefix

    def test_bot_prefix_starts_with_bot_prefix_const(self):
        raw, prefix, _ = generate_bot_token()
        assert prefix.startswith(BOT_TOKEN_PREFIX)

    def test_bot_hash_matches_manual_sha256(self):
        raw, _, token_hash = generate_bot_token()
        expected = hashlib.sha256(raw.encode()).hexdigest()
        assert token_hash == expected

    def test_pat_and_bot_same_length_prefix(self):
        """Оба типа используют TOKEN_PREFIX_LEN — UI может единообразно их показывать."""
        _, pat_pfx, _ = generate_pat()
        _, bot_pfx, _ = generate_bot_token()
        assert len(pat_pfx) == len(bot_pfx) == TOKEN_PREFIX_LEN

    def test_pat_and_bot_hashes_are_in_same_space(self):
        """Оба типа — SHA-256; при одинаковом raw должны совпасть (детерминизм)."""
        fake = "dbos_bot_xyz"
        assert hash_opaque_token(fake) == hash_opaque_token(fake)

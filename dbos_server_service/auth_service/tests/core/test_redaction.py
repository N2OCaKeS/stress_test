"""Юнит-тесты: src/services/redaction.py — маскировка секретов в details."""

import pytest

from src.services.redaction import redact


# ── Маскировка по имени ключа ────────────────────────────────────────────────


class TestKeyBasedRedaction:
    def test_password_key(self):
        out = redact({"password": "secret123"})
        assert out == {"password": "<PASSWORD>"}

    def test_password_aliases(self):
        for key in ("password", "passwd", "pwd", "pass",
                    "old_password", "new_password", "current_password",
                    "confirm_password", "user_password"):
            out = redact({key: "anything"})
            assert out == {key: "<PASSWORD>"}, f"failed for {key}"

    def test_token_keys(self):
        # `refresh_token_hash` переехал в _HASH_KEYS — это хэш, не токен;
        # покрывается test_hash_keys (W18-W4 разнос).
        for key in ("token", "access_token", "refresh_token", "id_token",
                    "oauth_token", "bearer", "jwt", "jwt_token",
                    "session_token",
                    "token_plaintext", "pat_token", "bot_token"):
            out = redact({key: "abc.def.ghi"})
            assert out == {key: "<TOKEN>"}, f"failed for {key}"

    def test_secret_keys(self):
        for key in ("secret", "secret_key", "api_key", "apikey",
                    "client_secret", "private_key", "signing_key"):
            out = redact({key: "some-value"})
            assert out == {key: "<SECRET>"}, f"failed for {key}"

    def test_hash_keys(self):
        for key in ("password_hash", "hash", "token_hash", "pwd_hash"):
            out = redact({key: "$argon2id$..."})
            assert out == {key: "<HASH>"}, f"failed for {key}"

    def test_credential_keys(self):
        for key in ("credential", "credentials", "auth", "authorization"):
            out = redact({key: "Bearer xyz"})
            assert out == {key: "<CREDENTIAL>"}, f"failed for {key}"

    def test_case_insensitive_keys(self):
        out = redact({"PASSWORD": "x", "Token": "y", "Api_Key": "z"})
        assert out == {"PASSWORD": "<PASSWORD>", "Token": "<TOKEN>", "Api_Key": "<SECRET>"}

    def test_non_sensitive_keys_passthrough(self):
        out = redact({"reason": "ok", "attempts": 3, "ip": "1.2.3.4"})
        assert out == {"reason": "ok", "attempts": 3, "ip": "1.2.3.4"}


# ── Маскировка по форме значения ─────────────────────────────────────────────


class TestValueBasedRedaction:
    def test_jwt_shaped_string_masked(self):
        jwt_like = "eyJhbGciOi.eyJzdWIiOi.signaturepart"
        out = redact({"info": jwt_like})
        assert out == {"info": "<TOKEN>"}

    def test_bcrypt_value_masked(self):
        bcrypt = "$2b$12$" + "a" * 53
        out = redact({"info": bcrypt})
        assert out == {"info": "<HASH>"}

    def test_bcrypt_short_tail_masked(self):
        # Нестандартные библиотеки иногда отдают усечённый bcrypt-хвост
        # (50-52 символа). Префикс $2[aby]$ всё ещё узнаваем, не должен
        # пройти мимо sanitizer'а.
        bcrypt = "$2b$12$" + "a" * 50
        out = redact({"info": bcrypt})
        assert out == {"info": "<HASH>"}

    def test_bcrypt_long_tail_masked(self):
        # Padding-вариации до 60 символов — всё ещё bcrypt.
        bcrypt = "$2a$10$" + "b" * 60
        out = redact({"info": bcrypt})
        assert out == {"info": "<HASH>"}

    def test_bcrypt_too_short_not_masked(self):
        # 49 — за пределами окна, не bcrypt.
        not_bcrypt = "$2b$12$" + "a" * 49
        out = redact({"info": not_bcrypt})
        assert out == {"info": not_bcrypt}

    def test_argon2_value_masked(self):
        argon = "$argon2id$v=19$m=65536,t=3,p=4$..."
        out = redact({"info": argon})
        assert out == {"info": "<HASH>"}

    def test_plain_string_not_masked(self):
        out = redact({"reason": "invalid_credentials"})
        assert out == {"reason": "invalid_credentials"}

    def test_short_string_not_jwt(self):
        # Слишком короткие сегменты — не похоже на JWT
        out = redact({"info": "a.b.c"})
        assert out == {"info": "a.b.c"}

    def test_pat_opaque_token_masked(self):
        out = redact({"info": "dbos_pat_aBcD3fGhIjKlMnOp"})
        assert out == {"info": "<TOKEN>"}

    def test_bot_opaque_token_masked(self):
        out = redact({"info": "dbos_bot_xY9z8wV7uT6sR5qP"})
        assert out == {"info": "<TOKEN>"}

    def test_dbos_prefix_too_short_not_masked(self):
        # Меньше 12 символов после префикса — не пройдёт по эвристике
        out = redact({"info": "dbos_pat_abc"})
        assert out == {"info": "dbos_pat_abc"}

    def test_random_dbos_prefix_not_masked(self):
        # Префикс с неизвестным типом — не маскируем
        out = redact({"info": "dbos_unknown_aBcDeFgHiJkL"})
        assert out == {"info": "dbos_unknown_aBcDeFgHiJkL"}


# ── Рекурсия ──────────────────────────────────────────────────────────────────


class TestRecursion:
    def test_nested_dict(self):
        out = redact({"user": {"name": "ivanov", "password": "secret"}})
        assert out == {"user": {"name": "ivanov", "password": "<PASSWORD>"}}

    def test_list_of_dicts(self):
        out = redact({"items": [{"token": "a"}, {"token": "b"}]})
        assert out == {"items": [{"token": "<TOKEN>"}, {"token": "<TOKEN>"}]}

    def test_deep_nesting(self):
        out = redact({"a": {"b": {"c": {"password": "x"}}}})
        assert out == {"a": {"b": {"c": {"password": "<PASSWORD>"}}}}

    def test_password_key_with_dict_value_masked_entirely(self):
        """Если ключ говорит 'это секрет' — содержимое маскируется целиком, без обхода."""
        out = redact({"password": {"hash": "x", "salt": "y"}})
        assert out == {"password": "<PASSWORD>"}

    def test_credentials_key_with_list_value(self):
        out = redact({"credentials": ["a", "b", "c"]})
        assert out == {"credentials": "<CREDENTIAL>"}


# ── Очень длинные строки ──────────────────────────────────────────────────────


class TestLongStrings:
    def test_string_truncated_above_2048(self):
        long = "x" * 3000
        out = redact({"blob": long})
        assert out["blob"].endswith("<TRUNCATED>")
        assert len(out["blob"]) < 3000

    def test_string_at_limit_not_truncated(self):
        s = "x" * 2048
        out = redact({"blob": s})
        assert out["blob"] == s


# ── Иммутабельность ───────────────────────────────────────────────────────────


class TestImmutability:
    def test_input_not_mutated(self):
        original = {"password": "secret", "nested": {"token": "t"}}
        snapshot = {"password": "secret", "nested": {"token": "t"}}
        redact(original)
        assert original == snapshot


# ── Реальные сценарии аудита ──────────────────────────────────────────────────


class TestAuditScenarios:
    def test_login_failure_with_form_password(self):
        """Если в details случайно попал введённый пароль — маскируется."""
        out = redact({
            "reason": "invalid_credentials",
            "username": "ivanov",
            "password": "MyP@ssw0rd",  # такого быть не должно — но если есть, маскируем
            "attempts": 3,
        })
        assert out["password"] == "<PASSWORD>"
        assert out["reason"] == "invalid_credentials"
        assert out["username"] == "ivanov"
        assert out["attempts"] == 3

    def test_token_creation_event(self):
        out = redact({
            "bot_id": "bot_123",
            "token": "dbos_bot_aaaaaaaaaaaaaaaa",
            "expires_at": "2026-12-31T00:00:00Z",
        })
        assert out["token"] == "<TOKEN>"
        assert out["bot_id"] == "bot_123"

    def test_oauth_client_secret(self):
        out = redact({"client_id": "cli_42", "client_secret": "sk_live_xyz"})
        assert out == {"client_id": "cli_42", "client_secret": "<SECRET>"}

    def test_authorization_header_in_details(self):
        out = redact({"request": {"authorization": "Bearer abcd"}})
        assert out == {"request": {"authorization": "<CREDENTIAL>"}}


# ── Сигнатура: parent_key не пробрасывается наружу ─────────────────────────────


def test_redact_signature_has_no_parent_key():
    """`_parent_key` был приватным kwarg'ом для внутренней рекурсии и нигде не
    использовался — сигнатура должна остаться чистой `(payload)`.
    """
    import inspect

    sig = inspect.signature(redact)
    assert list(sig.parameters.keys()) == ["payload"], (
        f"redact signature drifted: {list(sig.parameters.keys())}"
    )


def test_redact_rejects_unknown_kwarg():
    with pytest.raises(TypeError):
        redact({"a": 1}, _parent_key="x")  # type: ignore[call-arg]

"""Паритет масок `auth_service/redaction.py` с loging-версией.

После того как loging_service добавил `bearer_token` и `api_secret` в
свои `_TOKEN_KEYS` / `_SECRET_KEYS`, auth_service оставался уязвимым:
plaintext-значение под этими ключами могло уехать в SIEM-лог через
audit-detail. Тесты фиксируют, что (а) новые composite-имена маскируются,
(б) lookup case-insensitive, (в) ранее покрытые ключи продолжают работать.
"""

from src.services import redaction
from src.services.redaction import redact


# Канонический набор маскируемых ключей auth-копии redaction.py. Копия помечена
# `# DUPE: keep in sync` (SOURCE OF TRUTH — sdk/redaction.py), но копии по
# сервисам уже намеренно разошлись: server несёт `hkdf_salt`, secret —
# `secret_b64` и т.д. Поэтому здесь пиним именно auth-копию: любое добавление
# или удаление ключа в её _*_KEYS должно осознанно отразиться в этом списке,
# иначе тест падает и заставляет проверить, что маскировка не сузилась.
# Кросс-сервисную сверку «все копии содержат общий минимум» руками тут не
# сделать — файлы соседних сервисов в auth-тест-контейнер не примонтированы;
# это остаётся repo-level CI-шагом.
_CANONICAL_PASSWORD_KEYS = {
    "password", "passwd", "pwd", "pass",
    "old_password", "new_password", "current_password",
    "confirm_password", "user_password",
}
_CANONICAL_TOKEN_KEYS = {
    "token", "access_token", "refresh_token", "id_token",
    "oauth_token", "bearer", "bearer_token", "jwt", "jwt_token",
    "session_token", "token_plaintext", "pat_token", "bot_token",
}
_CANONICAL_SECRET_KEYS = {
    "secret", "secret_key", "api_key", "apikey", "api_secret",
    "client_secret", "private_key", "signing_key",
    "service_api_key", "service_key", "introspect_key",
    "logging_service_api_key",
}
_CANONICAL_HASH_KEYS = {
    "hash", "password_hash", "token_hash", "pwd_hash",
    "refresh_token_hash", "pat_hash", "bot_token_hash",
}
_CANONICAL_CREDENTIAL_KEYS = {
    "credential", "credentials", "auth", "authorization",
}


def test_auth_copy_key_sets_match_canonical():
    """Drift-guard: набор маскируемых ключей auth-копии зафиксирован.

    Если кто-то добавил/убрал ключ в `_PASSWORD_KEYS`/`_TOKEN_KEYS`/
    `_SECRET_KEYS`/`_HASH_KEYS`/`_CREDENTIAL_KEYS` и не обновил этот список —
    тест падает. Защищает от молчаливого сужения маскировки (например удалили
    `client_secret` → plaintext поедет в audit-лог).
    """
    assert redaction._PASSWORD_KEYS == _CANONICAL_PASSWORD_KEYS
    assert redaction._TOKEN_KEYS == _CANONICAL_TOKEN_KEYS
    assert redaction._SECRET_KEYS == _CANONICAL_SECRET_KEYS
    assert redaction._HASH_KEYS == _CANONICAL_HASH_KEYS
    assert redaction._CREDENTIAL_KEYS == _CANONICAL_CREDENTIAL_KEYS


def test_uppercase_bearer_token_redacted():
    # Mixed-case match через `_classify_key` → `.lower()` lookup.
    out = redact({"BEARER_TOKEN": "eyJhbGciOiJIUzI1NiJ9.payload.sig"})
    assert out == {"BEARER_TOKEN": "<TOKEN>"}

    out_lower = redact({"bearer_token": "raw-plaintext"})
    assert out_lower == {"bearer_token": "<TOKEN>"}

    out_mixed = redact({"Bearer_Token": "raw-plaintext"})
    assert out_mixed == {"Bearer_Token": "<TOKEN>"}


def test_uppercase_api_secret_redacted():
    out = redact({"API_SECRET": "abc"})
    assert out == {"API_SECRET": "<SECRET>"}

    out_lower = redact({"api_secret": "abc"})
    assert out_lower == {"api_secret": "<SECRET>"}

    out_mixed = redact({"Api_Secret": "abc"})
    assert out_mixed == {"Api_Secret": "<SECRET>"}


def test_existing_keys_still_work():
    # Регрессия: добавление composite-ключей не сломало старые имена.
    out = redact(
        {
            "password": "p@ss",
            "access_token": "x",
            "refresh_token": "y",
            "bearer": "z",
            "jwt": "j",
            "secret": "s",
            "api_key": "k",
            "client_secret": "cs",
            "private_key": "pk",
            "password_hash": "$2b$12$" + "a" * 53,
        }
    )
    assert out["password"] == "<PASSWORD>"
    assert out["access_token"] == "<TOKEN>"
    assert out["refresh_token"] == "<TOKEN>"
    assert out["bearer"] == "<TOKEN>"
    assert out["jwt"] == "<TOKEN>"
    assert out["secret"] == "<SECRET>"
    assert out["api_key"] == "<SECRET>"
    assert out["client_secret"] == "<SECRET>"
    assert out["private_key"] == "<SECRET>"
    assert out["password_hash"] == "<HASH>"

"""Паритет масок `auth_service/redaction.py` с loging-версией.

После того как loging_service добавил `bearer_token` и `api_secret` в
свои `_TOKEN_KEYS` / `_SECRET_KEYS`, auth_service оставался уязвимым:
plaintext-значение под этими ключами могло уехать в SIEM-лог через
audit-detail. Тесты фиксируют, что (а) новые composite-имена маскируются,
(б) lookup case-insensitive, (в) ранее покрытые ключи продолжают работать.
"""

from src.services.redaction import redact


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

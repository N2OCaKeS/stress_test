"""ERROR-audit на сбоях `_reencrypt_row` в sync re-encrypt-батче.

Раньше падение decrypt/encrypt давало только `logger.warning` + метрику —
пропавший из env мастер-ключ тонул в шуме. Теперь key-missing и битый
ciphertext эмитят отдельные ERROR-события (`secrets.migration_key_missing` /
`secrets.migration_decrypt_failed`), чтобы SIEM ловил инцидент.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.core.exceptions import AppException
from src.services import secrets_migration_service
from src.services.secrets_migration_service import (
    ENTITY_SERVER_ACCOUNT,
    _reencrypt_row,
)


def _aad(_id: str) -> bytes:
    return b"aad"


@pytest.fixture
def captured_emits(monkeypatch):
    calls: list[dict] = []

    def fake_emit(action, **kwargs):
        calls.append({"action": action, **kwargs})

    monkeypatch.setattr(secrets_migration_service.audit_service, "emit", fake_emit)
    monkeypatch.setattr(
        secrets_migration_service.metrics,
        "increment_secrets_decrypt_failures",
        lambda: None,
    )
    return calls


class TestReencryptRowAudit:
    def test_success_no_audit(self, monkeypatch, captured_emits):
        """Успешный re-encrypt не эмитит failure-аудита и возвращает None."""
        monkeypatch.setattr(
            secrets_migration_service.secrets_service, "decrypt", lambda ct, aad: "plain"
        )
        monkeypatch.setattr(
            secrets_migration_service.secrets_service,
            "encrypt",
            lambda pt, aad: "v2$new",
        )
        row = SimpleNamespace(id="sa_1", password_encrypted="v1$old")
        result = _reencrypt_row(
            row, aad_fn=_aad, entity_type=ENTITY_SERVER_ACCOUNT, log_label="server_account"
        )
        assert result is None
        assert row.password_encrypted == "v2$new"
        assert captured_emits == []

    def test_key_missing_emits_dedicated_error(self, monkeypatch, captured_emits):
        """ENCRYPTION_KEY_MISSING → `secrets.migration_key_missing` failure-audit."""
        def boom(ct, aad):
            raise AppException(
                http_status=500,
                error_code="ENCRYPTION_KEY_MISSING",
                message="key gone",
            )

        monkeypatch.setattr(secrets_migration_service.secrets_service, "decrypt", boom)
        row = SimpleNamespace(id="sa_2", password_encrypted="v1$old")
        result = _reencrypt_row(
            row, aad_fn=_aad, entity_type=ENTITY_SERVER_ACCOUNT, log_label="server_account"
        )
        assert result == {
            "entity_type": ENTITY_SERVER_ACCOUNT,
            "entity_id": "sa_2",
            "error_class": "AppException",
        }
        assert len(captured_emits) == 1
        emit = captured_emits[0]
        assert emit["action"] == "secrets.migration_key_missing"
        assert emit["status"] == "failure"
        assert emit["target_id"] == "sa_2"
        assert emit["details"]["reason"] == "encryption_key_missing"
        assert emit["details"]["error_code"] == "ENCRYPTION_KEY_MISSING"

    def test_decrypt_failure_emits_generic_error(self, monkeypatch, captured_emits):
        """DECRYPT_FAILED (битый ciphertext) → `secrets.migration_decrypt_failed`."""
        def boom(ct, aad):
            raise AppException(
                http_status=422,
                error_code="DECRYPT_FAILED",
                message="bad tag",
            )

        monkeypatch.setattr(secrets_migration_service.secrets_service, "decrypt", boom)
        row = SimpleNamespace(id="sa_3", password_encrypted="v1$old")
        result = _reencrypt_row(
            row, aad_fn=_aad, entity_type=ENTITY_SERVER_ACCOUNT, log_label="server_account"
        )
        assert result["entity_id"] == "sa_3"
        assert len(captured_emits) == 1
        emit = captured_emits[0]
        assert emit["action"] == "secrets.migration_decrypt_failed"
        assert emit["status"] == "failure"
        assert emit["details"]["reason"] == "decrypt_failed"

    def test_non_app_exception_emits_generic(self, monkeypatch, captured_emits):
        """Произвольный exc без error_code попадает в generic decrypt_failed."""
        def boom(ct, aad):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(secrets_migration_service.secrets_service, "decrypt", boom)
        row = SimpleNamespace(id="sa_4", password_encrypted="v1$old")
        _reencrypt_row(
            row, aad_fn=_aad, entity_type=ENTITY_SERVER_ACCOUNT, log_label="server_account"
        )
        assert len(captured_emits) == 1
        emit = captured_emits[0]
        assert emit["action"] == "secrets.migration_decrypt_failed"
        assert emit["details"]["error_class"] == "RuntimeError"
        assert emit["details"]["error_code"] is None

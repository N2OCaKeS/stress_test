"""Unit-тесты: `redaction._PASSWORD_KEYS` покрывает bootstrap-поля.

Bootstrap-кред prepare'а (`bootstrap_login`/`bootstrap_password`) едет в
Redis отдельным каналом, не через audit. Но если в каком-нибудь будущем
debug-emit'е или error-context dict с этими ключами окажется в
`audit_service.emit`, редактор обязан их замаскировать — иначе SIEM-оператор
с `loging_reader` видит plaintext SSH-пароль онбординг-аккаунта.
"""

from __future__ import annotations

from src.services.redaction import redact


def test_bootstrap_password_masked_by_key():
    out = redact({"bootstrap_password": "open-sesame-1234"})
    assert out == {"bootstrap_password": "<PASSWORD>"}


def test_bootstrap_login_masked_by_key():
    out = redact({"bootstrap_login": "dbos-bootstrap"})
    assert out == {"bootstrap_login": "<PASSWORD>"}


def test_bootstrap_password_in_nested_audit_details_masked():
    payload = {
        "task_id": "tsk_abc",
        "details": {
            "bootstrap_login": "operator",
            "bootstrap_password": "P@ssw0rd!",
            "department_id": "dep_1",
        },
    }
    out = redact(payload)
    assert out["details"]["bootstrap_login"] == "<PASSWORD>"
    assert out["details"]["bootstrap_password"] == "<PASSWORD>"
    assert out["details"]["department_id"] == "dep_1"
    assert out["task_id"] == "tsk_abc"

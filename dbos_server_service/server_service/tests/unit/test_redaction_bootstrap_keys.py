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


def test_provision_password_plaintext_masked_by_key():
    """provision-dispatch кладёт `password_plaintext` в worker payload. Если
    кто-то добавит debug-emit с этим dict'ом — audit-trail не должен утечь.
    """
    out = redact({"password_plaintext": "g3n3rated-Str0ng!"})
    assert out == {"password_plaintext": "<PASSWORD>"}


def test_ssh_private_key_plaintext_masked_by_key():
    """`ssh_private_key_plaintext` едет в worker payload и не должен светиться
    в audit, если кто-то залогирует payload целиком.
    """
    out = redact({"ssh_private_key_plaintext": "-----BEGIN OPENSSH PRIVATE KEY-----..."})
    assert out == {"ssh_private_key_plaintext": "<SECRET>"}


def test_ssh_private_key_masked_by_key():
    out = redact({"ssh_private_key": "-----BEGIN OPENSSH PRIVATE KEY-----..."})
    assert out == {"ssh_private_key": "<SECRET>"}


def test_provision_payload_dict_masked():
    """Полный provision worker-payload: все три секрета замаскированы, нон-секрет-
    поля как login/server_id остаются нетронуты.
    """
    payload = {
        "server_id": "srv_1",
        "account_id": "acc_1",
        "login": "ops",
        "password_plaintext": "Sup3rStr0ng!Pwd",
        "ssh_public_key": "ssh-ed25519 AAAA...",
        "ssh_private_key_plaintext": "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END...",
        "force_replace": True,
    }
    out = redact(payload)
    assert out["password_plaintext"] == "<PASSWORD>"
    assert out["ssh_private_key_plaintext"] == "<SECRET>"
    assert out["server_id"] == "srv_1"
    assert out["account_id"] == "acc_1"
    assert out["login"] == "ops"
    assert out["force_replace"] is True
    # ssh_public_key маскируется в audit-payload: actor-идентификатор не должен утекать в логи.
    assert out["ssh_public_key"] == "<SECRET>"

"""Charset для `RuleCreate.match_action` и `RuleCreate.name`.

Симметрично `EventCreate.action`: CRLF / Unicode / uppercase в `match_action`
и `name` отдаются обратно в admin-list endpoint as-is и оттуда уходят в
JSON-логи / CSV. Без charset'а admin (или скомпрометированный admin-токен)
поднимает log-injection вектор через создание правила с подделанным именем.
"""

import pytest

from tests.conftest import make_rule

RULES_URL = "/api/logging/v1/rules"


class TestMatchActionCharset:
    @pytest.mark.parametrize("bad_action", [
        "user.login\r\n[ALERT] fake",
        "user.login\n[ALERT] fake",
        "User.Login",         # uppercase
        "user.login!",        # punctuation вне [a-z0-9_.*]
        "user.login​",   # zero-width space (Unicode)
        "a" * 129,            # over max length
        "",                   # empty (но allowed null — это другое поле = None)
    ])
    def test_create_rejects_bad_match_action(self, admin_client, bad_action):
        r = admin_client.post(
            RULES_URL,
            json=make_rule(name="bad-action-rule", match_action=bad_action),
        )
        assert r.status_code == 422, r.text

    def test_create_accepts_glob_star(self, admin_client):
        # `*` явно разрешён — rule-движок матчит `user.*`.
        r = admin_client.post(
            RULES_URL,
            json=make_rule(name="glob-ok", match_action="user.*"),
        )
        assert r.status_code == 201, r.text

    def test_create_accepts_dot_namespace(self, admin_client):
        r = admin_client.post(
            RULES_URL,
            json=make_rule(name="dot-ok", match_action="http.4xx_error"),
        )
        assert r.status_code == 201, r.text

    def test_create_accepts_null_match_action(self, admin_client):
        # None означает «любое значение» — должно проходить.
        r = admin_client.post(
            RULES_URL,
            json=make_rule(name="null-ok", match_action=None),
        )
        assert r.status_code == 201, r.text

    def test_update_rejects_bad_match_action(self, admin_client):
        created = admin_client.post(
            RULES_URL, json=make_rule(name="upd-charset")
        ).json()
        r = admin_client.patch(
            f"{RULES_URL}/{created['id']}",
            json={"match_action": "User.Login\r\n[FAKE]"},
        )
        assert r.status_code == 422, r.text


class TestRuleNameCharset:
    @pytest.mark.parametrize("bad_name", [
        "rule\r\n[ALERT] fake",
        "rule\nfake",
        "rule\twith-tab",
        "пр​авило",       # zero-width space
        "правило-кириллица",   # non-ASCII
        "",                    # пустое
    ])
    def test_create_rejects_bad_name(self, admin_client, bad_name):
        r = admin_client.post(
            RULES_URL,
            json=make_rule(name=bad_name),
        )
        assert r.status_code == 422, r.text

    def test_create_accepts_printable_ascii(self, admin_client):
        r = admin_client.post(
            RULES_URL,
            json=make_rule(name="suppress-login-v2"),
        )
        assert r.status_code == 201, r.text

    def test_update_rejects_bad_name(self, admin_client):
        created = admin_client.post(
            RULES_URL, json=make_rule(name="rename-target")
        ).json()
        r = admin_client.patch(
            f"{RULES_URL}/{created['id']}",
            json={"name": "renamed\r\nfake"},
        )
        assert r.status_code == 422, r.text

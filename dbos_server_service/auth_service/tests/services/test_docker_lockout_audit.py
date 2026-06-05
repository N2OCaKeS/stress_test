"""GAP-17: docker.token_issued failure reason=account_locked — аудит при bot lockout.

Тест проверяет, что при `ACCOUNT_TEMPORARILY_LOCKED` на `/docker/token`
`docker_registry_service` эмитит `docker.token_issued` с `status="failure"`,
`allowed=False`, `details.reason="account_locked"` — независимо от user- или
bot-ветки обработки запроса.

Дополнительно: аналогичный аудит для user-lockout на docker/token.
"""

import pytest

from src.services import audit_service as audit_mod
from tests._helpers.http import _basic  # noqa: F401 — общий helper

TOKEN_URL = "/api/auth/v1/docker/token"
CONFIG_URL = "/api/auth/v1/docker/registry/{dept_id}"
BOTS_URL = "/api/auth/v1/bots"


@pytest.fixture()
def capture_audit(monkeypatch):
    captured: list[dict] = []
    original = audit_mod.emit

    def _spy(action, actor_id=None, **kw):
        captured.append({"action": action, "actor_id": actor_id, **kw})
        return original(action, actor_id, **kw)

    monkeypatch.setattr(audit_mod, "emit", _spy)
    return captured


async def _enable_docker(client, token, dept_id):
    return await client.put(
        CONFIG_URL.format(dept_id=dept_id),
        headers={"Authorization": f"Bearer {token}"},
        json={"pull_policy": "all", "pull_user_ids": [], "push_user_ids": []},
    )


async def _make_bot(client, admin_token, dept_id, name):
    bot_resp = (await client.post(
        BOTS_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": name, "department_id": dept_id, "allowed_services": []},
    )).json()
    tok_resp = (await client.post(
        f"{BOTS_URL}/{bot_resp['bot_id']}/tokens",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": "tok"},
    )).json()
    return bot_resp["bot_id"], tok_resp["token"]


class TestDockerBotLockoutAudit:
    async def test_bot_lockout_emits_token_issued_failure_account_locked(
        self, client, admin_token, dept_a, capture_audit,
    ):
        """После 5 промахов bot lockout → 6-й запрос получает 429
        и `docker.token_issued failure reason=account_locked` улетает в аудит.
        """
        await _enable_docker(client, admin_token, dept_a.id)
        bot_name = "audit_lockout_bot"
        _, good_token = await _make_bot(client, admin_token, dept_a.id, bot_name)

        bad = "dbos_bot_wrong_audit_test_tok"
        for _ in range(5):
            await client.get(
                TOKEN_URL,
                headers=_basic(bot_name, bad),
                params={"service": "registry.test"},
            )

        capture_audit.clear()

        resp = await client.get(
            TOKEN_URL,
            headers=_basic(bot_name, good_token),
            params={"service": "registry.test"},
        )
        assert resp.status_code == 429, resp.text

        lockout_events = [
            e for e in capture_audit
            if e["action"] == "docker.token_issued"
            and e.get("status") == "failure"
            and e.get("allowed") is False
            and e.get("details", {}).get("reason") == "account_locked"
        ]
        assert lockout_events, (
            f"ожидался docker.token_issued failure reason=account_locked, "
            f"получили: {[e['action'] for e in capture_audit]}"
        )

        details = lockout_events[0]["details"]
        assert details["username"] == bot_name
        assert "retry_after_seconds" in details

    async def test_bot_lockout_audit_contains_username(
        self, client, admin_token, dept_a, capture_audit,
    ):
        """В деталях audit-события при lockout есть поле `username` с именем бота."""
        await _enable_docker(client, admin_token, dept_a.id)
        bot_name = "audit_username_bot"
        _, good_token = await _make_bot(client, admin_token, dept_a.id, bot_name)

        bad = "dbos_bot_bad_for_username_check"
        for _ in range(5):
            await client.get(
                TOKEN_URL,
                headers=_basic(bot_name, bad),
                params={"service": "registry.test"},
            )

        capture_audit.clear()

        resp = await client.get(
            TOKEN_URL,
            headers=_basic(bot_name, good_token),
            params={"service": "registry.test"},
        )
        assert resp.status_code == 429

        events = [
            e for e in capture_audit
            if e["action"] == "docker.token_issued" and e.get("status") == "failure"
        ]
        assert events
        assert events[0]["details"]["username"] == bot_name


class TestDockerUserLockoutAudit:
    async def test_user_lockout_emits_token_issued_failure_account_locked(
        self, client, admin_token, user_a, dept_a, capture_audit,
    ):
        """User-lockout на /docker/token тоже должен эмитить
        `docker.token_issued failure reason=account_locked`.
        """
        await _enable_docker(client, admin_token, dept_a.id)

        for _ in range(5):
            await client.get(
                TOKEN_URL,
                headers=_basic("t_user_a", "wrong_password_docker"),
                params={"service": "registry.test"},
            )

        capture_audit.clear()

        resp = await client.get(
            TOKEN_URL,
            headers=_basic("t_user_a", "User1234!"),
            params={"service": "registry.test"},
        )
        assert resp.status_code == 429, (
            f"ожидался 429 после lockout, получили {resp.status_code}: {resp.text}"
        )

        lockout_events = [
            e for e in capture_audit
            if e["action"] == "docker.token_issued"
            and e.get("status") == "failure"
            and e.get("details", {}).get("reason") == "account_locked"
        ]
        assert lockout_events, (
            f"no docker.token_issued failure account_locked in: "
            f"{[(e['action'], e.get('details')) for e in capture_audit]}"
        )

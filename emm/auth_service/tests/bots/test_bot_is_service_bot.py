"""`is_service_bot` — платформенный флаг, недоступный обычному bot CRUD API.

Флаг проставляется ТОЛЬКО bootstrap-кодом (см. `tests/services/test_bootstrap_platform.py`
для покрытия самого бутстрапа). Здесь — контракт с обычным `POST /bots`/`PATCH
/bots/{id}`: клиент никак не может выставить `is_service_bot=True` через API.
"""

from src.models import BotAccount

BOTS_URL = "/api/auth/v1/bots"


class TestBotCreateCannotSetServiceFlag:
    async def test_is_service_bot_rejected_by_schema(self, client, admin_token, dept_a):
        """`BotCreate` — `extra="forbid"`: лишнее поле в теле ловится как 422,
        а не молча дропается."""
        resp = await client.post(
            BOTS_URL,
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "name": "sneaky_bot",
                "department_id": dept_a.id,
                "allowed_services": [],
                "is_service_bot": True,
            },
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["error_code"] == "VALIDATION_ERROR"

    async def test_created_bot_defaults_to_false(self, client, admin_token, dept_a, db):
        """Обычный create без лишнего поля — бот всегда заводится с False."""
        resp = await client.post(
            BOTS_URL,
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": "plain_bot", "department_id": dept_a.id, "allowed_services": []},
        )
        assert resp.status_code == 201, resp.text
        bot_id = resp.json()["bot_id"]

        bot = await db.get(BotAccount, bot_id)
        assert bot.is_service_bot is False


class TestBotUpdateCannotSetServiceFlag:
    async def test_is_service_bot_ignored_on_patch(self, client, admin_token, dept_a, db):
        """`BotUpdate` не декларирует поле — пермиссивная схема тихо
        игнорирует его, апдейт проходит как no-op по этому полю."""
        created = await client.post(
            BOTS_URL,
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": "patch_target_bot", "department_id": dept_a.id, "allowed_services": []},
        )
        assert created.status_code == 201, created.text
        bot_id = created.json()["bot_id"]

        resp = await client.patch(
            f"{BOTS_URL}/{bot_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"description": "renamed", "is_service_bot": True},
        )
        assert resp.status_code == 200, resp.text

        bot = await db.get(BotAccount, bot_id)
        await db.refresh(bot)
        assert bot.is_service_bot is False
        assert bot.description == "renamed"

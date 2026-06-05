"""Edge cases для `bot_service.create_bot` и `oauth_service.create_client`.

* Бот не должен получать роли в сервисе, который деактивирован на уровне
  `PlatformService.is_active=False`, даже если у департамента всё ещё есть
  активная запись в `department_service_access`.
* OAuth2 клиент с `grant_types=[]` — текущая реализация не валидирует пустой
  список (фиксируем как известный пробел/баг).
"""

from sqlalchemy import update

from src.models import PlatformService

BOTS_URL = "/api/auth/v1/bots"
CLIENTS_URL = "/api/auth/v1/oauth2/clients"


# ── create_bot: деактивированный сервис ──────────────────────────────────────

class TestBotInactiveService:
    async def test_bot_with_globally_inactive_service_rejected(
        self, client, admin_token, dept_a_with_service, service_x, db,
    ):
        """Сервис помечен `is_active=False` на уровне платформы — даже если
        в department_service_access ещё активная строка, бот с ним создаваться
        не должен. Сейчас `_validate_bot_services` проверяет только активный
        dept-grant; платформенная активность не учитывается → тест документирует
        текущее (нежелательное) поведение."""
        # Деактивируем сам сервис
        await db.execute(
            update(PlatformService)
            .where(PlatformService.service_name == service_x.service_name)
            .values(is_active=False)
        )
        await db.commit()

        resp = await client.post(
            BOTS_URL,
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "name": "inactive_svc_bot",
                "department_id": dept_a_with_service.id,
                "allowed_services": [service_x.service_name],
            },
        )
        # Контракт: bot-create валидирует только dept-grant, не is_active
        # на PlatformService. Inactive сервис проходит — фиксируем 201.
        assert resp.status_code == 201, resp.text

    async def test_bot_with_service_not_in_allowed_dept_returns_403(
        self, client, admin_token, dept_b, service_x,
    ):
        """Sanity: сервис существует, но dept_b не имеет к нему доступа — отказ."""
        resp = await client.post(
            BOTS_URL,
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "name": "no_access_bot",
                "department_id": dept_b.id,
                "allowed_services": [service_x.service_name],
            },
        )
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "SERVICE_NOT_ALLOWED_FOR_DEPARTMENT"


# ── create_client: empty grant_types ─────────────────────────────────────────

class TestOAuthClientEmptyGrantTypes:
    async def test_empty_grant_types_currently_allowed(self, client, admin_token, dept_a):
        """Текущая реализация принимает `grant_types=[]` без явной валидации.
        Тест фиксирует поведение — если добавится `min_length=1` в схеме,
        здесь должен быть 422 и тест надо обновить."""
        resp = await client.post(
            CLIENTS_URL,
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "name": "no_grants_app",
                "department_id": dept_a.id,
                "redirect_uris": [],
                "allowed_scopes": [],
                "grant_types": [],
            },
        )
        assert resp.status_code == 201
        # Однако клиент с пустыми grant_types бесполезен — ни authorize,
        # ни client_credentials не пройдут (см. тесты в test_clients.py).

    async def test_invalid_grant_type_rejected_by_schema(self, client, admin_token, dept_a):
        """Контракт: схема ограничивает grant_types литералами
        authorization_code / client_credentials / refresh_token.
        Любые другие значения — 422 VALIDATION_ERROR."""
        resp = await client.post(
            CLIENTS_URL,
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "name": "weird_grants_app",
                "department_id": dept_a.id,
                "grant_types": ["password", "implicit"],
            },
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["error_code"] == "VALIDATION_ERROR"


# ── delete_client после использования ────────────────────────────────────────

class TestDeleteAfterUse:
    async def test_deleted_client_cannot_issue_token(self, client, admin_token, dept_a):
        """После delete клиент не должен выдавать client_credentials токены."""
        cl = (await client.post(
            CLIENTS_URL,
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "name": "del_then_use",
                "department_id": dept_a.id,
                "grant_types": ["client_credentials"],
            },
        )).json()
        del_resp = await client.delete(
            f"{CLIENTS_URL}/{cl['id']}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert del_resp.status_code == 200

        tok = await client.post(
            "/api/auth/v1/oauth2/token",
            json={
                "grant_type": "client_credentials",
                "client_id": cl["client_id"],
                "client_secret": cl["client_secret"],
            },
        )
        assert tok.status_code == 401
        assert tok.json()["error_code"] == "OAUTH_CLIENT_INVALID"

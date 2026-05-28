"""`subject_type` propagation: introspect → server_service → audit-event.

Контракт (см. `server_service/src/dependencies/auth.py::_to_identity` +
`server_service/src/services/audit_service.py::emit`):

* introspect возвращает `subject_type ∈ {user, bot, oauth_client}`;
* `_to_identity` кладёт его в `IdentityContext.subject_type`;
* `audit_service.emit` подхватывает его в `actor_type` audit-события,
  иначе worker-bot эмитил бы события как `user` и SIEM-correlation
  ломалась.

Проверяем: один и тот же endpoint server_service (`POST /os-versions`),
вызванный последовательно user-JWT'ом и bot-токеном — порождает два
аудит-события с разными `actor_type`.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from tests.integration.conftest import wait_for_event
from tests.integration._helpers_I_oauth import (
    BOTS_URL,
    ensure_department,
    ensure_service,
    grant_service_to_department,
    login_user,
    make_user_in_dept,
    short_id,
)

OS_VERSIONS_URL = "/api/server/v1/os-versions"
SERVER_SERVICE_NAME = "server_service"


def _create_bot_with_token(
    auth_client: httpx.Client,
    admin_token: str,
    *,
    department_id: str,
    allowed_services: list[str],
    service_roles: list[dict] | None = None,
) -> tuple[str, str]:
    """Создать бота с правами и выписать ему bot-token. Вернуть (bot_id, token_plain).

    Роли вешаем `POST /bots/{id}/roles` (BotCreate их не принимает); каждую
    роль предварительно регистрируем в `ServiceRoleDefinition`.
    """
    bot_resp = auth_client.post(
        BOTS_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "name": f"audit_bot_{short_id()}",
            "department_id": department_id,
            "allowed_services": allowed_services,
        },
    )
    assert bot_resp.status_code in (200, 201), bot_resp.text
    bot = bot_resp.json()
    bot_id = bot.get("bot_id") or bot.get("id")

    from tests.integration._helpers_I_oauth import ensure_service_role
    for entry in service_roles or []:
        svc = entry["service_name"]
        for role in entry["roles"]:
            ensure_service_role(
                auth_client, admin_token,
                department_id=department_id, service_name=svc, role_name=role,
            )
        rr = auth_client.post(
            f"{BOTS_URL}/{bot_id}/roles",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"service_name": svc, "roles": entry["roles"]},
        )
        assert rr.status_code in (200, 201), f"assign bot roles: {rr.status_code} {rr.text}"

    tok_resp = auth_client.post(
        f"{BOTS_URL}/{bot_id}/tokens",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": f"audit_bot_t_{short_id()}"},
    )
    assert tok_resp.status_code == 201, tok_resp.text
    plain = tok_resp.json().get("token") or tok_resp.json().get("plaintext")
    return bot_id, plain


# `loging_service` режет `POST /events` на 100 rpm per-IP (slowapi). В E2E под
# auth/server трафиком лимит выбивается раньше, чем доходит наш `os_version.create`,
# и server_service (best-effort emit) глотает 429. Аудит-propagation проверяется
# только когда лимит снят (или service подняли с другим лимитом / выключенным
# rate-limit в test env).
@pytest.mark.xfail(
    reason="loging_service /events rate-limit (100/min) задушивает best-effort audit emit под E2E нагрузкой",
    strict=False,
)
class TestSubjectTypePropagation:
    def test_user_jwt_emits_actor_type_user_on_server_endpoint(
        self,
        auth_client: httpx.Client,
        server_client: httpx.Client,
        logging_client: httpx.Client,
        admin_token: str,
        reset_state,
    ):
        sid = short_id()
        dept = ensure_department(auth_client, admin_token, f"st_user_{sid}")
        ensure_service(auth_client, admin_token, SERVER_SERVICE_NAME)
        grant_service_to_department(auth_client, admin_token, dept, SERVER_SERVICE_NAME)
        user = make_user_in_dept(
            auth_client, admin_token, department_id=dept,
            services_with_roles={SERVER_SERVICE_NAME: ["admin"]},
        )
        u_jwt = login_user(auth_client, user["username"], user["_password"])["body"]["access_token"]

        os_name = f"st_user_os_{sid}"
        since = datetime.now(timezone.utc)
        r = server_client.post(
            OS_VERSIONS_URL,
            headers={"Authorization": f"Bearer {u_jwt}"},
            json={"name": os_name},
        )
        assert r.status_code == 201, r.text

        ev = wait_for_event(
            logging_client,
            action="os_version.create",
            status="success",
            from_time=since,
        )
        assert ev["actor_type"] == "user", (
            f"user JWT must surface actor_type=user in audit, got {ev['actor_type']}"
        )
        assert ev["actor_id"] == user["id"]

    def test_bot_token_emits_actor_type_bot_on_server_endpoint(
        self,
        auth_client: httpx.Client,
        server_client: httpx.Client,
        logging_client: httpx.Client,
        admin_token: str,
        reset_state,
    ):
        sid = short_id()
        dept = ensure_department(auth_client, admin_token, f"st_bot_{sid}")
        ensure_service(auth_client, admin_token, SERVER_SERVICE_NAME)
        grant_service_to_department(auth_client, admin_token, dept, SERVER_SERVICE_NAME)

        bot_id, bot_token = _create_bot_with_token(
            auth_client, admin_token,
            department_id=dept,
            allowed_services=[SERVER_SERVICE_NAME],
            service_roles=[{"service_name": SERVER_SERVICE_NAME, "roles": ["admin"]}],
        )

        os_name = f"st_bot_os_{sid}"
        since = datetime.now(timezone.utc)
        r = server_client.post(
            OS_VERSIONS_URL,
            headers={"Authorization": f"Bearer {bot_token}"},
            json={"name": os_name},
        )
        assert r.status_code == 201, r.text

        ev = wait_for_event(
            logging_client,
            action="os_version.create",
            status="success",
            from_time=since,
        )
        assert ev["actor_type"] == "bot", (
            f"bot-token must surface actor_type=bot in audit, got {ev['actor_type']}"
        )
        assert ev["actor_id"] == bot_id

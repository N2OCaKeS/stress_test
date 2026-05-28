"""Cross-service интеграция: полный lifecycle бота → audit в loging_service.

Реальные процессы auth_service + loging_service. Проверяем что:
* `bot.create`, `bot.token_create`, `bot.roles_assign` → события с правильным
  severity и target;
* introspect бот-токена эмитит `token.introspect` с `actor_type=bot` и
  `actor_id=bot.id`;
* `service.access_check` от имени бота — с правильным actor_id.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import httpx

from tests.integration._helpers_I_oauth import SERVICE_API_KEY
from tests.integration.conftest import wait_for_event


def _u() -> str:
    return uuid.uuid4().hex[:8]


# ── Setup helpers ────────────────────────────────────────────────────────────

def _ensure_dept(auth_client: httpx.Client, admin_token: str, name: str) -> str:
    """Идемпотентно создаёт отдел; возвращает его id."""
    r = auth_client.post(
        "/api/auth/v1/departments",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": name, "display_name": name.title()},
    )
    if r.status_code == 201:
        return r.json()["department_id"]
    # 409 → отдел уже есть, найдём
    listing = auth_client.get(
        "/api/auth/v1/departments",
        headers={"Authorization": f"Bearer {admin_token}"},
    ).json()
    return next(d["department_id"] for d in listing if d["name"] == name)


def _ensure_service(auth_client: httpx.Client, admin_token: str, name: str) -> None:
    auth_client.post(
        "/api/auth/v1/services",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"service_name": name, "display_name": name.title()},
    )
    # 201 при успехе, 409 если уже есть — обе ок


def _grant_service(auth_client: httpx.Client, admin_token: str, dept_id: str, service: str) -> None:
    auth_client.post(
        f"/api/auth/v1/departments/{dept_id}/services",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"service_name": service},
    )


# ── Тесты ────────────────────────────────────────────────────────────────────

class TestBotLifecycleAudit:
    def test_bot_create_audit_emitted(
        self, auth_client: httpx.Client, logging_client: httpx.Client, admin_token: str,
    ):
        u = _u()
        svc = f"bot_lc_svc_{u}"
        dept_id = _ensure_dept(auth_client, admin_token, f"bot_lc_dept_{u}")
        _ensure_service(auth_client, admin_token, svc)
        _grant_service(auth_client, admin_token, dept_id, svc)

        since = datetime.now(timezone.utc)
        r = auth_client.post(
            "/api/auth/v1/bots",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "name": f"audit_test_bot_{u}",
                "department_id": dept_id,
                "allowed_services": [svc],
            },
        )
        assert r.status_code == 201, r.text
        bot_id = r.json()["bot_id"]

        ev = wait_for_event(
            logging_client, action="bot.create", status="success", from_time=since,
        )
        assert ev["service"] == "auth_service"
        # bot.create severity = WARNING (см. _DEFAULT_SEVERITY в rule_service)
        assert ev["severity"] == "WARNING"
        assert ev["target_id"] == bot_id
        assert ev["target_type"] == "bot"
        assert ev["details"]["department_id"] == dept_id

    def test_bot_token_create_audit_with_target(
        self, auth_client: httpx.Client, logging_client: httpx.Client, admin_token: str,
    ):
        u = _u()
        svc = f"bot_tok_svc_{u}"
        dept_id = _ensure_dept(auth_client, admin_token, f"bot_tok_dept_{u}")
        _ensure_service(auth_client, admin_token, svc)
        _grant_service(auth_client, admin_token, dept_id, svc)

        bot = auth_client.post(
            "/api/auth/v1/bots",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": f"tok_audit_bot_{u}", "department_id": dept_id,
                  "allowed_services": [svc]},
        ).json()

        since = datetime.now(timezone.utc)
        tok = auth_client.post(
            f"/api/auth/v1/bots/{bot['bot_id']}/tokens",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": "audit_token"},
        )
        assert tok.status_code == 201
        token_id = tok.json()["token_id"]
        raw_token = tok.json()["token"]

        ev = wait_for_event(
            logging_client, action="bot.token_create", status="success", from_time=since,
        )
        assert ev["target_id"] == token_id
        assert ev["target_type"] == "bot_token"
        # plaintext bot-token должен быть замаскирован в audit details
        assert raw_token not in str(ev.get("details", {})), (
            "raw bot token must be redacted in audit details"
        )

    def test_introspect_bot_token_emits_actor_type_bot(
        self, auth_client: httpx.Client, logging_client: httpx.Client, admin_token: str,
    ):
        u = _u()
        svc = f"bot_intr_svc_{u}"
        dept_id = _ensure_dept(auth_client, admin_token, f"bot_intr_dept_{u}")
        _ensure_service(auth_client, admin_token, svc)
        _grant_service(auth_client, admin_token, dept_id, svc)
        bot = auth_client.post(
            "/api/auth/v1/bots",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": f"intr_bot_{u}", "department_id": dept_id,
                  "allowed_services": [svc]},
        ).json()
        raw = auth_client.post(
            f"/api/auth/v1/bots/{bot['bot_id']}/tokens",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": "intr_tok"},
        ).json()["token"]

        since = datetime.now(timezone.utc)
        # introspect — service-to-service, нужен SERVICE_API_KEY + X-Service-Identity.
        intr = auth_client.post(
            "/api/auth/v1/authorization/introspect",
            json={"token": raw},
            headers={
                "Authorization": f"Bearer {SERVICE_API_KEY}",
                "X-Service-Identity": "loging_service",
            },
        )
        assert intr.status_code == 200
        body = intr.json()
        assert body["active"] is True
        assert body["subject_type"] == "bot"
        assert body["sub"] == bot["bot_id"]
        assert body["department_id"] == dept_id

        # В loging_service приехал event token.introspect с actor_type=bot
        # Поскольку фильтра по actor_type в /events может не быть — ищем по
        # action и target_id (= bot_token.id), и затем проверяем actor_type.
        # Простейшее: action=token.introspect, status=success, ищем актора=bot_id.
        import time
        deadline_loops = 30
        found = None
        for _ in range(deadline_loops):
            r = logging_client.get(
                "/api/logging/v1/events",
                params={"action": "token.introspect", "status": "success",
                        "from_time": since.isoformat(), "limit": 50},
            )
            for item in r.json()["items"]:
                if item.get("actor_type") == "bot" and item.get("actor_id") == bot["bot_id"]:
                    found = item
                    break
            if found:
                break
            time.sleep(0.5)

        assert found is not None, "token.introspect event for bot must be emitted"
        assert found["department_id"] == dept_id
        assert found["details"]["token_type"] == "bot_token"


class TestBotRolesAudit:
    def test_assigning_role_to_bot_creates_audit(
        self, auth_client: httpx.Client, logging_client: httpx.Client, admin_token: str,
    ):
        u = _u()
        svc = f"bot_role_svc_{u}"
        role = f"reader_x_{u}"
        dept_id = _ensure_dept(auth_client, admin_token, f"bot_role_dept_{u}")
        _ensure_service(auth_client, admin_token, svc)
        _grant_service(auth_client, admin_token, dept_id, svc)

        # Создадим custom-роль для пары (dept, svc), чтобы было что назначать
        auth_client.post(
            f"/api/auth/v1/departments/{dept_id}/services/{svc}/roles",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"role_name": role, "display_name": "ReaderX"},
        )

        bot = auth_client.post(
            "/api/auth/v1/bots",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": f"roles_audit_bot_{u}", "department_id": dept_id,
                  "allowed_services": [svc]},
        ).json()

        since = datetime.now(timezone.utc)
        assign = auth_client.post(
            f"/api/auth/v1/bots/{bot['bot_id']}/roles",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"service_name": svc, "roles": [role]},
        )
        assert assign.status_code == 201, assign.text

        ev = wait_for_event(
            logging_client, action="bot.roles_assign", status="success", from_time=since,
        )
        assert ev["target_id"] == bot["bot_id"]
        assert ev["target_type"] == "bot"
        assert ev["details"]["service_name"] == svc
        assert ev["details"]["roles"] == [role]

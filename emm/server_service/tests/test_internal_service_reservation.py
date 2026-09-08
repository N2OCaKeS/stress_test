"""Тесты брони сервера «от имени сервиса» (s2s, X-Service-Identity).

Третий канал доступа рядом с user-facing `/servers/{id}/busy` и
worker-internal `/internal/servers/{id}/...`: вызывающий сервис
(`testing_service`, `acs`) аутентифицируется shared-secret'ом из
`SERVER_INBOUND_SERVICE_API_KEYS` — без пользователя, отдела и матрицы
`entity_permissions`.

Покрывает:

* whitelist identity и сверку bearer'а (наследуется от `require_internal_caller`);
* держатель брони берётся из identity, а не из тела запроса;
* захват занятого сервера — 409, независимо от того, кто его занял;
* release/смена стадии работают только для собственной брони;
* необязательный `requested_by_department_id` сверяется с отделом сервера;
* сервисная бронь гейтит деструктивные операции чужого пользователя;
* смена стадии не двигает `busy_since`.

Реальный PostgreSQL через сервисный docker-compose.test.yml (см. conftest).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from src.core.constants import BusyState
from src.models import Server
from tests._helpers import auth_hdr as _user_hdr

BASE = "/api/server/v1/internal/servers"
TESTING_SECRET = "test-testing-service-secret-do-not-use-in-prod"
ACS_SECRET = "test-acs-secret-do-not-use-in-prod"


@pytest.fixture
def configure_service_keys(monkeypatch):
    """Сконфигурировать ключи обоих разрешённых identity через env.

    `get_settings()` lru_cached'ится — чистим cache до и после теста, чтобы
    значения не утекали между кейсами.
    """
    from src.core.config import get_settings as _get_settings

    monkeypatch.setenv(
        "SERVER_INBOUND_SERVICE_API_KEYS",
        f"testing_service={TESTING_SECRET},acs={ACS_SECRET}",
    )
    _get_settings.cache_clear()  # type: ignore[attr-defined]
    yield
    _get_settings.cache_clear()  # type: ignore[attr-defined]


def _hdr(secret: str, identity: str = "testing_service") -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}", "X-Service-Identity": identity}


async def _row(db, server_id: str) -> Server:
    return (await db.execute(select(Server).where(Server.id == server_id))).scalar_one()


class TestServiceAcquireAuth:
    async def test_unknown_identity_rejected(
        self, client, make_server, db, configure_service_keys,
    ):
        srv = await make_server()
        await db.flush()
        resp = await client.post(
            f"{BASE}/{srv.id}/acquire-for-service",
            headers=_hdr(TESTING_SECRET, identity="rogue_service"),
            json={},
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["error_code"] == "SERVICE_IDENTITY_NOT_ALLOWED"

    async def test_missing_identity_header_rejected(
        self, client, make_server, db, configure_service_keys,
    ):
        srv = await make_server()
        await db.flush()
        resp = await client.post(
            f"{BASE}/{srv.id}/acquire-for-service",
            headers={"Authorization": f"Bearer {TESTING_SECRET}"},
            json={},
        )
        assert resp.status_code == 401, resp.text
        assert resp.json()["error_code"] == "SERVICE_IDENTITY_REQUIRED"

    async def test_wrong_secret_rejected(
        self, client, make_server, db, configure_service_keys,
    ):
        srv = await make_server()
        await db.flush()
        resp = await client.post(
            f"{BASE}/{srv.id}/acquire-for-service",
            headers=_hdr("wrong-secret"),
            json={},
        )
        assert resp.status_code == 401, resp.text
        assert resp.json()["error_code"] == "INVALID_SERVICE_TOKEN"


class TestServiceAcquire:
    async def test_acquire_sets_service_actor_from_identity(
        self, client, make_server, db, configure_service_keys,
    ):
        srv = await make_server()
        await db.flush()

        resp = await client.post(
            f"{BASE}/{srv.id}/acquire-for-service",
            headers=_hdr(TESTING_SECRET),
            json={"busy_note": "подготовка стенда"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["busy_actor_type"] == "service"
        assert body["busy_service_name"] == "testing_service"
        # Дефолтная стадия цикла — подготовка стенда.
        assert body["busy_state"] == "acs"

        row = await _row(db, srv.id)
        assert row.busy_state == BusyState.ACS
        assert row.busy_user_id is None
        assert row.busy_service_name == "testing_service"
        assert row.busy_note == "подготовка стенда"
        assert row.busy_since is not None

    async def test_busy_state_from_body_is_honoured(
        self, client, make_server, db, configure_service_keys,
    ):
        srv = await make_server()
        await db.flush()
        resp = await client.post(
            f"{BASE}/{srv.id}/acquire-for-service",
            headers=_hdr(TESTING_SECRET),
            json={"busy_state": "testing"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["busy_state"] == "testing"

    async def test_free_is_not_an_acceptable_busy_state(
        self, client, make_server, db, configure_service_keys,
    ):
        srv = await make_server()
        await db.flush()
        resp = await client.post(
            f"{BASE}/{srv.id}/acquire-for-service",
            headers=_hdr(TESTING_SECRET),
            json={"busy_state": "free"},
        )
        assert resp.status_code == 422, resp.text

    async def test_conflict_when_held_by_user(
        self, client, make_server, db, configure_service_keys,
    ):
        srv = await make_server()
        srv.busy_state = BusyState.BUSY
        srv.busy_user_id = "usr_owner"
        await db.flush()

        resp = await client.post(
            f"{BASE}/{srv.id}/acquire-for-service",
            headers=_hdr(TESTING_SECRET),
            json={},
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["error_code"] == "SERVER_ALREADY_BUSY"

        row = await _row(db, srv.id)
        assert row.busy_user_id == "usr_owner"
        assert row.busy_service_name is None

    async def test_conflict_when_held_by_other_service(
        self, client, make_server, db, configure_service_keys,
    ):
        srv = await make_server()
        await db.flush()
        first = await client.post(
            f"{BASE}/{srv.id}/acquire-for-service",
            headers=_hdr(ACS_SECRET, identity="acs"),
            json={},
        )
        assert first.status_code == 200, first.text

        second = await client.post(
            f"{BASE}/{srv.id}/acquire-for-service",
            headers=_hdr(TESTING_SECRET),
            json={},
        )
        assert second.status_code == 409, second.text
        assert second.json()["error_code"] == "SERVER_ALREADY_BUSY"
        assert (await _row(db, srv.id)).busy_service_name == "acs"

    async def test_department_mismatch_masked_as_404(
        self, client, make_server, db, configure_service_keys,
    ):
        srv = await make_server(department_id="dep_a")
        await db.flush()
        resp = await client.post(
            f"{BASE}/{srv.id}/acquire-for-service",
            headers=_hdr(TESTING_SECRET),
            json={"requested_by_department_id": "dep_b"},
        )
        assert resp.status_code == 404, resp.text
        assert resp.json()["error_code"] == "SERVER_NOT_FOUND"
        assert (await _row(db, srv.id)).busy_state == BusyState.FREE

    async def test_matching_department_passes(
        self, client, make_server, db, configure_service_keys,
    ):
        srv = await make_server(department_id="dep_a")
        await db.flush()
        resp = await client.post(
            f"{BASE}/{srv.id}/acquire-for-service",
            headers=_hdr(TESTING_SECRET),
            json={"requested_by_department_id": "dep_a"},
        )
        assert resp.status_code == 200, resp.text
        assert (await _row(db, srv.id)).busy_service_name == "testing_service"

    async def test_unknown_server_is_404(
        self, client, configure_service_keys,
    ):
        resp = await client.post(
            f"{BASE}/srv_missing/acquire-for-service",
            headers=_hdr(TESTING_SECRET),
            json={},
        )
        assert resp.status_code == 404, resp.text
        assert resp.json()["error_code"] == "SERVER_NOT_FOUND"


class TestServiceRelease:
    async def test_release_own_reservation(
        self, client, make_server, db, configure_service_keys,
    ):
        srv = await make_server()
        await db.flush()
        await client.post(
            f"{BASE}/{srv.id}/acquire-for-service",
            headers=_hdr(TESTING_SECRET), json={"busy_note": "n"},
        )

        resp = await client.post(
            f"{BASE}/{srv.id}/release-for-service", headers=_hdr(TESTING_SECRET),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["busy_state"] == "free"

        row = await _row(db, srv.id)
        assert row.busy_state == BusyState.FREE
        assert row.busy_actor_type == "user"
        assert row.busy_service_name is None
        assert row.busy_note is None
        assert row.busy_since is None

    async def test_cannot_release_other_service_reservation(
        self, client, make_server, db, configure_service_keys,
    ):
        srv = await make_server()
        await db.flush()
        await client.post(
            f"{BASE}/{srv.id}/acquire-for-service",
            headers=_hdr(ACS_SECRET, identity="acs"), json={},
        )

        resp = await client.post(
            f"{BASE}/{srv.id}/release-for-service", headers=_hdr(TESTING_SECRET),
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["error_code"] == "SERVER_RESERVED_BY_OTHER"
        assert (await _row(db, srv.id)).busy_service_name == "acs"

    async def test_cannot_release_user_reservation(
        self, client, make_server, db, configure_service_keys,
    ):
        srv = await make_server()
        srv.busy_state = BusyState.BUSY
        srv.busy_user_id = "usr_owner"
        await db.flush()

        resp = await client.post(
            f"{BASE}/{srv.id}/release-for-service", headers=_hdr(TESTING_SECRET),
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["error_code"] == "SERVER_RESERVED_BY_OTHER"
        assert (await _row(db, srv.id)).busy_user_id == "usr_owner"

    async def test_release_free_server_is_conflict(
        self, client, make_server, db, configure_service_keys,
    ):
        srv = await make_server()
        await db.flush()
        resp = await client.post(
            f"{BASE}/{srv.id}/release-for-service", headers=_hdr(TESTING_SECRET),
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["error_code"] == "SERVER_NOT_BUSY"


class TestServiceStatus:
    async def test_acs_to_testing_keeps_busy_since(
        self, client, make_server, db, configure_service_keys,
    ):
        srv = await make_server()
        await db.flush()
        acquired = await client.post(
            f"{BASE}/{srv.id}/acquire-for-service",
            headers=_hdr(TESTING_SECRET), json={"busy_note": "restore|1711rc42"},
        )
        assert acquired.status_code == 200, acquired.text
        busy_since = (await _row(db, srv.id)).busy_since

        resp = await client.post(
            f"{BASE}/{srv.id}/service-status",
            headers=_hdr(TESTING_SECRET),
            json={"busy_state": "testing", "busy_note": "smoke|1711rc42|6.6"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["busy_state"] == "testing"

        row = await _row(db, srv.id)
        assert row.busy_state == BusyState.TESTING
        assert row.busy_note == "smoke|1711rc42|6.6"
        assert row.busy_service_name == "testing_service"
        # busy_since отмеряет всю бронь, а не отдельную стадию.
        assert row.busy_since == busy_since

    async def test_note_omitted_keeps_previous_note(
        self, client, make_server, db, configure_service_keys,
    ):
        srv = await make_server()
        await db.flush()
        await client.post(
            f"{BASE}/{srv.id}/acquire-for-service",
            headers=_hdr(TESTING_SECRET), json={"busy_note": "restore|1711rc42"},
        )

        resp = await client.post(
            f"{BASE}/{srv.id}/service-status",
            headers=_hdr(TESTING_SECRET), json={"busy_state": "testing"},
        )
        assert resp.status_code == 200, resp.text
        assert (await _row(db, srv.id)).busy_note == "restore|1711rc42"

    async def test_cannot_change_other_service_reservation(
        self, client, make_server, db, configure_service_keys,
    ):
        srv = await make_server()
        await db.flush()
        await client.post(
            f"{BASE}/{srv.id}/acquire-for-service",
            headers=_hdr(ACS_SECRET, identity="acs"), json={},
        )

        resp = await client.post(
            f"{BASE}/{srv.id}/service-status",
            headers=_hdr(TESTING_SECRET), json={"busy_state": "testing"},
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["error_code"] == "SERVER_RESERVED_BY_OTHER"
        assert (await _row(db, srv.id)).busy_state == BusyState.ACS

    async def test_cannot_change_free_server(
        self, client, make_server, db, configure_service_keys,
    ):
        srv = await make_server()
        await db.flush()
        resp = await client.post(
            f"{BASE}/{srv.id}/service-status",
            headers=_hdr(TESTING_SECRET), json={"busy_state": "testing"},
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["error_code"] == "SERVER_NOT_BUSY"


class TestServiceReservationBlocksUserOperations:
    async def test_user_acquire_conflicts_with_service_reservation(
        self, client, make_server, db, configure_service_keys, operator_token_a,
    ):
        """Бронь сервиса видна обычному acquire как обычная занятость."""
        srv = await make_server(department_id="dep_a")
        await db.flush()
        held = await client.post(
            f"{BASE}/{srv.id}/acquire-for-service",
            headers=_hdr(TESTING_SECRET), json={"busy_state": "busy"},
        )
        assert held.status_code == 200, held.text

        resp = await client.post(
            f"/api/server/v1/servers/{srv.id}/busy",
            headers=_user_hdr(operator_token_a),
            json={"purpose": "мой тест"},
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["error_code"] == "SERVER_ALREADY_BUSY"

    async def test_human_release_can_unstick_service_reservation(
        self, client, make_server, db, configure_service_keys, operator_token_a,
    ):
        """Аварийный выход: носитель `busy_release` снимает зависшую бронь сервиса."""
        srv = await make_server(department_id="dep_a")
        await db.flush()
        held = await client.post(
            f"{BASE}/{srv.id}/acquire-for-service",
            headers=_hdr(TESTING_SECRET), json={"busy_state": "testing"},
        )
        assert held.status_code == 200, held.text

        resp = await client.delete(
            f"/api/server/v1/servers/{srv.id}/busy",
            headers=_user_hdr(operator_token_a),
        )
        assert resp.status_code == 200, resp.text

        row = await _row(db, srv.id)
        assert row.busy_state == BusyState.FREE
        assert row.busy_actor_type == "user"
        assert row.busy_service_name is None

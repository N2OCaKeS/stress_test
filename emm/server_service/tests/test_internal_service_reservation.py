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
* смена стадии не двигает `busy_since`;
* `takeover=true` отнимает `busy` / `testing_done`, отдаёт `previous_holder`
  и эмитит `server.reservation_taken_over`; `updating` / `acs` / `testing`
  не отнимаются.

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
def captured_emits(monkeypatch):
    from tests._helpers import make_emit_capture

    return make_emit_capture(monkeypatch, "src.services.server.audit_service.emit")


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


class TestServiceAcquireTakeover:
    async def test_takeover_busy_user_reservation(
        self, client, make_server, db, configure_service_keys, captured_emits,
    ):
        srv = await make_server()
        srv.busy_state = BusyState.BUSY
        srv.busy_user_id = "usr_owner"
        srv.busy_note = "ручной прогон"
        await db.flush()

        resp = await client.post(
            f"{BASE}/{srv.id}/acquire-for-service",
            headers=_hdr(TESTING_SECRET),
            json={"busy_state": "acs", "busy_note": "run-1", "takeover": True},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["busy_state"] == "acs"
        assert body["busy_actor_type"] == "service"
        assert body["busy_service_name"] == "testing_service"
        assert body["busy_note"] == "run-1"
        assert body["previous_holder"] == {
            "busy_state": "busy",
            "busy_user_id": "usr_owner",
            "busy_service_name": None,
            "busy_note": "ручной прогон",
        }

        row = await _row(db, srv.id)
        assert row.busy_state == BusyState.ACS
        assert row.busy_user_id is None
        assert row.busy_actor_type == "service"
        assert row.busy_service_name == "testing_service"
        assert row.busy_since is not None

        taken = [e for e in captured_emits if e["action"] == "server.reservation_taken_over"]
        assert len(taken) == 1
        assert taken[0]["status"] == "success"
        assert taken[0]["target_id"] == srv.id
        details = taken[0]["details"]
        assert details["service_name"] == "testing_service"
        assert details["previous_busy_state"] == "busy"
        assert details["previous_busy_user_id"] == "usr_owner"
        assert details["previous_busy_note"] == "ручной прогон"
        assert not [e for e in captured_emits if e["action"] == "server.acquired_for_service"]

    async def test_takeover_testing_done(
        self, client, make_server, db, configure_service_keys, captured_emits,
    ):
        srv = await make_server()
        await db.flush()
        await client.post(
            f"{BASE}/{srv.id}/acquire-for-service",
            headers=_hdr(ACS_SECRET, identity="acs"), json={"busy_note": "old"},
        )
        await client.post(
            f"{BASE}/{srv.id}/release-for-service-as-done", headers=_hdr(ACS_SECRET, identity="acs"),
        )

        resp = await client.post(
            f"{BASE}/{srv.id}/acquire-for-service",
            headers=_hdr(TESTING_SECRET),
            json={"takeover": True},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["busy_service_name"] == "testing_service"
        assert body["previous_holder"]["busy_state"] == "testing_done"
        assert body["previous_holder"]["busy_service_name"] == "acs"
        assert body["previous_holder"]["busy_note"] == "old"

        row = await _row(db, srv.id)
        assert row.busy_state == BusyState.ACS
        assert row.busy_service_name == "testing_service"
        assert [e for e in captured_emits if e["action"] == "server.reservation_taken_over"]

    @pytest.mark.parametrize("state", [BusyState.UPDATING, BusyState.ACS, BusyState.TESTING])
    async def test_takeover_refused_for_untakeable_states(
        self, client, make_server, db, configure_service_keys, captured_emits, state,
    ):
        srv = await make_server()
        srv.busy_state = state
        srv.busy_actor_type = "service"
        srv.busy_service_name = "acs"
        await db.flush()

        resp = await client.post(
            f"{BASE}/{srv.id}/acquire-for-service",
            headers=_hdr(TESTING_SECRET),
            json={"takeover": True},
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["error_code"] == "SERVER_ALREADY_BUSY"

        row = await _row(db, srv.id)
        assert row.busy_state == state
        assert row.busy_service_name == "acs"
        assert not [e for e in captured_emits if e["action"] == "server.reservation_taken_over"]

    async def test_no_takeover_flag_keeps_conflict_on_busy(
        self, client, make_server, db, configure_service_keys, captured_emits,
    ):
        srv = await make_server()
        srv.busy_state = BusyState.BUSY
        srv.busy_user_id = "usr_owner"
        await db.flush()

        resp = await client.post(
            f"{BASE}/{srv.id}/acquire-for-service",
            headers=_hdr(TESTING_SECRET),
            json={"takeover": False},
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["error_code"] == "SERVER_ALREADY_BUSY"
        assert (await _row(db, srv.id)).busy_user_id == "usr_owner"
        assert not [e for e in captured_emits if e["action"] == "server.reservation_taken_over"]

    async def test_takeover_on_free_is_plain_acquire(
        self, client, make_server, db, configure_service_keys, captured_emits,
    ):
        srv = await make_server()
        await db.flush()

        resp = await client.post(
            f"{BASE}/{srv.id}/acquire-for-service",
            headers=_hdr(TESTING_SECRET),
            json={"takeover": True},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["busy_service_name"] == "testing_service"
        assert body["previous_holder"] is None
        assert not [e for e in captured_emits if e["action"] == "server.reservation_taken_over"]
        assert [e for e in captured_emits if e["action"] == "server.acquired_for_service"]

    async def test_plain_acquire_response_has_null_previous_holder(
        self, client, make_server, db, configure_service_keys,
    ):
        srv = await make_server()
        await db.flush()
        resp = await client.post(
            f"{BASE}/{srv.id}/acquire-for-service",
            headers=_hdr(TESTING_SECRET), json={},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["previous_holder"] is None

    async def test_takeover_decommissioned_is_conflict(
        self, client, make_server, db, configure_service_keys,
    ):
        srv = await make_server()
        srv.status = "decommissioned"
        srv.busy_state = BusyState.BUSY
        srv.busy_user_id = "usr_owner"
        await db.flush()

        resp = await client.post(
            f"{BASE}/{srv.id}/acquire-for-service",
            headers=_hdr(TESTING_SECRET), json={"takeover": True},
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["error_code"] == "SERVER_DECOMMISSIONED"
        assert (await _row(db, srv.id)).busy_user_id == "usr_owner"

    async def test_takeover_rejected_for_non_whitelisted_identity(
        self, client, make_server, db, configure_service_keys,
    ):
        srv = await make_server()
        srv.busy_state = BusyState.BUSY
        srv.busy_user_id = "usr_owner"
        await db.flush()

        resp = await client.post(
            f"{BASE}/{srv.id}/acquire-for-service",
            headers=_hdr(TESTING_SECRET, identity="rogue_service"),
            json={"takeover": True},
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["error_code"] == "SERVICE_IDENTITY_NOT_ALLOWED"
        assert (await _row(db, srv.id)).busy_user_id == "usr_owner"

    async def test_release_after_takeover_frees_server(
        self, client, make_server, db, configure_service_keys,
    ):
        srv = await make_server()
        srv.busy_state = BusyState.BUSY
        srv.busy_user_id = "usr_owner"
        await db.flush()
        await client.post(
            f"{BASE}/{srv.id}/acquire-for-service",
            headers=_hdr(TESTING_SECRET), json={"takeover": True},
        )
        resp = await client.post(
            f"{BASE}/{srv.id}/release-for-service", headers=_hdr(TESTING_SECRET),
        )
        assert resp.status_code == 200, resp.text
        assert (await _row(db, srv.id)).busy_state == BusyState.FREE


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


class TestServiceReleaseAsDone:
    """`/release-for-service-as-done` — стенд паркуется в `testing_done`,
    контекст держателя (кто тестировал) сохраняется, а не сбрасывается."""

    async def test_release_as_done_keeps_holder_context(
        self, client, make_server, db, configure_service_keys,
    ):
        srv = await make_server()
        await db.flush()
        await client.post(
            f"{BASE}/{srv.id}/acquire-for-service",
            headers=_hdr(TESTING_SECRET), json={"busy_note": "rc42"},
        )
        before = await _row(db, srv.id)
        busy_since_before = before.busy_since

        resp = await client.post(
            f"{BASE}/{srv.id}/release-for-service-as-done", headers=_hdr(TESTING_SECRET),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["busy_state"] == "testing_done"

        row = await _row(db, srv.id)
        assert row.busy_state == BusyState.TESTING_DONE
        assert row.busy_actor_type == "service"
        assert row.busy_service_name == "testing_service"
        assert row.busy_note == "rc42"
        assert row.busy_since == busy_since_before

    async def test_cannot_release_as_done_other_service_reservation(
        self, client, make_server, db, configure_service_keys,
    ):
        srv = await make_server()
        await db.flush()
        await client.post(
            f"{BASE}/{srv.id}/acquire-for-service",
            headers=_hdr(ACS_SECRET, identity="acs"), json={},
        )

        resp = await client.post(
            f"{BASE}/{srv.id}/release-for-service-as-done", headers=_hdr(TESTING_SECRET),
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["error_code"] == "SERVER_RESERVED_BY_OTHER"
        assert (await _row(db, srv.id)).busy_service_name == "acs"

    async def test_cannot_release_as_done_user_reservation(
        self, client, make_server, db, configure_service_keys,
    ):
        srv = await make_server()
        srv.busy_state = BusyState.BUSY
        srv.busy_user_id = "usr_owner"
        await db.flush()

        resp = await client.post(
            f"{BASE}/{srv.id}/release-for-service-as-done", headers=_hdr(TESTING_SECRET),
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["error_code"] == "SERVER_RESERVED_BY_OTHER"
        assert (await _row(db, srv.id)).busy_user_id == "usr_owner"

    async def test_release_as_done_free_server_is_conflict(
        self, client, make_server, db, configure_service_keys,
    ):
        srv = await make_server()
        await db.flush()
        resp = await client.post(
            f"{BASE}/{srv.id}/release-for-service-as-done", headers=_hdr(TESTING_SECRET),
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

    async def test_human_cannot_unstick_service_reservation(
        self, client, make_server, db, configure_service_keys, operator_token_a,
    ):
        """Никакой аварийный выход для людей: зависшую `testing`-бронь снимает
        только сам держащий сервис через internal-канал (`release-for-service*`).

        Раньше носитель `busy_release` мог форсировать `DELETE .../busy` и
        стереть чужую сервисную бронь — это расходилось с докстрингом
        `reservation.py` («прервать тест может только сам держащий сервис») и
        оставляло `testing_service` с элементом очереди, который считает тест
        всё ещё идущим, хотя сервер уже «free» для остальных."""
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
        assert resp.status_code == 409, resp.text
        assert resp.json()["error_code"] == "SERVER_TESTING_IN_PROGRESS"

        row = await _row(db, srv.id)
        assert row.busy_state == BusyState.TESTING
        assert row.busy_actor_type == "service"


class TestConnectionInfo:
    """`GET /internal/servers/{id}/connection-info` — хост стенда для testing_worker."""

    async def test_returns_host_for_allowed_identity(
        self, client, make_server, db, configure_service_keys,
    ):
        srv = await make_server()
        await db.flush()
        resp = await client.get(
            f"{BASE}/{srv.id}/connection-info", headers=_hdr(TESTING_SECRET),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["server_id"] == srv.id
        assert body["host"] == str(srv.ip_address)

    async def test_unknown_server_404(self, client, configure_service_keys):
        resp = await client.get(
            f"{BASE}/srv_does_not_exist/connection-info", headers=_hdr(TESTING_SECRET),
        )
        assert resp.status_code == 404, resp.text
        assert resp.json()["error_code"] == "SERVER_NOT_FOUND"

    async def test_unknown_identity_rejected(
        self, client, make_server, db, configure_service_keys,
    ):
        srv = await make_server()
        await db.flush()
        resp = await client.get(
            f"{BASE}/{srv.id}/connection-info",
            headers=_hdr(TESTING_SECRET, identity="rogue_service"),
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["error_code"] == "SERVICE_IDENTITY_NOT_ALLOWED"

    async def test_does_not_require_a_reservation(
        self, client, make_server, db, configure_service_keys,
    ):
        """Справочник по id — не завязан на busy_state, работает и на свободном сервере."""
        srv = await make_server()
        await db.flush()
        assert (await _row(db, srv.id)).busy_state == BusyState.FREE
        resp = await client.get(
            f"{BASE}/{srv.id}/connection-info", headers=_hdr(TESTING_SECRET),
        )
        assert resp.status_code == 200, resp.text


class TestBatchStatus:
    """`POST /internal/servers/batch-status` — ping/busy пачкой для обзора пула."""

    async def test_returns_ping_and_busy_for_each_server(
        self, client, make_server, db, configure_service_keys,
    ):
        srv_a = await make_server()
        srv_b = await make_server()
        srv_b.ping_reachable = False
        srv_b.busy_state = BusyState.ACS
        await db.flush()

        resp = await client.post(
            f"{BASE}/batch-status",
            headers=_hdr(TESTING_SECRET),
            json={"server_ids": [srv_a.id, srv_b.id]},
        )
        assert resp.status_code == 200, resp.text
        by_id = {row["server_id"]: row for row in resp.json()["servers"]}
        assert by_id[srv_a.id]["found"] is True
        assert by_id[srv_a.id]["busy_state"] == BusyState.FREE
        assert by_id[srv_b.id]["ping_reachable"] is False
        assert by_id[srv_b.id]["busy_state"] == BusyState.ACS

    async def test_returns_user_holder_details(
        self, client, make_server, db, configure_service_keys,
    ):
        srv = await make_server()
        srv.busy_state = BusyState.BUSY
        srv.busy_actor_type = "user"
        srv.busy_user_id = "usr_holder1"
        srv.busy_note = "ручная отладка"
        await db.flush()

        resp = await client.post(
            f"{BASE}/batch-status",
            headers=_hdr(TESTING_SECRET),
            json={"server_ids": [srv.id]},
        )
        assert resp.status_code == 200, resp.text
        row = resp.json()["servers"][0]
        assert row["busy_user_id"] == "usr_holder1"
        assert row["busy_actor_type"] == "user"
        assert row["busy_note"] == "ручная отладка"
        assert row["busy_service_name"] is None

    async def test_returns_service_holder_details(
        self, client, make_server, configure_service_keys,
    ):
        srv = await make_server()
        acquire = await client.post(
            f"{BASE}/{srv.id}/acquire-for-service",
            headers=_hdr(TESTING_SECRET),
            json={"busy_state": "testing", "busy_note": "queue-item"},
        )
        assert acquire.status_code == 200, acquire.text

        resp = await client.post(
            f"{BASE}/batch-status",
            headers=_hdr(TESTING_SECRET),
            json={"server_ids": [srv.id]},
        )
        row = resp.json()["servers"][0]
        assert row["busy_actor_type"] == "service"
        assert row["busy_service_name"] == "testing_service"
        assert row["busy_user_id"] is None
        assert row["busy_note"] == "queue-item"

    async def test_unknown_server_id_marked_not_found_not_500(
        self, client, configure_service_keys,
    ):
        resp = await client.post(
            f"{BASE}/batch-status",
            headers=_hdr(TESTING_SECRET),
            json={"server_ids": ["srv_does_not_exist"]},
        )
        assert resp.status_code == 200, resp.text
        row = resp.json()["servers"][0]
        assert row == {
            "server_id": "srv_does_not_exist", "found": False,
            "busy_state": None, "busy_service_name": None,
            "busy_user_id": None, "busy_actor_type": None, "busy_note": None,
            "ping_reachable": None, "ping_checked_at": None,
        }

    async def test_unknown_identity_rejected(self, client, make_server, configure_service_keys):
        srv = await make_server()
        resp = await client.post(
            f"{BASE}/batch-status",
            headers=_hdr(TESTING_SECRET, identity="rogue_service"),
            json={"server_ids": [srv.id]},
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["error_code"] == "SERVICE_IDENTITY_NOT_ALLOWED"

    async def test_empty_list_rejected(self, client, configure_service_keys):
        resp = await client.post(
            f"{BASE}/batch-status", headers=_hdr(TESTING_SECRET), json={"server_ids": []},
        )
        assert resp.status_code == 422, resp.text


async def _enable_acs(db, *, department_id: str = "dep_a", enabled: bool = True) -> None:
    """Тот же helper, что у `test_acs_snapshots_list.py` — не общий, потому что
    в этом файле тестов нет ни одного другого потребителя ACS-настроек."""
    from src.models.acs_settings import SINGLETON_ID, AcsSettings
    from src.services import secrets_service

    row = await db.get(AcsSettings, SINGLETON_ID)
    if row is None:
        row = AcsSettings(
            id=SINGLETON_ID, enabled=enabled, acs_url="http://acs.example.com",
            acs_password_encrypted=secrets_service.encrypt(
                "acs-plaintext-secret", aad=secrets_service.aad_for_acs_password(SINGLETON_ID),
            ),
        )
        db.add(row)
    else:
        row.enabled = enabled
    await db.flush()


class TestAcsSnapshotsForService:
    """`GET /internal/servers/{id}/acs-snapshots` — owner п.9: тот же живой
    список, что у user-facing эндпоинта, но по service-to-service каналу, для
    синхронной ACS-проверки перед постановкой в очередь `testing_service`.

    Департаментский opt-in (`AcsDepartmentAccess`) намеренно не гейтится
    здесь — как и у `acs.snapshot_restore`, который этот preflight
    предвосхищает (`prepare_for_test.py::start` дефайлит restore безусловно).
    """

    async def test_filters_by_hostname_prefix(
        self, client, make_server, db, configure_service_keys, monkeypatch,
    ):
        srv = await make_server(department_id="dep_a", hostname="lowserver1")
        await _enable_acs(db)

        async def fake_list_snapshots(base_url, password):
            return [f"{srv.hostname}-1.8.5", "othertest-1.8.5", "unrelated"]

        monkeypatch.setattr(
            "src.api.v1.endpoints.internal_service_reservation.acs_client.list_snapshots",
            fake_list_snapshots,
        )

        resp = await client.get(
            f"{BASE}/{srv.id}/acs-snapshots", headers=_hdr(TESTING_SECRET),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["snapshots"] == [
            {"name": f"{srv.hostname}-1.8.5", "version_name": "1.8.5"},
        ]

    async def test_acs_disabled_503(self, client, make_server, db, configure_service_keys):
        srv = await make_server()
        await _enable_acs(db, enabled=False)

        resp = await client.get(
            f"{BASE}/{srv.id}/acs-snapshots", headers=_hdr(TESTING_SECRET),
        )
        assert resp.status_code == 503, resp.text
        assert resp.json()["error_code"] == "ACS_DISABLED"

    async def test_unknown_server_404(self, client, configure_service_keys):
        resp = await client.get(
            f"{BASE}/srv_does_not_exist/acs-snapshots", headers=_hdr(TESTING_SECRET),
        )
        assert resp.status_code == 404, resp.text

    async def test_unknown_identity_rejected(self, client, make_server, configure_service_keys):
        srv = await make_server()
        resp = await client.get(
            f"{BASE}/{srv.id}/acs-snapshots",
            headers=_hdr(TESTING_SECRET, identity="rogue_service"),
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["error_code"] == "SERVICE_IDENTITY_NOT_ALLOWED"

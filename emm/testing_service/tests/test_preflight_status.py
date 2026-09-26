"""Статус ожидания внешних сервисов для левой панели UI."""

from __future__ import annotations

import redis.asyncio as aioredis

from src.core.config import get_settings
from src.db.session import AsyncSessionLocal
from src.services import preflight_status
from src.services import queue as queue_svc
from tests.conftest import auth_hdr
from tests.test_queue import (  # noqa: F401 — фикстуры переиспользуются pytest'ом по имени
    LAUNCH_CTX,
    QUEUE_BASE,
    WORKER_SECRET,
    _create_stand,
    _create_test_def,
    _identity,
    _server_hdr,
    configure_internal_keys,
    mock_server_service,
    recorded_calls,
)

STATUS_URL = "/api/testing/v1/preflight/status"


async def _enqueue(client, admin_token, department_id="dep_a") -> str:
    stand_id, _ = await _create_stand(client, admin_token, department_id=department_id)
    test_id = await _create_test_def(client, admin_token, stand_id)
    async with AsyncSessionLocal() as db:
        item = await queue_svc.enqueue(db, _identity(department_id), test_id, launch_context=LAUNCH_CTX)
    return item.id


async def _report(client, item_id: str, state: str, unavailable: list[str] | None = None):
    return await client.post(
        f"{QUEUE_BASE}/{item_id}/preflight-state",
        headers=_server_hdr("testing_worker", WORKER_SECRET),
        json={"state": state, "unavailable": unavailable or []},
    )


async def _ttl(department_id: str, item_id: str) -> int:
    client = aioredis.from_url(get_settings().redis_url)
    try:
        return await client.ttl(preflight_status._key(department_id, item_id))
    finally:
        await client.aclose()


class TestPreflightStatus:
    async def test_waiting_then_ok(self, client, admin_token, mock_server_service, configure_internal_keys):
        mock_server_service()
        item_id = await _enqueue(client, admin_token)

        resp = await _report(client, item_id, "waiting", ["https://git.astralinux.ru", "dns"])
        assert resp.status_code == 200, resp.text
        status = (await client.get(STATUS_URL, headers=auth_hdr(admin_token))).json()
        assert status["state"] == "waiting"
        assert status["unavailable"] == ["dns", "https://git.astralinux.ru"]
        assert status["waiting_items"] == 1
        since = status["since"]
        assert since is not None

        # Повторный отчёт продлевает запись, но не сдвигает начало ожидания.
        await _report(client, item_id, "waiting", ["dns"])
        status = (await client.get(STATUS_URL, headers=auth_hdr(admin_token))).json()
        assert status["unavailable"] == ["dns"]
        assert status["since"] == since

        await _report(client, item_id, "ok")
        status = (await client.get(STATUS_URL, headers=auth_hdr(admin_token))).json()
        assert status == {"state": "ok", "unavailable": [], "since": None, "waiting_items": 0}

    async def test_ttl_is_two_poll_intervals(self, client, admin_token, mock_server_service, configure_internal_keys):
        """Упавший воркер не оставляет вечное ожидание: TTL = 2 × 180 с + запас."""
        mock_server_service()
        item_id = await _enqueue(client, admin_token)
        await _report(client, item_id, "waiting", ["dns"])
        ttl = await _ttl("dep_a", item_id)
        assert 2 * 180 < ttl <= 2 * 180 + 60
        await _report(client, item_id, "ok")

    async def test_other_department_does_not_see_waiting(
        self, client, admin_token, make_token, mock_server_service, configure_internal_keys,
    ):
        mock_server_service()
        item_id = await _enqueue(client, admin_token)
        await _report(client, item_id, "waiting", ["dns"])
        try:
            other = make_token(department_id="dep_other")
            status = (await client.get(STATUS_URL, headers=auth_hdr(other))).json()
            assert status["state"] == "ok"
            no_dept = make_token(department_id=None)
            assert (await client.get(STATUS_URL, headers=auth_hdr(no_dept))).json()["state"] == "ok"
        finally:
            await _report(client, item_id, "ok")

    async def test_unknown_item_is_404(self, client, configure_internal_keys):
        resp = await _report(client, "qi_missing", "waiting", ["dns"])
        assert resp.status_code == 404

    async def test_requires_worker_identity(self, client, admin_token, mock_server_service, configure_internal_keys):
        mock_server_service()
        item_id = await _enqueue(client, admin_token)
        resp = await client.post(
            f"{QUEUE_BASE}/{item_id}/preflight-state", headers=auth_hdr(admin_token),
            json={"state": "waiting", "unavailable": ["dns"]},
        )
        assert resp.status_code in (401, 403)

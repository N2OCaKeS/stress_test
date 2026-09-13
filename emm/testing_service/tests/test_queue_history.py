"""Фильтры истории применяются до пагинации, отдел и цепочки не теряются."""
from datetime import datetime, timedelta, timezone
import uuid
import pytest
from src.db.session import AsyncSessionLocal
from src.models import QueueItem
from tests.conftest import auth_hdr
from tests.test_queue import _create_stand, _create_test_def, mock_server_service as mock_server_service, recorded_calls as recorded_calls

BASE = "/api/testing/v1/queue-items"


@pytest.fixture(autouse=True)
def server_mock(mock_server_service):
    mock_server_service()



async def seed_history(client, token):
    stand, _ = await _create_stand(client, token)
    test = await _create_test_def(client, token, stand)
    start = datetime(2026, 9, 13, 0, 0, tzinfo=timezone.utc)
    ids = [f"qi_history_{uuid.uuid4().hex}" for _ in range(4)]
    async with AsyncSessionLocal() as db:
        for i, id_ in enumerate(ids):
            db.add(QueueItem(
                id=id_, stand_id=stand, test_id=test, launch_context={},
                state="failed" if i == 0 else "succeeded", debug_mode=i > 1,
                created_at=start + timedelta(hours=i), created_by="usr_history",
                retry_of_id=ids[0] if i == 1 else None,
            ))
            await db.flush()
        await db.commit()
    return stand, test, ids


async def test_history_filters_count_page_and_retry_across_pages(client, admin_token):
    stand, test, ids = await seed_history(client, admin_token)
    params = {"stand_id": stand, "test_id": test, "order": "asc", "limit": 1}
    response = await client.get(BASE, headers=auth_hdr(admin_token), params=params)
    assert response.status_code == 200, response.text
    first = response.json()
    assert first["total"] == 4
    assert first["items"][0]["id"] == ids[0]
    assert first["items"][0]["is_current"] is False
    assert first["items"][0]["test_code"]
    second = (await client.get(BASE, headers=auth_hdr(admin_token), params={**params, "offset": 1})).json()
    assert second["total"] == 4 and second["items"][0]["id"] == ids[1]
    assert second["items"][0]["is_current"] is True
    filtered = (await client.get(BASE, headers=auth_hdr(admin_token), params={
        **params, "debug_mode": False, "states": "succeeded",
        "created_from": "2026-09-13T04:00:00+03:00", "created_until": "2026-09-13T05:00:00+03:00",
    })).json()
    assert filtered["total"] == 1 and filtered["items"][0]["id"] == ids[1]
    for filters, expected in [({"attempt_id": ids[2]}, ids[2]), ({"retry_of_id": ids[0]}, ids[1]), ({"q": ids[3]}, ids[3])]:
        result = (await client.get(BASE, headers=auth_hdr(admin_token), params=filters)).json()
        assert result["total"] == 1 and result["items"][0]["id"] == expected


async def test_history_exact_filters_cannot_escape_department(client, admin_token, make_token):
    stand, test, ids = await seed_history(client, admin_token)
    other = make_token(department_id="dep_other", service_roles={"testing_service": ["admin"]})
    response = await client.get(BASE, headers=auth_hdr(other), params={"kind": "all", "stand_id": stand, "test_id": test, "attempt_id": ids[0]})
    assert response.status_code == 200 and response.json()["total"] == 0
    assert (await client.get(BASE)).status_code == 401


@pytest.mark.parametrize("filters", [
    {"created_from": "2026-09-13T00:00:00"},
    {"created_from": "2026-09-13T03:00:00Z", "created_until": "2026-09-13T00:00:00Z"},
    {"states": "unknown"},
    {"offset": -1},
])
async def test_invalid_history_filters_rejected(client, admin_token, filters):
    response = await client.get(BASE, headers=auth_hdr(admin_token), params=filters)
    assert response.status_code == 422, response.text


async def test_search_escapes_wildcards_and_matches_test_name(client, admin_token):
    _, _, ids = await seed_history(client, admin_token)
    page = (await client.get(BASE, headers=auth_hdr(admin_token), params={"attempt_id": ids[0]})).json()
    code = page["items"][0]["test_code"]
    page = (await client.get(BASE, headers=auth_hdr(admin_token), params={"q": code.lower()})).json()
    assert page["total"] == 4
    page = (await client.get(BASE, headers=auth_hdr(admin_token), params={"q": "%"})).json()
    assert page["total"] == 0

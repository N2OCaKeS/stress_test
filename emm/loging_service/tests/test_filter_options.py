from tests.test_query import _ingest
from src.dependencies.auth import require_reader
from src.main import app

BASE = "/api/logging/v1/events/filter-options"


def test_named_filter_options_pagination_and_scope(client, admin_client, auth_headers):
    _ingest(client, auth_headers, department_id="dep_a", department_name="Испытания", actor_id="usr_z", username="Анна", target_id="srv_z", target_type="server", details={"name": "Стенд 2"})
    _ingest(client, auth_headers, department_id="dep_b", department_name="Разработка", actor_id="usr_a", username="Борис", target_id="srv_a", target_type="server", details={"name": "Стенд 1"})
    _ingest(client, auth_headers, department_id="dep_a", department_name="Испытания", actor_id="usr_z", username="Анна", target_id="srv_z", target_type="server", details={"name": "Стенд 2"})
    first = admin_client.get(BASE, params={"kind": "actor", "limit": 1})
    assert first.status_code == 200, first.text
    assert first.json() == {"items": [{"value": "usr_z", "label": "Анна"}], "has_more": True}
    second = admin_client.get(BASE, params={"kind": "actor", "limit": 1, "offset": 1}).json()
    assert second == {"items": [{"value": "usr_a", "label": "Борис"}], "has_more": False}
    previous = app.dependency_overrides[require_reader]
    app.dependency_overrides[require_reader] = lambda: {"platform_role": "department_admin", "department_id": "dep_a"}
    try:
        for kind, value in [("department", "dep_a"), ("actor", "usr_z"), ("target", "srv_z")]:
            result = admin_client.get(BASE, params={"kind": kind, "department_id": "dep_b"})
            assert result.status_code == 200, result.text
            assert [item["value"] for item in result.json()["items"]] == [value]
    finally:
        app.dependency_overrides[require_reader] = previous


def test_filter_options_requires_reader(client):
    assert client.get(BASE, params={"kind": "actor"}).status_code == 401

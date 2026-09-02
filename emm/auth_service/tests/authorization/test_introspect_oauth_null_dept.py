"""`_introspect_oauth_client_jwt` для клиента без отдела — fail-closed.

`allowed_services` у OAuth client_credentials считается как INTERSECT
dept-services с allowed_scopes. Если у клиента `department_id=None`,
`list_active_services(None)` ушёл бы в `WHERE department_id IS NULL` и мог бы
вернуть бесхозные DepartmentServiceAccess-строки — клиент получил бы доступ к
сервису без подключённого отдела. Модель держит department_id NOT NULL, но
introspect не полагается на это и отбивает такой клиент явным active=false.
"""

from __future__ import annotations

from types import SimpleNamespace

from src.repositories.oauth_clients import OAuthClientRepository
from src.services import authorization_service


class TestIntrospectOAuthNullDept:
    async def test_null_dept_client_introspect_inactive(self, monkeypatch):
        fake_client = SimpleNamespace(
            id="oac_test",
            client_id="cli_test",
            name="m2m platform client",
            is_active=True,
            department_id=None,
            allowed_scopes=["server_service"],
        )

        async def _fake_get(self, client_id):
            return fake_client

        monkeypatch.setattr(OAuthClientRepository, "get_by_client_id", _fake_get)

        resp = await authorization_service._introspect_oauth_client_jwt(
            db=None,
            sub="cli_test",
            payload={"exp": 9999999999},
            request_id="req_test",
        )
        assert resp.active is False

    async def test_dept_lookup_not_reached_for_null_dept(self, monkeypatch):
        """Гард срабатывает до `DepartmentRepository.list_active_services` —
        запрос с department_id IS NULL даже не уходит в БД."""
        fake_client = SimpleNamespace(
            id="oac_test",
            client_id="cli_test",
            name="m2m platform client",
            is_active=True,
            department_id=None,
            allowed_scopes=["server_service"],
        )

        async def _fake_get(self, client_id):
            return fake_client

        monkeypatch.setattr(OAuthClientRepository, "get_by_client_id", _fake_get)

        called = False

        async def _boom(self, dept_id):
            nonlocal called
            called = True
            return ["server_service"]

        monkeypatch.setattr(
            authorization_service.DepartmentRepository, "list_active_services", _boom
        )

        resp = await authorization_service._introspect_oauth_client_jwt(
            db=None,
            sub="cli_test",
            payload={"exp": 9999999999},
            request_id="req_test",
        )
        assert resp.active is False
        assert called is False, "null-dept client must not reach dept-service lookup"

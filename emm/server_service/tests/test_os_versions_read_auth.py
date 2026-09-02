"""Анонимный доступ к GET /os-versions* отбивается 401.

Раньше каталог OS-версий читался без bearer'а (публичный read) и был защищён
отдельным per-IP anon rate-limit'ом от enumeration-сканера. Политика изменена:
читать каталог может только аутентифицированный актор, аноним без токена
отбивается 401 `ACCESS_TOKEN_MISSING` на endpoint-уровне (зависимость
`AuthenticatedIdentity`). Отдельный anon rate-limit снят — анонимные запросы
не доходят до бизнес-логики.
"""

from __future__ import annotations

LIST = "/api/server/v1/os-versions"


from tests._helpers import assert_error, auth_hdr as _hdr  # noqa: E402


class TestAnonymousRejected:
    async def test_anonymous_list_returns_401(self, client):
        """GET /os-versions без токена → 401 ACCESS_TOKEN_MISSING."""
        resp = await client.get(LIST)
        assert_error(resp, 401, "ACCESS_TOKEN_MISSING")

    async def test_anonymous_by_id_returns_401(self, client):
        resp = await client.get(f"{LIST}/osv_anything")
        assert_error(resp, 401, "ACCESS_TOKEN_MISSING")

    async def test_anonymous_by_name_returns_401(self, client):
        resp = await client.get(f"{LIST}/by-name/anything")
        assert_error(resp, 401, "ACCESS_TOKEN_MISSING")

    async def test_authenticated_list_passes(self, client, admin_role_token_a):
        """Аутентифицированный актор читает каталог без 401/429."""
        resp = await client.get(LIST, headers=_hdr(admin_role_token_a))
        assert resp.status_code == 200

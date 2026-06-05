"""Per-IP rate-limit на анонимные GET /os-versions* (slowapi `@limiter.limit`).

Каталог OS-версий публичный (читается без bearer-токена), что делает его
удобной мишенью для enumeration-сканера: анонимный клиент мог бы выкачивать
имена / репозитории и шумить в audit `os_version.list_anonymous` /
`view_anonymous`. Endpoint-декоратор `@limiter.limit(OS_VERSIONS_ANON_RATE_LIMIT)`
кладёт жёсткий потолок (default 100/minute, env-конфигурируемый), причём
`exempt_when=_is_authenticated` оставляет authenticated read под одним только
глобальным `global_rate_limit`.

Что проверяем (integration):

* первые 100 anonymous-запросов проходят (любым non-429 статусом);
* 101-й anonymous → 429 с нашим стандартным envelope'ом и `Retry-After`;
* authenticated-запросы не съедают anon-квоту (exempt_when).

Юнит-проверки конфига и декоратора — в `tests/unit/test_os_versions_limiter.py`.
"""

from __future__ import annotations

LIST = "/api/server/v1/os-versions"


from tests._helpers import auth_hdr as _hdr  # noqa: E402


class TestAnonymousRateLimit:
    async def test_first_100_anonymous_pass_then_101_is_429(self, client):
        """Первые 100 anon-запросов — non-429, 101-й — 429."""
        for i in range(100):
            resp = await client.get(LIST)
            assert resp.status_code != 429, f"premature 429 на #{i + 1}: {resp.text}"

        resp = await client.get(LIST)
        assert resp.status_code == 429, f"expected 429, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body["error_code"] == "RATE_LIMIT_EXCEEDED"
        assert resp.headers.get("Retry-After") == "60"

    async def test_authenticated_exempt_from_anon_limit(
        self, client, admin_role_token_a,
    ):
        """Authenticated GET'ы НЕ должны учитываться в anon-bucket.

        Делаем 50 authenticated запросов — ни один не должен ловить 429
        (anon-лимит у них exempt_when=True). После них anonymous ещё
        должен принять 100 запросов до отбоя.
        """
        for i in range(50):
            resp = await client.get(LIST, headers=_hdr(admin_role_token_a))
            assert resp.status_code != 429, f"auth #{i + 1} попал в 429: {resp.text}"

        # Сейчас anon-bucket пуст — должно влезать ещё 100 запросов.
        for i in range(100):
            resp = await client.get(LIST)
            assert resp.status_code != 429, f"anon #{i + 1} попал в 429 раньше времени"

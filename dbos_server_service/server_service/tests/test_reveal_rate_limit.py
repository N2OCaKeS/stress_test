"""Per-IP+target rate-limit на reveal-эндпоинты, отдающие plaintext.

* GET /server-accounts/{id} (с view_password) — `password_b64`;
* GET /servers/{id}/ipmi (с view_credentials) — `password_b64`.

Без отдельного лимита plaintext-канал жил под глобальными `global_rate_limit`,
и burst из сотен GET'ов scraped бы пароль одной цели до того, как
CRITICAL-аудит успевал прокачаться (throttling пишет INFO после первого
reveal'а в окне).

`PASSWORD_REVEAL_RATE_LIMIT` штатно держит высокий потолок, поэтому тесты
прижимают endpoint-лимит к `10/minute` через `tight_reveal_limit`, чтобы
проверить сам механизм (per-(IP, target) keying) без сотен HTTP-вызовов.
"""

from __future__ import annotations

import pytest

BASE = "/api/server/v1"


from tests._helpers import auth_hdr as _hdr  # noqa: E402


@pytest.fixture
def tight_reveal_limit():
    """Временно подменяет лимит endpoint-декораторов на жёсткий `<N>/period`.

    Реальный `password_reveal_rate_limit` резолвится в `RateLimitItem` при
    импорте handler'а, поэтому env-override на горячую не помогает. Меняем
    сам `Limit.limit` у уже зарегистрированных route-лимитов, сохраняя их
    key-функции (per-account keying для reveal'а остаётся), и откатываем
    после теста. Счётчики между кейсами чистит autouse `_reset_rate_limiter`.
    """
    from limits import parse_many

    from src.core.limiter import endpoint_limiter
    from src.main import app  # noqa: F401 — гарантирует регистрацию route-лимитов

    saved = {
        name: [lim.limit for lim in lims]
        for name, lims in endpoint_limiter._route_limits.items()
    }

    def _apply(limit_str: str) -> None:
        item = parse_many(limit_str)[0]
        for lims in endpoint_limiter._route_limits.values():
            for lim in lims:
                lim.limit = item
        endpoint_limiter.reset()

    yield _apply

    for name, items in saved.items():
        for lim, original in zip(endpoint_limiter._route_limits[name], items):
            lim.limit = original
    endpoint_limiter.reset()


class TestPasswordRevealRateLimit:
    async def test_account_reveal_blocked_on_11th_call(
        self, client, admin_token, make_server, make_account, tight_reveal_limit,
    ):
        """11-й GET карточки аккаунта с одного IP+account → 429.

        Лимит прижат к `10/minute`; admin несёт `view_password`, так что
        каждый успешный GET — plaintext-reveal.
        """
        tight_reveal_limit("10/minute")
        srv = await make_server(department_id="dep_a")
        acc = await make_account(server_id=srv.id, password="rate-limit-test")

        # 10 успешных вызовов — under limit.
        for i in range(10):
            resp = await client.get(
                f"{BASE}/server-accounts/{acc.id}", headers=_hdr(admin_token),
            )
            assert resp.status_code == 200, f"call {i}: {resp.text}"

        # 11-й — отбивается 429.
        resp = await client.get(
            f"{BASE}/server-accounts/{acc.id}", headers=_hdr(admin_token),
        )
        assert resp.status_code == 429, resp.text

    async def test_ipmi_reveal_blocked_on_11th_call(
        self, client, admin_token, make_server, make_ipmi, tight_reveal_limit,
    ):
        """11-й GET карточки IPMI с одного IP+server → 429."""
        tight_reveal_limit("10/minute")
        srv = await make_server(department_id="dep_a")
        await make_ipmi(server_id=srv.id, password="rate-limit-test")

        for i in range(10):
            resp = await client.get(
                f"{BASE}/servers/{srv.id}/ipmi", headers=_hdr(admin_token),
            )
            assert resp.status_code == 200, f"call {i}: {resp.text}"

        resp = await client.get(
            f"{BASE}/servers/{srv.id}/ipmi", headers=_hdr(admin_token),
        )
        assert resp.status_code == 429, resp.text

    async def test_different_accounts_have_independent_buckets(
        self, client, admin_token, make_server, make_account, tight_reveal_limit,
    ):
        """Лимит ключуется на (IP, account_id) — разные account'ы делят счётчик отдельно.

        Burst до 10 на acc_a не должен забирать слоты у acc_b.
        """
        tight_reveal_limit("10/minute")
        srv = await make_server(department_id="dep_a")
        acc_a = await make_account(server_id=srv.id, login="acc_a", password="x")
        acc_b = await make_account(server_id=srv.id, login="acc_b", password="y")

        for _ in range(10):
            resp = await client.get(
                f"{BASE}/server-accounts/{acc_a.id}", headers=_hdr(admin_token),
            )
            assert resp.status_code == 200
        # acc_a — 11-й = 429.
        resp = await client.get(
            f"{BASE}/server-accounts/{acc_a.id}", headers=_hdr(admin_token),
        )
        assert resp.status_code == 429

        # acc_b — первый вызов всё ещё 200.
        resp = await client.get(
            f"{BASE}/server-accounts/{acc_b.id}", headers=_hdr(admin_token),
        )
        assert resp.status_code == 200, resp.text

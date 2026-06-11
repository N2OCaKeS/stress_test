"""GET /bots/{bot_id} — direct read одного бота.

UI замапил bot detail на отдельный get вместо list+find, бэк тоже подвозит
ручку. account_admin читает любого, department_admin — только своего отдела.
"""

from tests._helpers.http import _create_bot

BOTS_URL = "/api/auth/v1/bots"


async def _make_bot(client, token, dept_id, name):
    resp = await _create_bot(client, token, dept_id, name=name)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_account_admin_gets_any_bot(client, admin_token, dept_a):
    bot = await _make_bot(client, admin_token, dept_a.id, "get_bot_aa")

    resp = await client.get(
        f"{BOTS_URL}/{bot['bot_id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["bot_id"] == bot["bot_id"]
    assert body["name"] == "get_bot_aa"
    assert body["department_id"] == dept_a.id
    assert body["is_active"] is True


async def test_dept_admin_gets_own_dept_bot(
    client, admin_token, dept_admin_a_token, dept_a,
):
    bot = await _make_bot(client, admin_token, dept_a.id, "get_bot_own")

    resp = await client.get(
        f"{BOTS_URL}/{bot['bot_id']}",
        headers={"Authorization": f"Bearer {dept_admin_a_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["bot_id"] == bot["bot_id"]


async def test_dept_admin_cannot_read_cross_dept_bot(
    client, admin_token, dept_admin_a_token, dept_b,
):
    bot = await _make_bot(client, admin_token, dept_b.id, "get_bot_cross")

    resp = await client.get(
        f"{BOTS_URL}/{bot['bot_id']}",
        headers={"Authorization": f"Bearer {dept_admin_a_token}"},
    )
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "BOT_ACCESS_DENIED"


async def test_get_unknown_bot_returns_404(client, admin_token):
    resp = await client.get(
        f"{BOTS_URL}/bot_does_not_exist",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "BOT_NOT_FOUND"


async def test_get_bot_reads_actor_dept_from_db_after_transfer(
    client, db, admin_token, dept_admin_a, dept_admin_a_token, dept_a, dept_b,
):
    """После переноса dept_admin_a в dept_b, GET /bots/{id} на бота dept_a → 403.

    Раньше get_bot брал dept актора из identity-cache (параметр endpoint'а), и
    в течение TTL после PATCH /users/{id} юзер видел ботов своего старого
    отдела. Теперь dept резолвится из БД на каждом вызове — таймер кэша
    больше не оставляет окно privilege-mismatch.
    """
    bot = await _make_bot(client, admin_token, dept_a.id, "get_bot_after_transfer")

    # Меняем dept admin'а напрямую в БД, минуя API: эмулируем «cache hit на
    # старом dept» — токен и identity-cache юзера ещё помнят dept_a, а в БД
    # уже dept_b. Если get_bot опирался бы на identity.department_id, тест
    # ловил бы 200.
    dept_admin_a.department_id = dept_b.id
    await db.commit()

    resp = await client.get(
        f"{BOTS_URL}/{bot['bot_id']}",
        headers={"Authorization": f"Bearer {dept_admin_a_token}"},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["error_code"] == "BOT_ACCESS_DENIED"

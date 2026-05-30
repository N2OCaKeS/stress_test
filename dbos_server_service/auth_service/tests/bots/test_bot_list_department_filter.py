"""GET /bots поддерживает query-параметр `department_id` для account_admin.

bot_service.list_bots давно принимал фильтр, но endpoint его не объявлял —
account_admin без возможности сузить выдачу по отделу. Для department_admin
параметр игнорируется (он залочен на свой отдел сервисным слоем).
"""

BOTS_URL = "/api/auth/v1/bots"


async def _create(client, token, dept_id, name):
    return (await client.post(
        BOTS_URL,
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name, "department_id": dept_id, "allowed_services": []},
    )).json()


async def test_admin_can_filter_bots_by_department(
    client, admin_token, dept_a, dept_b,
):
    await _create(client, admin_token, dept_a.id, "bot_alpha_1")
    await _create(client, admin_token, dept_a.id, "bot_alpha_2")
    await _create(client, admin_token, dept_b.id, "bot_beta_1")

    resp_all = await client.get(
        BOTS_URL, headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp_all.status_code == 200
    names_all = {b["name"] for b in resp_all.json()}
    assert {"bot_alpha_1", "bot_alpha_2", "bot_beta_1"}.issubset(names_all)

    resp_a = await client.get(
        BOTS_URL,
        headers={"Authorization": f"Bearer {admin_token}"},
        params={"department_id": dept_a.id},
    )
    assert resp_a.status_code == 200
    names_a = {b["name"] for b in resp_a.json()}
    assert {"bot_alpha_1", "bot_alpha_2"}.issubset(names_a)
    assert "bot_beta_1" not in names_a

    # X-Total-Count соответствует scoped результату.
    assert resp_a.headers.get("X-Total-Count") == str(len(resp_a.json()))


async def test_dept_admin_filter_ignored_to_own_dept(
    client, admin_token, dept_admin_a_token, dept_a, dept_b,
):
    """department_admin не может смотреть чужие отделы даже передав department_id."""
    await _create(client, admin_token, dept_a.id, "bot_in_a")
    await _create(client, admin_token, dept_b.id, "bot_in_b")

    resp = await client.get(
        BOTS_URL,
        headers={"Authorization": f"Bearer {dept_admin_a_token}"},
        params={"department_id": dept_b.id},
    )
    assert resp.status_code == 200
    names = {b["name"] for b in resp.json()}
    assert "bot_in_b" not in names
    assert "bot_in_a" in names

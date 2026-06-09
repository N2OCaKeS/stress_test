"""Регрессия на N+1 в `GET /servers` (storage load).

Раньше: per-row `load_storage` → `disk_repo.list_all_for_server` дёргался
по числу серверов в странице (до 501 SELECT'а на limit=500).
Сейчас: один `disk_repo.list_for_servers(WHERE server_id IN (...))` per страница.
"""

from __future__ import annotations

import pytest

BASE = "/api/server/v1"


from tests._helpers import auth_hdr as _hdr  # noqa: E402


@pytest.fixture
def disk_repo_spy(monkeypatch):
    """Считаем вызовы `disk_repo.list_all_for_server` vs `list_for_servers`.

    Раньше list-эндпоинт шёл по первому, сейчас — по второму. Возвращаем dict
    `{single, bulk}` со счётчиками; тест проверяет, что bulk вызывался
    ровно один раз и single — ноль раз.
    """
    import src.repositories.server_disk as disk_repo
    import src.services.server as server_svc

    counters = {"single": 0, "bulk": 0}
    original_single = disk_repo.list_all_for_server
    original_bulk = disk_repo.list_for_servers

    async def wrap_single(db, server_id):
        counters["single"] += 1
        return await original_single(db, server_id)

    async def wrap_bulk(db, server_ids):
        counters["bulk"] += 1
        return await original_bulk(db, server_ids)

    monkeypatch.setattr(disk_repo, "list_all_for_server", wrap_single)
    monkeypatch.setattr(disk_repo, "list_for_servers", wrap_bulk)
    # services.server импортировал имена в свой namespace через `from ... import`,
    # поэтому патчим там тоже — иначе load_storage_for_servers вызовет оригинал.
    monkeypatch.setattr(server_svc.disk_repo, "list_all_for_server", wrap_single)
    monkeypatch.setattr(server_svc.disk_repo, "list_for_servers", wrap_bulk)
    return counters


class TestListServersStorageN1:
    async def test_offset_list_uses_bulk(
        self, client, admin_token, make_server, disk_repo_spy,
    ):
        """legacy offset/limit envelope: один bulk-SELECT, ноль single-SELECT'ов."""
        for _ in range(5):
            await make_server(department_id="dep_a")
        resp = await client.get(
            f"{BASE}/servers?limit=10&offset=0", headers=_hdr(admin_token),
        )
        assert resp.status_code == 200, resp.text
        assert len(resp.json()["items"]) == 5
        assert disk_repo_spy["bulk"] == 1, disk_repo_spy
        assert disk_repo_spy["single"] == 0, (
            f"per-row load_storage triggered N+1: {disk_repo_spy}"
        )

    async def test_cursor_list_uses_bulk(
        self, client, admin_token, make_server, disk_repo_spy,
    ):
        """cursor envelope: тот же контракт — один bulk, ноль single."""
        for _ in range(5):
            await make_server(department_id="dep_a")
        resp = await client.get(
            f"{BASE}/servers?cursor=true&limit=10", headers=_hdr(admin_token),
        )
        assert resp.status_code == 200, resp.text
        assert len(resp.json()["items"]) == 5
        assert disk_repo_spy["bulk"] == 1, disk_repo_spy
        assert disk_repo_spy["single"] == 0, (
            f"per-row load_storage triggered N+1: {disk_repo_spy}"
        )

    async def test_empty_page_calls_bulk_with_empty_list(
        self, client, admin_token, disk_repo_spy,
    ):
        """Пустая страница — bulk всё равно вызывается (1 раз) с [], single — никогда."""
        resp = await client.get(
            f"{BASE}/servers?limit=10", headers=_hdr(admin_token),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["items"] == []
        assert disk_repo_spy["bulk"] == 1
        assert disk_repo_spy["single"] == 0

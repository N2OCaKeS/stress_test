"""Справочник семейств статистики в БД + пересчёт нескольких семейств.

Сетевые вызовы замоканы фикстурами из `tests/test_statistics_recalc.py`.
Сид справочника не трогаем: тесты заводят свои семейства через API (у них
заполнен `created_by`, conftest их подчищает).
"""

from __future__ import annotations

import pytest

from tests.conftest import auth_hdr as _hdr
from tests.test_statistics_recalc import (  # noqa: F401 — фикстуры
    CATEGORIES_BASE,
    RECALC_BASE,
    STATUS_BASE,
    _configure_statistics,
    _drain_pending_tasks,
    _reset_statistics_singletons,
    _seed_integration_settings,
    mock_secret_client,
    mock_statistics_category_client,
    mock_statistics_client,
)


def _docker_payload(**over) -> dict:
    """`Docker` из `allta_app_full/statistics_conf.py:33-35` — у легаси без кнопки."""
    body = {
        "key": "docker",
        "label": "Docker",
        "path": "/docker-statistics",
        "title_statistics": "Docker",
        "set_of_test_types": ["docker-wa"],
        "sort_order": 1000,
    }
    body.update(over)
    return body


@pytest.fixture
async def _stats_ready(mock_secret_client):
    mock_secret_client["cred_x"] = ("bot", "tok123")
    await _configure_statistics()
    await _seed_integration_settings("dep_a")


class TestCategoryCrud:
    async def test_create_requires_update_permission(self, client, guest_token):
        resp = await client.post(CATEGORIES_BASE, headers=_hdr(guest_token), json=_docker_payload())
        assert resp.status_code == 403, resp.text

    async def test_patch_and_delete_require_update_permission(self, client, admin_token, guest_token):
        created = (await client.post(CATEGORIES_BASE, headers=_hdr(admin_token), json=_docker_payload())).json()
        resp = await client.patch(
            f"{CATEGORIES_BASE}/{created['id']}", headers=_hdr(guest_token), json={"label": "x"},
        )
        assert resp.status_code == 403, resp.text
        resp = await client.delete(f"{CATEGORIES_BASE}/{created['id']}", headers=_hdr(guest_token))
        assert resp.status_code == 403, resp.text

    async def test_new_category_appears_in_list_without_code_change(self, client, admin_token, guest_token):
        """Критерий приёмки: добавленное в UI семейство видно в модалке (списке)."""
        resp = await client.post(CATEGORIES_BASE, headers=_hdr(admin_token), json=_docker_payload())
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["key"] == "docker"
        assert body["comparison_list"] is None
        assert body["created_by"]

        items = (await client.get(CATEGORIES_BASE, headers=_hdr(guest_token))).json()["items"]
        assert [item["key"] for item in items][-1] == "docker"
        assert len(items) == 9

    async def test_duplicate_key_is_409(self, client, admin_token):
        await client.post(CATEGORIES_BASE, headers=_hdr(admin_token), json=_docker_payload())
        resp = await client.post(CATEGORIES_BASE, headers=_hdr(admin_token), json=_docker_payload())
        assert resp.status_code == 409, resp.text
        assert resp.json()["error_code"] == "STATISTICS_CATEGORY_DUPLICATE"

    async def test_seeded_key_is_duplicate(self, client, admin_token):
        resp = await client.post(CATEGORIES_BASE, headers=_hdr(admin_token), json=_docker_payload(key="apache"))
        assert resp.status_code == 409, resp.text

    @pytest.mark.parametrize("over", [
        {"key": "Docker"},
        {"key": "1docker"},
        {"path": "docker-statistics"},
        {"path": "/docker statistics"},
        {"set_of_test_types": []},
        {"set_of_test_types": ["  "]},
        {"comparison_list": [["only-one"]]},
        {"label": "   "},
    ])
    async def test_validation(self, client, admin_token, over):
        resp = await client.post(CATEGORIES_BASE, headers=_hdr(admin_token), json=_docker_payload(**over))
        assert resp.status_code == 422, resp.text

    async def test_patch_updates_fields_and_clears_optional_lists(self, client, admin_token):
        created = (await client.post(
            CATEGORIES_BASE, headers=_hdr(admin_token),
            json=_docker_payload(comparison_list=[["docker-wa", "docker-wb"]], comparison_kernel_list=["docker-wa"]),
        )).json()
        resp = await client.patch(
            f"{CATEGORIES_BASE}/{created['id']}", headers=_hdr(admin_token),
            json={"label": "Docker CE", "comparison_list": None, "set_of_test_types": ["docker-wa", "docker-wb"]},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["label"] == "Docker CE"
        assert body["comparison_list"] is None
        # непереданное поле не трогается
        assert body["comparison_kernel_list"] == ["docker-wa"]
        assert body["set_of_test_types"] == ["docker-wa", "docker-wb"]
        assert body["key"] == "docker"

    async def test_patch_null_for_required_field_is_ignored(self, client, admin_token):
        created = (await client.post(CATEGORIES_BASE, headers=_hdr(admin_token), json=_docker_payload())).json()
        resp = await client.patch(
            f"{CATEGORIES_BASE}/{created['id']}", headers=_hdr(admin_token), json={"path": None},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["path"] == "/docker-statistics"

    async def test_patch_unknown_is_404(self, client, admin_token):
        resp = await client.patch(f"{CATEGORIES_BASE}/stcat_missing", headers=_hdr(admin_token), json={"label": "x"})
        assert resp.status_code == 404, resp.text
        assert resp.json()["error_code"] == "STATISTICS_CATEGORY_NOT_FOUND"

    async def test_disabled_hidden_by_default_visible_with_flag(self, client, admin_token):
        await client.post(CATEGORIES_BASE, headers=_hdr(admin_token), json=_docker_payload(enabled=False))
        default = (await client.get(CATEGORIES_BASE, headers=_hdr(admin_token))).json()["items"]
        assert "docker" not in [item["key"] for item in default]
        full = (await client.get(
            CATEGORIES_BASE, headers=_hdr(admin_token), params={"include_disabled": "true"},
        )).json()["items"]
        assert "docker" in [item["key"] for item in full]

    async def test_delete(self, client, admin_token):
        created = (await client.post(CATEGORIES_BASE, headers=_hdr(admin_token), json=_docker_payload())).json()
        resp = await client.delete(f"{CATEGORIES_BASE}/{created['id']}", headers=_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"ok": True}
        items = (await client.get(CATEGORIES_BASE, headers=_hdr(admin_token))).json()["items"]
        assert "docker" not in [item["key"] for item in items]
        again = await client.delete(f"{CATEGORIES_BASE}/{created['id']}", headers=_hdr(admin_token))
        assert again.status_code == 404, again.text


class TestRecalcFromCatalog:
    async def test_new_category_recalculates_with_its_own_payload(
        self, client, admin_token, _stats_ready, monkeypatch,
    ):
        """Семейство, заведённое через API, уходит во внешний сервис своим path/телом."""
        from src.services import statistics_client

        captured: list[dict] = []

        async def fake_post(*, base_url, path, payload, timeout):
            captured.append({"url": f"{base_url}{path}", "payload": payload})

        monkeypatch.setattr(statistics_client, "_post", fake_post)
        await client.post(
            CATEGORIES_BASE, headers=_hdr(admin_token),
            json=_docker_payload(comparison_kernel_list=["docker-wa"]),
        )

        resp = await client.post(RECALC_BASE, headers=_hdr(admin_token), json={"categories": ["docker"]})
        assert resp.status_code == 202, resp.text
        await _drain_pending_tasks()

        assert captured == [{
            "url": "http://stats.example:7777/docker-statistics",
            "payload": {
                "title_statistics": "Docker",
                "username": "bot",
                "token": "tok123",
                "set_of_test_types": ["docker-wa"],
                "latest_stable_versions_bool": True,
                "comparison_kernel_list": ["docker-wa"],
            },
        }]

    async def test_edited_seed_payload_is_used(self, client, admin_token, _stats_ready, monkeypatch):
        """Правка сида в UI меняет то, что уходит во внешний сервис (восстанавливаем после)."""
        from src.services import statistics_client

        captured: list[dict] = []

        async def fake_post(*, base_url, path, payload, timeout):
            captured.append(payload)

        monkeypatch.setattr(statistics_client, "_post", fake_post)
        items = (await client.get(CATEGORIES_BASE, headers=_hdr(admin_token))).json()["items"]
        apache = next(item for item in items if item["key"] == "apache")
        try:
            await client.patch(
                f"{CATEGORIES_BASE}/{apache['id']}", headers=_hdr(admin_token),
                json={"set_of_test_types": ["apache-rp", "apache-new"]},
            )
            await client.post(RECALC_BASE, headers=_hdr(admin_token), json={"category": "apache"})
            await _drain_pending_tasks()
        finally:
            await client.patch(
                f"{CATEGORIES_BASE}/{apache['id']}", headers=_hdr(admin_token),
                json={"set_of_test_types": apache["set_of_test_types"]},
            )
        assert captured[0]["set_of_test_types"] == ["apache-rp", "apache-new"]

    async def test_several_categories_run_sequentially_in_catalog_order(
        self, client, admin_token, _stats_ready, mock_statistics_client, mock_statistics_category_client,
    ):
        all_calls, _state = mock_statistics_client
        resp = await client.post(
            RECALC_BASE, headers=_hdr(admin_token),
            json={"categories": ["virt", "apache", "virt"]},
        )
        assert resp.status_code == 202, resp.text
        await _drain_pending_tasks()

        assert all_calls == []
        # порядок справочника (apache раньше virt), дубль схлопнут
        assert [call["category"] for call in mock_statistics_category_client] == ["apache", "virt"]
        status = (await client.get(STATUS_BASE, headers=_hdr(admin_token))).json()
        assert status["status"] == "succeeded"
        assert status["categories"] == ["apache", "virt"]
        assert status["category"] == "virt"

    async def test_category_and_categories_are_merged(
        self, client, admin_token, _stats_ready, mock_statistics_category_client,
    ):
        await client.post(
            RECALC_BASE, headers=_hdr(admin_token), json={"category": "parsec", "categories": ["freeipa"]},
        )
        await _drain_pending_tasks()
        assert [call["category"] for call in mock_statistics_category_client] == ["freeipa", "parsec"]

    async def test_one_failure_does_not_stop_the_rest(
        self, client, admin_token, _stats_ready, mock_statistics_category_client,
    ):
        mock_statistics_category_client.failing.add("apache")
        await client.post(RECALC_BASE, headers=_hdr(admin_token), json={"categories": ["apache", "parsec"]})
        await _drain_pending_tasks()

        assert [call["category"] for call in mock_statistics_category_client] == ["apache", "parsec"]
        status = (await client.get(STATUS_BASE, headers=_hdr(admin_token))).json()
        assert status["status"] == "failed"
        assert status["error"] == "apache: boom"

    async def test_single_failure_keeps_plain_error(
        self, client, admin_token, _stats_ready, mock_statistics_category_client,
    ):
        mock_statistics_category_client.failing.add("apache")
        await client.post(RECALC_BASE, headers=_hdr(admin_token), json={"categories": ["apache"]})
        await _drain_pending_tasks()
        status = (await client.get(STATUS_BASE, headers=_hdr(admin_token))).json()
        assert status["error"] == "boom"

    async def test_empty_categories_is_full_recalc(
        self, client, admin_token, _stats_ready, mock_statistics_client, mock_statistics_category_client,
    ):
        all_calls, _state = mock_statistics_client
        await client.post(RECALC_BASE, headers=_hdr(admin_token), json={"categories": []})
        await _drain_pending_tasks()
        assert len(all_calls) == 1
        assert mock_statistics_category_client == []
        status = (await client.get(STATUS_BASE, headers=_hdr(admin_token))).json()
        assert status["categories"] is None
        assert status["category"] is None

    async def test_unknown_in_list_is_422_and_nothing_runs(
        self, client, admin_token, _stats_ready, mock_statistics_category_client,
    ):
        resp = await client.post(
            RECALC_BASE, headers=_hdr(admin_token), json={"categories": ["apache", "nope"]},
        )
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert body["error_code"] == "STATISTICS_CATEGORY_UNKNOWN"
        await _drain_pending_tasks()
        assert mock_statistics_category_client == []

    async def test_disabled_category_is_422(
        self, client, admin_token, _stats_ready, mock_statistics_category_client,
    ):
        await client.post(CATEGORIES_BASE, headers=_hdr(admin_token), json=_docker_payload(enabled=False))
        resp = await client.post(RECALC_BASE, headers=_hdr(admin_token), json={"categories": ["docker"]})
        assert resp.status_code == 422, resp.text
        assert resp.json()["error_code"] == "STATISTICS_CATEGORY_DISABLED"
        assert mock_statistics_category_client == []

    async def test_list_requires_update_permission(self, client, guest_token):
        resp = await client.post(RECALC_BASE, headers=_hdr(guest_token), json={"categories": ["apache"]})
        assert resp.status_code == 403, resp.text

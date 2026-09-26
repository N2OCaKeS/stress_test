""" / D15 — порядок тестов на стенде.

1. Прогон РЦ (`POST /test-runs`) ставит состав в очередь стенда по правилу
   отдела `department_test_settings.campaign_sort_rule`; сид — легаси
   `allta_back.py:393` (режим → ядро → имя тест-кейса).
2. `PATCH /test-stands/{id}/queue/order` переставляет только `queued`-элементы,
   активный не трогается; следующий берётся уже по новому порядку.
3. Ручной режим (одиночный/debug `POST /queue-items`) — FIFO, правило кампании
   к нему не применяется.
"""

from __future__ import annotations

import random
import uuid
from types import SimpleNamespace

import pytest

from src.core.constants import QueueItemState
from src.db.session import AsyncSessionLocal
from src.repositories import department_test_settings as dts_repo
from src.repositories import queue_item as queue_repo
from src.repositories import stp_cell as stp_cell_repo
from src.repositories import stp_test_case as stp_test_case_repo
from src.repositories import stp_test_run as stp_test_run_repo
from src.services import test_run as test_run_svc
from src.services.department_test_settings import DEFAULT_CAMPAIGN_SORT_RULE
from src.utils.ids import department_test_settings_id, stp_cell_id, stp_test_case_id, stp_test_run_id
from tests.conftest import auth_hdr as _hdr
from tests.test_queue import (  # noqa: F401 — фикстуры переиспользуются pytest'ом по имени
    TESTS_BASE,
    _create_stand,
    _create_test_def,
    _get_item,
    configure_internal_keys,
    mock_git_token,
    mock_server_service,
    recorded_calls,
)
from tests.test_queue_interrupt import _enqueue
from tests.test_test_runs import _drive_to_success

RUNS = "/api/testing/v1/test-runs"
STANDS = "/api/testing/v1/test-stands"
QUEUE_ITEMS = "/api/testing/v1/queue-items"
SETTINGS = "/api/testing/v1/department-test-settings"

LEGACY_RULE = [
    {"key": "mode", "direction": "asc"},
    {"key": "kernel", "direction": "asc"},
    {"key": "test_case_name", "direction": "asc"},
]


async def _create_catalog_test(
    client, admin_token, stand_id: str, *, code: str, full_name: str, mode: str = "orel", priority: int | None = None,
) -> str:
    payload = {
        "code": code, "full_name": full_name, "readiness": "ready", "mode": mode, "pinned_stand_id": stand_id,
    }
    if priority is not None:
        payload["priority"] = priority
    resp = await client.post(TESTS_BASE, headers=_hdr(admin_token), json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _set_rule(client, admin_token, rule: list[dict], department_id: str = "dep_a") -> None:
    resp = await client.put(f"{SETTINGS}/{department_id}", headers=_hdr(admin_token), json={"campaign_sort_rule": rule})
    assert resp.status_code == 200, resp.text


async def _stand_queue(stand_id: str) -> list:
    """Не терминальные элементы стенда в порядке исполнения (`position`)."""
    async with AsyncSessionLocal() as db:
        return await queue_repo.list_active_for_stand(db, stand_id)


def _unique_rc() -> str:
    return f"osv_1.8.5.{uuid.uuid4().hex[:6]}"


class TestCampaignSortRuleSettings:
    async def test_defaults_to_legacy_rule_without_row(self, client, admin_token):
        resp = await client.get(f"{SETTINGS}/dep_a", headers=_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        assert resp.json()["campaign_sort_rule"] == LEGACY_RULE
        assert [dict(i) for i in DEFAULT_CAMPAIGN_SORT_RULE] == LEGACY_RULE

    async def test_migration_seeds_legacy_rule_for_existing_rows(self, client, admin_token):
        """Строка, заведённая без правила (как все строки до миграции), получает легаси-правило из `server_default`."""
        async with AsyncSessionLocal() as db:
            await dts_repo.create(db, {"id": department_test_settings_id(), "department_id": "dep_a", "retry_enabled": True})
            await db.commit()
        resp = await client.get(f"{SETTINGS}/dep_a", headers=_hdr(admin_token))
        assert resp.json()["id"] is not None
        assert resp.json()["campaign_sort_rule"] == LEGACY_RULE

    async def test_put_stores_custom_rule(self, client, admin_token):
        rule = [{"key": "priority", "direction": "desc"}, {"key": "test_code", "direction": "asc"}]
        await _set_rule(client, admin_token, rule)
        resp = await client.get(f"{SETTINGS}/dep_a", headers=_hdr(admin_token))
        assert resp.json()["campaign_sort_rule"] == rule
        # Остальные поля не задеты.
        assert resp.json()["retry_enabled"] is True

    async def test_direction_defaults_to_asc(self, client, admin_token):
        await _set_rule(client, admin_token, [{"key": "kernel"}])
        resp = await client.get(f"{SETTINGS}/dep_a", headers=_hdr(admin_token))
        assert resp.json()["campaign_sort_rule"] == [{"key": "kernel", "direction": "asc"}]

    @pytest.mark.parametrize("rule", [
        [{"key": "stand", "direction": "asc"}],
        [{"key": "mode", "direction": "up"}],
        [{"key": "mode"}, {"key": "mode", "direction": "desc"}],
        [],
        None,
        [{"key": "mode", "extra": 1}],
    ])
    async def test_invalid_rule_rejected(self, client, admin_token, rule):
        resp = await client.put(f"{SETTINGS}/dep_a", headers=_hdr(admin_token), json={"campaign_sort_rule": rule})
        assert resp.status_code == 422, resp.text


def _spec(*, stand="st", code, mode="orel", kernel="6.1.0", name=None, priority=0):
    test = SimpleNamespace(id=f"tdef_{code}", code=code, full_name=name or code, priority=priority)
    return test_run_svc._EntrySpec(
        stand_id=stand, kernel=kernel, mode=mode, test=test, stp_test_run_id=None, test_case_name=name,
    )


class TestSortEntrySpecs:
    def test_legacy_rule_is_mode_kernel_case_name_and_deterministic(self):
        specs = [
            _spec(code="c1", mode="smolensk", kernel="5.15.0", name="alpha"),
            _spec(code="c2", mode="orel", kernel="6.1.0", name="beta"),
            _spec(code="c3", mode="orel", kernel="5.15.0", name="zeta"),
            _spec(code="c4", mode="orel", kernel="5.15.0", name="alpha"),
            _spec(code="c5", mode="orel", kernel="6.1.0", name="alpha"),
        ]
        expected = ["c4", "c3", "c5", "c2", "c1"]
        for _ in range(10):
            shuffled = specs[:]
            random.shuffle(shuffled)
            assert [s.test.code for s in test_run_svc.sort_entry_specs(shuffled, LEGACY_RULE)] == expected

    def test_kernel_is_compared_as_string_like_legacy_sorted(self):
        specs = [_spec(code="a", kernel="5.4.0"), _spec(code="b", kernel="5.10.0")]
        ordered = test_run_svc.sort_entry_specs(specs, [{"key": "kernel", "direction": "asc"}])
        assert [s.kernel for s in ordered] == ["5.10.0", "5.4.0"]

    def test_mixed_directions_and_ties_fall_back_to_code(self):
        specs = [
            _spec(code="b", priority=5), _spec(code="c", priority=1),
            _spec(code="a", priority=5), _spec(code="d", priority=1, kernel="5.0"),
        ]
        rule = [{"key": "priority", "direction": "desc"}, {"key": "kernel", "direction": "desc"}]
        assert [s.test.code for s in test_run_svc.sort_entry_specs(specs, rule)] == ["a", "b", "c", "d"]

    def test_unknown_key_is_ignored(self):
        specs = [_spec(code="b"), _spec(code="a")]
        ordered = test_run_svc.sort_entry_specs(specs, [{"key": "bogus"}, {"key": "test_code", "direction": "desc"}])
        assert [s.test.code for s in ordered] == ["b", "a"]


class TestCampaignOrder:
    async def test_rc_campaign_queues_stand_tests_mode_kernel_case_name(
        self, client, admin_token, mock_server_service,
    ):
        """Кампания по РЦ из активной СТП — очередь стенда режим → ядро → имя тест-кейса (легаси по умолчанию)."""
        mock_server_service()
        rc = _unique_rc()
        stand_id, _ = await _create_stand(client, admin_token)
        tag = uuid.uuid4().hex[:6]
        # Код и название теста намеренно упорядочены иначе, чем имя тест-кейса СТП.
        zeta = await _create_catalog_test(client, admin_token, stand_id, code=f"ord.a.{tag}", full_name="A-first by name")
        alpha = await _create_catalog_test(client, admin_token, stand_id, code=f"ord.z.{tag}", full_name="Z-last by name")
        smol = await _create_catalog_test(
            client, admin_token, stand_id, code=f"ord.0.{tag}", full_name="0-first by name", mode="smolensk",
        )
        titles = {zeta: "zeta benchmark", alpha: "alpha benchmark", smol: "aaa smolensk benchmark"}
        codes = {zeta: f"ord.a.{tag}", alpha: f"ord.z.{tag}", smol: f"ord.0.{tag}"}

        async with AsyncSessionLocal() as db:
            cases = {}
            for test_id, title in titles.items():
                cases[test_id] = await stp_test_case_repo.create(db, {
                    "id": stp_test_case_id(), "code": codes[test_id], "title": title, "zephyr_id": f"BT-{codes[test_id]}",
                })
            runs = {}
            for kernel, mode in (("6.1.0", "orel"), ("5.15.0", "orel"), ("5.15.0", "smolensk")):
                runs[(kernel, mode)] = await stp_test_run_repo.create(db, {
                    "id": stp_test_run_id(), "os_version_id": rc, "mode": mode, "kernel": kernel,
                    "stand_id": stand_id, "zephyr_test_run_key": f"BT-R-{kernel}-{mode}-{tag}",
                    "zephyr_folder_path": "/stress_test",
                })
            # Порядок вставки ячеек — «как попало», без ORDER BY на чтении он ничего не должен решать.
            cells = [
                (smol, ("5.15.0", "smolensk")),
                (zeta, ("6.1.0", "orel")), (alpha, ("5.15.0", "orel")),
                (alpha, ("6.1.0", "orel")), (zeta, ("5.15.0", "orel")),
            ]
            for test_id, run_key in cells:
                await stp_cell_repo.create(db, {
                    "id": stp_cell_id(), "stp_test_case_id": cases[test_id].id,
                    "stp_test_run_id": runs[run_key].id, "is_active": True,
                })
            await db.commit()

        preview = await client.post(f"{RUNS}/preview", headers=_hdr(admin_token), json={"os_version_id": rc})
        assert preview.status_code == 200, preview.text

        resp = await client.post(RUNS, headers=_hdr(admin_token), json={"os_version_id": rc})
        assert resp.status_code == 201, resp.text
        assert resp.json()["enqueue_errors"] == []

        queue = await _stand_queue(stand_id)
        order = [(item.test_id, item.launch_context["KERNEL"], item.launch_context["MODE"]) for item in queue]
        expected = [
            (alpha, "5.15.0", "orel"),
            (zeta, "5.15.0", "orel"),
            (alpha, "6.1.0", "orel"),
            (zeta, "6.1.0", "orel"),
            (smol, "5.15.0", "smolensk"),
        ]
        assert order == expected
        assert queue[0].state == QueueItemState.PREPARING
        assert all(item.state == QueueItemState.QUEUED for item in queue[1:])
        assert [item.position for item in queue] == sorted(item.position for item in queue)

        preview_order = [
            (e["test_id"], e["kernel"], e["mode"]) for e in preview.json()["entries"] if e["action"] == "launch"
        ]
        assert preview_order == expected

    async def test_department_rule_applies_to_explicit_stand_campaign(
        self, client, admin_token, mock_server_service,
    ):
        mock_server_service()
        await _set_rule(client, admin_token, [{"key": "priority", "direction": "desc"}, {"key": "test_code"}])
        stand_id, _ = await _create_stand(client, admin_token)
        tag = uuid.uuid4().hex[:6]
        low = await _create_catalog_test(client, admin_token, stand_id, code=f"exp.a.{tag}", full_name="low", priority=1)
        high_b = await _create_catalog_test(client, admin_token, stand_id, code=f"exp.c.{tag}", full_name="high b", priority=5)
        high_a = await _create_catalog_test(client, admin_token, stand_id, code=f"exp.b.{tag}", full_name="high a", priority=5)

        resp = await client.post(RUNS, headers=_hdr(admin_token), json={
            "os_version_id": "osv_1.8.5", "kernel": "6.1.0", "test_run_stands": [stand_id], "debug": True,
        })
        assert resp.status_code == 201, resp.text
        assert [item.test_id for item in await _stand_queue(stand_id)] == [high_a, high_b, low]


class TestManualModeIsFifo:
    async def test_single_and_debug_launches_ignore_campaign_rule(
        self, client, admin_token, mock_server_service,
    ):
        """Ручной режим — в порядке попадания в очередь, даже если правило отдела упорядочило бы иначе."""
        mock_server_service()
        await _set_rule(client, admin_token, [{"key": "test_code", "direction": "asc"}])
        stand_id, _ = await _create_stand(client, admin_token)
        tag = uuid.uuid4().hex[:6]
        test_c = await _create_catalog_test(client, admin_token, stand_id, code=f"fifo.c.{tag}", full_name="c")
        test_a = await _create_catalog_test(client, admin_token, stand_id, code=f"fifo.a.{tag}", full_name="a")
        test_b = await _create_catalog_test(client, admin_token, stand_id, code=f"fifo.b.{tag}", full_name="b")

        for test_id in (test_c, test_b, test_a):
            resp = await client.post(QUEUE_ITEMS, headers=_hdr(admin_token), json={
                "request_id": uuid.uuid4().hex, "test_id": test_id, "stand_id": stand_id,
                "os_version_id": "osv_1.8.5", "kernel": "6.1.0", "debug_mode": True,
                "on_active_queue": "append",
            })
            assert resp.status_code in (200, 201), resp.text

        assert [item.test_id for item in await _stand_queue(stand_id)] == [test_c, test_b, test_a]


class TestReorderQueue:
    async def _stand_with_queue(self, client, admin_token, mock_server_service, size: int = 3):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        head = await _enqueue(test_id)
        queued = [await _enqueue(test_id) for _ in range(size)]
        assert head.state == QueueItemState.PREPARING
        return stand_id, head, queued

    async def test_reorders_queued_items_and_next_follows_new_order(
        self, client, admin_token, mock_server_service, configure_internal_keys, mock_git_token, monkeypatch,
    ):
        stand_id, head, queued = await self._stand_with_queue(client, admin_token, mock_server_service)
        await mock_git_token()
        events: list[tuple[str, dict]] = []
        from src.services import audit_service
        monkeypatch.setattr(
            audit_service, "emit",
            lambda action, *args, **kwargs: events.append((action, kwargs.get("details") or {})),
        )
        new_order = [queued[2].id, queued[0].id, queued[1].id]

        resp = await client.patch(
            f"{STANDS}/{stand_id}/queue/order", headers=_hdr(admin_token), json={"queue_item_ids": new_order},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()["items"]
        assert [i["id"] for i in body] == new_order
        assert all(i["state"] == "queued" for i in body)
        assert [i["position"] for i in body] == sorted(i["position"] for i in body)

        queue = await _stand_queue(stand_id)
        assert [item.id for item in queue] == [head.id, *new_order]
        # Активный элемент не тронут.
        head_after = await _get_item(head.id)
        assert head_after.state == QueueItemState.PREPARING
        assert head_after.position == head.position

        reordered = [details for action, details in events if action == "test_stand.queue_reordered"]
        assert reordered == [{
            "stand_id": stand_id, "count": 3,
            "previous_order": [q.id for q in queued], "new_order": new_order,
        }]

        # Следующим стартует тот, кто теперь первый.
        await _drive_to_success(client, head_after)
        assert (await _get_item(queued[2].id)).state == QueueItemState.PREPARING
        assert (await _get_item(queued[0].id)).state == QueueItemState.QUEUED

    async def test_list_items_expose_position(self, client, admin_token, mock_server_service):
        stand_id, head, queued = await self._stand_with_queue(client, admin_token, mock_server_service, size=2)
        await client.patch(
            f"{STANDS}/{stand_id}/queue/order", headers=_hdr(admin_token),
            json={"queue_item_ids": [queued[1].id, queued[0].id]},
        )
        resp = await client.get(
            QUEUE_ITEMS, headers=_hdr(admin_token), params={"kind": "all", "stand_id": stand_id, "states": "queued"},
        )
        assert resp.status_code == 200, resp.text
        positions = {i["id"]: i["position"] for i in resp.json()["items"]}
        assert positions[queued[1].id] < positions[queued[0].id]

    async def test_active_item_cannot_be_reordered(self, client, admin_token, mock_server_service):
        stand_id, head, queued = await self._stand_with_queue(client, admin_token, mock_server_service, size=2)
        resp = await client.patch(
            f"{STANDS}/{stand_id}/queue/order", headers=_hdr(admin_token),
            json={"queue_item_ids": [queued[1].id, head.id, queued[0].id]},
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["error_code"] == "QUEUE_ITEM_NOT_QUEUED"
        assert [item.id for item in await _stand_queue(stand_id)] == [head.id, queued[0].id, queued[1].id]

    async def test_paused_item_cannot_be_reordered(self, client, admin_token, mock_server_service):
        stand_id, head, queued = await self._stand_with_queue(client, admin_token, mock_server_service, size=1)
        await client.post(f"{QUEUE_ITEMS}/{head.id}/pause", headers=_hdr(admin_token))
        assert (await _get_item(head.id)).state == QueueItemState.PAUSED
        resp = await client.patch(
            f"{STANDS}/{stand_id}/queue/order", headers=_hdr(admin_token),
            json={"queue_item_ids": [queued[0].id, head.id]},
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["error_code"] == "QUEUE_ITEM_NOT_QUEUED"

    async def test_stale_list_is_rejected(self, client, admin_token, mock_server_service):
        stand_id, head, queued = await self._stand_with_queue(client, admin_token, mock_server_service)
        resp = await client.patch(
            f"{STANDS}/{stand_id}/queue/order", headers=_hdr(admin_token),
            json={"queue_item_ids": [queued[1].id, queued[0].id, "qi_unknown"]},
        )
        assert resp.status_code == 409, resp.text
        body = resp.json()
        assert body["error_code"] == "QUEUE_ORDER_STALE"
        assert body["details"]["unexpected"] == ["qi_unknown"]
        assert body["details"]["missing"] == [queued[2].id]
        assert [item.id for item in await _stand_queue(stand_id)] == [head.id, *[q.id for q in queued]]

    async def test_duplicate_ids_rejected(self, client, admin_token, mock_server_service):
        stand_id, _head, queued = await self._stand_with_queue(client, admin_token, mock_server_service, size=2)
        resp = await client.patch(
            f"{STANDS}/{stand_id}/queue/order", headers=_hdr(admin_token),
            json={"queue_item_ids": [queued[0].id, queued[0].id, queued[1].id]},
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["error_code"] == "QUEUE_ORDER_DUPLICATE_IDS"

    async def test_empty_queue_empty_list_is_noop(self, client, admin_token, mock_server_service):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        resp = await client.patch(
            f"{STANDS}/{stand_id}/queue/order", headers=_hdr(admin_token), json={"queue_item_ids": []},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["items"] == []

    async def test_requires_rights_on_the_stand(
        self, client, admin_token, make_token, no_role_token, mock_server_service,
    ):
        stand_id, _head, queued = await self._stand_with_queue(client, admin_token, mock_server_service, size=2)
        other = make_token(department_id="dep_other", service_roles={"testing_service": ["admin"]})
        body = {"queue_item_ids": [queued[1].id, queued[0].id]}
        for token in (other, no_role_token):
            resp = await client.patch(f"{STANDS}/{stand_id}/queue/order", headers=_hdr(token), json=body)
            assert resp.status_code == 403, resp.text
        assert (await client.patch(f"{STANDS}/{stand_id}/queue/order", json=body)).status_code == 401
        assert (await client.patch(f"{STANDS}/tst_missing/queue/order", headers=_hdr(admin_token), json=body)).status_code == 404

"""Многостендовые сценарии — CRUD, валидация, превью, источник `stand_ref`.

Главный сценарий приёмки — превью сценария из двух стендов: в команде теста,
который идёт на первом стенде, адрес второго стенда подставлен переменной
`stand_ref`, а у второго `run_test` — своё ядро (`kernel_override`).
"""

from __future__ import annotations

import uuid

import pytest

from src.db.session import AsyncSessionLocal
from src.models import TestStand
from src.services import server_client
from tests.conftest import auth_hdr as _hdr
from tests.test_queue import (
    LAUNCH_CTX,
    TESTS_BASE,
    _create_stand,
    _create_test_def,
    mock_git_token,  # noqa: F401 — фикстура
    mock_server_service,  # noqa: F401 — фикстура
    recorded_calls,  # noqa: F401 — зависимость mock_server_service
)

BASE = "/api/testing/v1/scenarios"
VARS_BASE = "/api/testing/v1/global-variables"
STANDS_BASE = "/api/testing/v1/test-stands"

HOSTS = {}  # server_id → host, заполняет `per_stand_hosts`


@pytest.fixture
def per_stand_hosts(monkeypatch, mock_server_service):
    """У каждого стенда свой адрес: `connection-info` отвечает по `server_id`."""
    mock_server_service()
    HOSTS.clear()

    async def fake_connection_info(server_id: str) -> dict:
        return {"host": HOSTS.get(server_id, "10.0.0.5"), "port": 22}

    monkeypatch.setattr(server_client, "get_connection_info", fake_connection_info)
    return HOSTS


async def _two_stands(client, admin_token) -> tuple[str, str]:
    kd, kd_server = await _create_stand(client, admin_token, legacy_token=f"stand{uuid.uuid4().int % 90 + 10}")
    cl, cl_server = await _create_stand(client, admin_token)
    HOSTS[kd_server] = "10.1.1.1"
    HOSTS[cl_server] = "10.2.2.2"
    return kd, cl


async def _stand_in_other_department() -> str:
    stand_id = f"stand_{uuid.uuid4().hex}"
    async with AsyncSessionLocal() as db:
        db.add(TestStand(id=stand_id, server_id=f"srv_{uuid.uuid4().hex[:10]}", department_id="dep_other",
                         created_by="usr_test_fixture"))
        await db.commit()
    return stand_id


def _doc(stands: list[dict], actions: list[dict], **over) -> dict:
    body = {
        "code": f"scn.{uuid.uuid4().hex[:8]}", "name": "FreeIPA", "department_id": "dep_a",
        "stands": stands, "actions": actions,
    }
    body.update(over)
    return body


async def _stand_ref_variable(client, admin_token, stand_id: str, field: str = "host") -> str:
    code = f"PEER_{uuid.uuid4().hex[:6].upper()}"
    resp = await client.post(VARS_BASE, headers=_hdr(admin_token), json={
        "code": code, "label": "Адрес второго стенда", "source": "stand_ref",
        "source_ref": {"stand_id": stand_id, "field": field},
    })
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


class TestCrud:
    async def test_create_read_update_delete(self, client, admin_token, guest_token, per_stand_hosts):
        kd, cl = await _two_stands(client, admin_token)
        test_id = await _create_test_def(client, admin_token, None)
        body = _doc(
            [
                {"stand_id": kd, "label": "КД", "preparation": "full",
                 "stand_setup": {"kernel_cmdline_extra": ["audit=0"], "script": "echo {{RC_NAME}}"}},
                {"stand_id": cl, "label": "клиент", "preparation": "revert_only", "kernel_override": "5.15.0",
                 "mode_override": "smolensk"},
            ],
            [
                {"kind": "prepare_stand", "stand_id": kd},
                {"kind": "wait", "params": {"seconds": 30}},
                {"kind": "run_test", "stand_id": cl, "test_id": test_id, "is_verdict": True},
            ],
        )
        resp = await client.post(BASE, headers=_hdr(admin_token), json=body)
        assert resp.status_code == 201, resp.text
        created = resp.json()
        assert created["id"].startswith("scn_")
        assert [s["stand_id"] for s in created["stands"]] == [kd, cl]
        assert created["stands"][0]["target_type"] == "server"
        assert created["stands"][0]["stand_setup"]["kernel_cmdline_extra"] == ["audit=0"]
        assert created["stands"][1]["mode_override"] == "smolensk"
        assert [(a["position"], a["kind"], a["stand_id"]) for a in created["actions"]] == [
            (0, "prepare_stand", kd), (1, "wait", None), (2, "run_test", cl),
        ]
        assert created["actions"][2]["test_code"].startswith("queue.test.")

        listing = await client.get(BASE, headers=_hdr(guest_token))
        assert listing.status_code == 200
        row = next(i for i in listing.json()["items"] if i["id"] == created["id"])
        assert (row["stands_count"], row["actions_count"]) == (2, 3)

        # PUT — документ целиком: порядок действий меняется, стенд убирается.
        body["stands"] = body["stands"][1:]
        body["actions"] = [
            {"kind": "run_test", "stand_id": cl, "test_id": test_id, "is_verdict": True},
            {"kind": "prepare_stand", "stand_id": cl},
        ]
        body["readiness"] = "ready"
        resp = await client.put(f"{BASE}/{created['id']}", headers=_hdr(admin_token), json=body)
        assert resp.status_code == 200, resp.text
        updated = resp.json()
        assert updated["readiness"] == "ready"
        assert [s["stand_id"] for s in updated["stands"]] == [cl]
        assert [a["kind"] for a in updated["actions"]] == ["run_test", "prepare_stand"]

        got = await client.get(f"{BASE}/{created['id']}", headers=_hdr(guest_token))
        assert got.json()["actions"] == updated["actions"]

        assert (await client.delete(f"{BASE}/{created['id']}", headers=_hdr(admin_token))).status_code == 204
        assert (await client.get(f"{BASE}/{created['id']}", headers=_hdr(admin_token))).status_code == 404

    async def test_skip_pam_fix_round_trip(self, client, admin_token, guest_token, per_stand_hosts):
        kd, cl = await _two_stands(client, admin_token)
        test_id = await _create_test_def(client, admin_token, None)
        body = _doc(
            [
                {"stand_id": kd, "preparation": "full"},
                {"stand_id": cl, "preparation": "revert_only", "skip_pam_fix": True},
            ],
            [{"kind": "run_test", "stand_id": cl, "test_id": test_id, "is_verdict": True}],
        )
        resp = await client.post(BASE, headers=_hdr(admin_token), json=body)
        assert resp.status_code == 201, resp.text
        created = resp.json()
        assert [s["skip_pam_fix"] for s in created["stands"]] == [False, True]

        got = await client.get(f"{BASE}/{created['id']}", headers=_hdr(guest_token))
        assert [s["skip_pam_fix"] for s in got.json()["stands"]] == [False, True]

        # PUT заменяет документ целиком: флаг, не переданный в стенде, сбрасывается.
        body["stands"] = [
            {"stand_id": kd, "preparation": "full", "skip_pam_fix": True},
            {"stand_id": cl, "preparation": "revert_only"},
        ]
        resp = await client.put(f"{BASE}/{created['id']}", headers=_hdr(admin_token), json=body)
        assert resp.status_code == 200, resp.text
        assert [s["skip_pam_fix"] for s in resp.json()["stands"]] == [True, False]
        got = await client.get(f"{BASE}/{created['id']}", headers=_hdr(guest_token))
        assert [s["skip_pam_fix"] for s in got.json()["stands"]] == [True, False]

        body["stands"][0]["skip_pam_fix"] = "sometimes"
        resp = await client.put(f"{BASE}/{created['id']}", headers=_hdr(admin_token), json=body)
        assert resp.status_code == 422

    async def test_guest_cannot_write(self, client, admin_token, guest_token, per_stand_hosts):
        kd, _ = await _two_stands(client, admin_token)
        test_id = await _create_test_def(client, admin_token, None)
        body = _doc([{"stand_id": kd}], [{"kind": "run_test", "stand_id": kd, "test_id": test_id, "is_verdict": True}])
        resp = await client.post(BASE, headers=_hdr(guest_token), json=body)
        assert resp.status_code == 403

    async def test_other_department_is_isolated(self, client, admin_token, make_token, per_stand_hosts):
        kd, _ = await _two_stands(client, admin_token)
        test_id = await _create_test_def(client, admin_token, None)
        body = _doc([{"stand_id": kd}], [{"kind": "run_test", "stand_id": kd, "test_id": test_id, "is_verdict": True}])
        created = (await client.post(BASE, headers=_hdr(admin_token), json=body)).json()
        other = make_token(department_id="dep_b", service_roles={"testing_service": ["admin"]})
        assert (await client.get(f"{BASE}/{created['id']}", headers=_hdr(other))).status_code == 403
        assert (await client.delete(f"{BASE}/{created['id']}", headers=_hdr(other))).status_code == 403
        # Завести сценарий «от имени» чужого отдела нельзя.
        resp = await client.post(BASE, headers=_hdr(other), json=_doc([], [], department_id="dep_a"))
        assert resp.status_code == 403

    async def test_duplicate_code_409(self, client, admin_token, per_stand_hosts):
        kd, _ = await _two_stands(client, admin_token)
        test_id = await _create_test_def(client, admin_token, None)
        body = _doc([{"stand_id": kd}], [{"kind": "run_test", "stand_id": kd, "test_id": test_id, "is_verdict": True}])
        assert (await client.post(BASE, headers=_hdr(admin_token), json=body)).status_code == 201
        resp = await client.post(BASE, headers=_hdr(admin_token), json=body)
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "SCENARIO_DUPLICATE"


class TestValidation:
    @pytest.fixture
    async def ctx(self, client, admin_token, per_stand_hosts):
        kd, cl = await _two_stands(client, admin_token)
        test_id = await _create_test_def(client, admin_token, None)
        return kd, cl, test_id

    async def _post(self, client, admin_token, body) -> tuple[int, str]:
        resp = await client.post(BASE, headers=_hdr(admin_token), json=body)
        return resp.status_code, resp.json().get("error_code", "")

    async def test_stand_listed_twice(self, client, admin_token, ctx):
        kd, _, test_id = ctx
        body = _doc([{"stand_id": kd}, {"stand_id": kd}],
                    [{"kind": "run_test", "stand_id": kd, "test_id": test_id, "is_verdict": True}])
        assert await self._post(client, admin_token, body) == (422, "SCENARIO_STAND_DUPLICATE")

    async def test_unknown_or_foreign_stand(self, client, admin_token, ctx):
        _, _, test_id = ctx
        for stand_id in ("stand_missing", await _stand_in_other_department()):
            body = _doc([{"stand_id": stand_id}],
                        [{"kind": "run_test", "stand_id": stand_id, "test_id": test_id, "is_verdict": True}])
            assert await self._post(client, admin_token, body) == (422, "SCENARIO_STAND_INVALID")

    async def test_run_test_needs_scenario_stand_and_test(self, client, admin_token, ctx):
        kd, cl, test_id = ctx
        cases = [
            {"kind": "run_test", "test_id": test_id, "is_verdict": True},
            {"kind": "run_test", "stand_id": cl, "test_id": test_id, "is_verdict": True},  # не стенд сценария
            {"kind": "run_test", "stand_id": kd, "is_verdict": True},
        ]
        for action in cases:
            body = _doc([{"stand_id": kd}], [action])
            assert await self._post(client, admin_token, body) == (422, "SCENARIO_ACTION_INVALID"), action

    async def test_wait_and_prepare_shape(self, client, admin_token, ctx):
        kd, _, test_id = ctx
        verdict = {"kind": "run_test", "stand_id": kd, "test_id": test_id, "is_verdict": True}
        for action in (
            {"kind": "wait", "params": {}},
            {"kind": "wait", "params": {"seconds": 0}},
            {"kind": "wait", "params": {"seconds": 10}, "stand_id": kd},
            {"kind": "prepare_stand", "stand_id": kd, "is_verdict": True},
            {"kind": "prepare_stand"},
        ):
            body = _doc([{"stand_id": kd}], [action, verdict])
            assert await self._post(client, admin_token, body) == (422, "SCENARIO_ACTION_INVALID"), action

    async def test_verdict_required(self, client, admin_token, ctx):
        kd, _, test_id = ctx
        body = _doc([{"stand_id": kd}], [{"kind": "run_test", "stand_id": kd, "test_id": test_id}])
        assert await self._post(client, admin_token, body) == (422, "SCENARIO_VERDICT_MISSING")
        assert await self._post(client, admin_token, _doc([{"stand_id": kd}], [])) == (422, "SCENARIO_ACTIONS_EMPTY")

    async def test_foreign_department_test(self, client, admin_token, make_token, ctx):
        kd, _, _ = ctx
        other = make_token(department_id="dep_b", service_roles={"testing_service": ["admin"]})
        resp = await client.post(TESTS_BASE, headers=_hdr(other), json={
            "code": f"foreign.{uuid.uuid4().hex[:8]}", "full_name": "чужой", "department_id": "dep_b",
        })
        assert resp.status_code == 201, resp.text
        body = _doc([{"stand_id": kd}],
                    [{"kind": "run_test", "stand_id": kd, "test_id": resp.json()["id"], "is_verdict": True}])
        assert await self._post(client, admin_token, body) == (422, "SCENARIO_TEST_INVALID")

    async def test_platform_test_allowed(self, client, admin_token, ctx):
        kd, _, _ = ctx
        resp = await client.post(TESTS_BASE, headers=_hdr(admin_token), json={
            "code": f"platform.{uuid.uuid4().hex[:8]}", "full_name": "общий",
        })
        assert resp.status_code == 201, resp.text
        body = _doc([{"stand_id": kd}],
                    [{"kind": "run_test", "stand_id": kd, "test_id": resp.json()["id"], "is_verdict": True}])
        assert (await self._post(client, admin_token, body))[0] == 201

    async def test_department_is_immutable(self, client, admin_token, ctx):
        kd, _, test_id = ctx
        body = _doc([{"stand_id": kd}], [{"kind": "run_test", "stand_id": kd, "test_id": test_id, "is_verdict": True}])
        created = (await client.post(BASE, headers=_hdr(admin_token), json=body)).json()
        resp = await client.put(f"{BASE}/{created['id']}", headers=_hdr(admin_token), json={**body, "department_id": "dep_b"})
        assert resp.status_code == 422
        assert resp.json()["error_code"] == "SCENARIO_DEPARTMENT_IMMUTABLE"
        body["stands"][0]["preparation"] = "bogus"
        resp = await client.put(f"{BASE}/{created['id']}", headers=_hdr(admin_token), json=body)
        assert resp.status_code == 422


class TestStandRef:
    async def test_variable_validation(self, client, admin_token, per_stand_hosts):
        kd, _ = await _two_stands(client, admin_token)
        bad = [
            {"stand_id": kd, "field": "id"},
            {"stand_id": kd},
            {"stand_id": kd, "field": "host", "fallback": "number"},
            {"stand_id": "stand_missing", "field": "host"},
        ]
        for ref in bad:
            resp = await client.post(VARS_BASE, headers=_hdr(admin_token), json={
                "code": "PEER_BAD", "label": "x", "source": "stand_ref", "source_ref": ref,
            })
            assert resp.status_code == 422, ref
            assert resp.json()["error_code"] == "VARIABLE_SOURCE_REF_INVALID"
        options = (await client.get(f"{VARS_BASE}/source-options", headers=_hdr(admin_token))).json()
        assert "stand_ref" in options["sources"]
        assert options["stand_ref_fields"] == ["host", "legacy_token", "number"]

    async def test_referenced_stand_cannot_be_deleted(self, client, admin_token, per_stand_hosts):
        kd, cl = await _two_stands(client, admin_token)
        var_id = await _stand_ref_variable(client, admin_token, kd)
        resp = await client.delete(f"{STANDS_BASE}/{kd}", headers=_hdr(admin_token))
        assert resp.status_code == 409, resp.text
        assert resp.json()["error_code"] == "TEST_STAND_IN_USE"
        assert resp.json()["details"]["referenced_by_variables"]

        test_id = await _create_test_def(client, admin_token, None)
        scn = await client.post(BASE, headers=_hdr(admin_token), json=_doc(
            [{"stand_id": cl}], [{"kind": "run_test", "stand_id": cl, "test_id": test_id, "is_verdict": True}],
        ))
        resp = await client.delete(f"{STANDS_BASE}/{cl}", headers=_hdr(admin_token))
        assert resp.status_code == 409
        assert resp.json()["details"]["referenced_by_scenarios"] == [scn.json()["code"]]

        # Ссылки убраны — стенды удаляются.
        assert (await client.delete(f"{VARS_BASE}/{var_id}", headers=_hdr(admin_token))).status_code == 200
        assert (await client.delete(f"{BASE}/{scn.json()['id']}", headers=_hdr(admin_token))).status_code == 204
        assert (await client.delete(f"{STANDS_BASE}/{kd}", headers=_hdr(admin_token))).status_code == 200
        assert (await client.delete(f"{STANDS_BASE}/{cl}", headers=_hdr(admin_token))).status_code == 200


class TestPreview:
    async def test_two_stands_preview_substitutes_peer_address(
        self, client, admin_token, guest_token, per_stand_hosts, mock_git_token,
    ):
        """Приёмка: у каждого действия dates и команда, адрес второго стенда — через `stand_ref`."""
        await mock_git_token()
        kd, cl = await _two_stands(client, admin_token)
        peer_var = await _stand_ref_variable(client, admin_token, kd)
        peer_number = await _stand_ref_variable(client, admin_token, kd, field="number")
        client_test = await _create_test_def(client, admin_token, None)
        for var_id in (peer_var, peer_number):
            arg = await client.post(
                f"{TESTS_BASE}/{client_test}/args", headers=_hdr(admin_token),
                json={"kind": "variable", "variable_id": var_id},
            )
            assert arg.status_code == 201, arg.text
        server_test = await _create_test_def(client, admin_token, None)

        scn = await client.post(BASE, headers=_hdr(admin_token), json=_doc(
            [
                {"stand_id": kd, "label": "КД",
                 "stand_setup": {"script": "ipa-server-install --hostname {{RC_NAME}}"}},
                {"stand_id": cl, "label": "клиент", "kernel_override": "5.15.0-custom"},
            ],
            [
                {"kind": "prepare_stand", "stand_id": kd},
                {"kind": "run_test", "stand_id": kd, "test_id": server_test},
                {"kind": "wait", "params": {"seconds": 60}},
                {"kind": "run_test", "stand_id": cl, "test_id": client_test, "is_verdict": True},
            ],
        ))
        assert scn.status_code == 201, scn.text

        resp = await client.post(
            f"{BASE}/{scn.json()['id']}/preview", headers=_hdr(guest_token),
            json={"os_version_id": LAUNCH_CTX["RC"], "kernel": LAUNCH_CTX["KERNEL"]},
        )
        assert resp.status_code == 200, resp.text
        actions = resp.json()["actions"]
        assert [a["kind"] for a in actions] == ["prepare_stand", "run_test", "wait", "run_test"]
        assert all(a["errors"] == [] for a in actions), actions

        prepare = actions[0]["stand"]
        assert prepare["preparation"] == "full" and prepare["kernel"] == LAUNCH_CTX["KERNEL"]
        assert prepare["stand_setup"]["script"].startswith("ipa-server-install --hostname ")
        assert "{{" not in prepare["stand_setup"]["script"]

        on_kd, on_client = actions[1]["launch"], actions[3]["launch"]
        assert on_kd["stand_id"] == kd and on_kd["dates_content_masked"] == "--run"
        # Тест клиента идёт на втором стенде, но адрес в dates — КД (stand_ref).
        assert on_client["stand_id"] == cl
        kd_number = "".join(ch for ch in on_client["dates_content_masked"].split()[-1] if ch.isdigit())
        assert on_client["dates_content_masked"].split()[:2] == ["--run", "10.1.1.1"]
        assert kd_number
        assert on_client["launch_context"]["KERNEL"] == "5.15.0-custom"
        assert on_client["launch_command_masked"]
        assert actions[2]["params"] == {"seconds": 60} and actions[3]["is_verdict"] is True

    async def test_foreign_scenario_preview_denied(self, client, admin_token, make_token, per_stand_hosts):
        kd, _ = await _two_stands(client, admin_token)
        test_id = await _create_test_def(client, admin_token, None)
        scn = (await client.post(BASE, headers=_hdr(admin_token), json=_doc(
            [{"stand_id": kd}], [{"kind": "run_test", "stand_id": kd, "test_id": test_id, "is_verdict": True}],
        ))).json()
        other = make_token(department_id="dep_b", service_roles={"testing_service": ["admin"]})
        resp = await client.post(
            f"{BASE}/{scn['id']}/preview", headers=_hdr(other),
            json={"os_version_id": LAUNCH_CTX["RC"], "kernel": LAUNCH_CTX["KERNEL"]},
        )
        assert resp.status_code == 403

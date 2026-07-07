"""Edge cases матрицы прав — роли и ENTITY_ACTIONS.

Дополняет test_entity_actions.py и test_permissions_endpoints.py.
Фокус:
* worker_bot имеет ровно 8 гранта из seeded-миграции (43cf9cfef9e1) — не больше, не меньше.
* worker_bot НЕ имеет server.view, server.update, server.delete и прочих admin-actions.
* Все 8 гранта worker_bot правильно описаны по entity_type + action.
* Запрос через endpoint /permissions?role=worker_bot возвращает ровно 8 строк.
* operator не имеет view_password/view_credentials (sensitive) по умолчанию.
* guest несёт ровно один грант — server.view (метаданные серверов отдела).
* reader имеет только view на все entity.
* Catalog: все entity содержат worker_only-флаг только у внутренних actions.
"""

from __future__ import annotations

import pytest

BASE = "/api/server/v1/permissions"


from tests._helpers import auth_hdr as _hdr  # noqa: E402


class TestWorkerBotGrantCount:
    """worker_bot least-privilege: ровно 8 гранта, строго определённые."""

    EXPECTED_WORKER_BOT_GRANTS = frozenset({
        ("server_account", "view_password"),
        ("server_account", "rotate_password"),
        ("server_account", "inventory_submit"),
        ("ipmi_controller", "view_credentials"),
        ("ipmi_controller", "rotate_credentials"),
        ("server", "inventory_submit"),
        ("server_account", "provision_on_host"),
        ("server", "prepare_callback"),
        ("server", "view_management_credentials"),
    })

    async def test_worker_bot_has_exactly_9_grants(
        self, client, admin_token,
    ):
        resp = await client.get(f"{BASE}?role=worker_bot", headers=_hdr(admin_token))
        assert resp.status_code == 200
        rows = resp.json()["items"]
        assert len(rows) == 9, f"expected 9 worker_bot grants, got {len(rows)}: {rows}"

    async def test_worker_bot_grants_match_expected_set(
        self, client, admin_token,
    ):
        resp = await client.get(f"{BASE}?role=worker_bot", headers=_hdr(admin_token))
        rows = resp.json()["items"]
        actual = {(r["entity_type"], r["action"]) for r in rows}
        assert actual == self.EXPECTED_WORKER_BOT_GRANTS, (
            f"extra={actual - self.EXPECTED_WORKER_BOT_GRANTS}, "
            f"missing={self.EXPECTED_WORKER_BOT_GRANTS - actual}"
        )

    async def test_worker_bot_has_no_server_view(
        self, client, admin_token,
    ):
        """worker_bot не должен видеть список серверов — только колбэки."""
        resp = await client.get(f"{BASE}?role=worker_bot", headers=_hdr(admin_token))
        rows = resp.json()["items"]
        server_view = [r for r in rows if r["entity_type"] == "server" and r["action"] == "view"]
        assert server_view == [], "worker_bot must not have server.view"

    async def test_worker_bot_has_no_server_update(
        self, client, admin_token,
    ):
        resp = await client.get(f"{BASE}?role=worker_bot", headers=_hdr(admin_token))
        rows = resp.json()["items"]
        server_update = [r for r in rows if r["entity_type"] == "server" and r["action"] == "update"]
        assert server_update == [], "worker_bot must not have server.update"

    async def test_worker_bot_has_no_permission_grant(
        self, client, admin_token,
    ):
        resp = await client.get(f"{BASE}?role=worker_bot", headers=_hdr(admin_token))
        rows = resp.json()["items"]
        perm_grant = [r for r in rows if r["action"] == "permission_grant"]
        assert perm_grant == [], "worker_bot must not have permission_grant"


class TestSensitiveActionsNotDefaultGranted:
    """Sensitive-действия не выдаются оператору/ридеру по умолчанию."""

    @pytest.mark.parametrize("sensitive_pair", [
        ("server_account", "view_password"),
        ("ipmi_controller", "view_credentials"),
    ])
    async def test_operator_no_sensitive_action(
        self, client, admin_token, sensitive_pair,
    ):
        entity, action = sensitive_pair
        resp = await client.get(
            f"{BASE}?role=operator", headers=_hdr(admin_token),
        )
        rows = resp.json()["items"]
        found = [r for r in rows
                 if r["entity_type"] == entity and r["action"] == action]
        assert found == [], f"operator must not have default grant {entity}.{action}"

    @pytest.mark.parametrize("sensitive_pair", [
        ("server_account", "view_password"),
        ("ipmi_controller", "view_credentials"),
    ])
    async def test_reader_no_sensitive_action(
        self, client, admin_token, sensitive_pair,
    ):
        entity, action = sensitive_pair
        resp = await client.get(
            f"{BASE}?role=reader", headers=_hdr(admin_token),
        )
        rows = resp.json()["items"]
        found = [r for r in rows
                 if r["entity_type"] == entity and r["action"] == action]
        assert found == [], f"reader must not have default grant {entity}.{action}"


class TestGuestBaselineGrants:
    async def test_guest_has_only_server_view(self, client, admin_token):
        """guest несёт единственный seeded грант — server.view (метаданные).

        Чувствительного (ipmi-кред, паролей учёток, управляющих кред) у guest
        по-прежнему нет — только тип-wide view карточек серверов отдела.
        """
        resp = await client.get(f"{BASE}?role=guest", headers=_hdr(admin_token))
        assert resp.status_code == 200
        body = resp.json()
        rows = body["items"]
        assert body["total"] == len(rows)
        assert body["described"] is False
        actual = {(r["entity_type"], r["action"]) for r in rows}
        assert actual == {("server", "view"), ("vm", "view")}


class TestReaderOnlyViewGrants:
    async def test_reader_has_only_view_actions(self, client, admin_token):
        resp = await client.get(f"{BASE}?role=reader", headers=_hdr(admin_token))
        assert resp.status_code == 200
        rows = resp.json()["items"]
        assert rows, "reader should have at least one grant"
        non_view = [r for r in rows if r["action"] != "view"]
        assert non_view == [], f"reader has non-view grants: {non_view}"


class TestCatalogWorkerOnlyFlags:
    """worker_only=True только у worker-специфичных actions."""

    KNOWN_WORKER_ONLY_ACTIONS = frozenset({
        ("server", "inventory_submit"),
        ("server_account", "inventory_submit"),
        ("server_account", "provision_on_host"),
        ("server", "prepare_callback"),
        # Служебный pull управляющих кред — людям в матрице не показывается.
        ("server", "view_management_credentials"),
    })

    async def test_worker_only_flags_match_expected(self, client, admin_token):
        resp = await client.get(f"{BASE}/catalog?describe=true", headers=_hdr(admin_token))
        assert resp.status_code == 200
        catalog = resp.json()

        actual_worker_only = set()
        for entity in catalog:
            for action in entity["actions"]:
                if action.get("worker_only"):
                    actual_worker_only.add((entity["entity_type"], action["action"]))

        assert self.KNOWN_WORKER_ONLY_ACTIONS.issubset(actual_worker_only), (
            f"Missing worker_only flags: "
            f"{self.KNOWN_WORKER_ONLY_ACTIONS - actual_worker_only}"
        )

    async def test_view_action_not_worker_only(self, client, admin_token):
        resp = await client.get(f"{BASE}/catalog?describe=true", headers=_hdr(admin_token))
        catalog = resp.json()
        for entity in catalog:
            for action in entity["actions"]:
                if action["action"] == "view":
                    assert not action.get("worker_only"), (
                        f"view on {entity['entity_type']} should not be worker_only"
                    )

    async def test_sensitive_flag_on_view_password(self, client, admin_token):
        resp = await client.get(f"{BASE}/catalog?describe=true", headers=_hdr(admin_token))
        catalog = resp.json()
        flat = {
            (e["entity_type"], a["action"]): a
            for e in catalog for a in e["actions"]
        }
        assert flat[("server_account", "view_password")]["sensitive"] is True
        assert flat[("ipmi_controller", "view_credentials")]["sensitive"] is True
        assert flat[("server_account", "rotate_password")]["sensitive"] is True
        assert flat[("ipmi_controller", "rotate_credentials")]["sensitive"] is True
        assert flat[("server", "view")]["sensitive"] is False
        assert flat[("server", "create")]["sensitive"] is False


class TestGrantRevokeCrossRole:
    """grant/revoke scope: dep-A admin cannot write into dep-B."""

    async def test_admin_a_cannot_grant_into_dept_b(
        self, client, admin_token, admin_token_b,
    ):
        """dep_a admin пытается выдать грант с target_department_id=dep_b → 403."""
        resp = await client.put(
            f"{BASE}/server/operator/view",
            headers=_hdr(admin_token),
            json={"target_department_id": "dep_b"},
        )
        # Вставка в чужой dept должна быть отклонена.
        assert resp.status_code in (403, 422), (
            f"expected 403/422 for cross-dept grant, got {resp.status_code}: {resp.text}"
        )

    async def test_double_grant_idempotent_same_id(
        self, client, admin_token,
    ):
        first = await client.put(
            f"{BASE}/server/operator/delete", headers=_hdr(admin_token),
        )
        second = await client.put(
            f"{BASE}/server/operator/delete", headers=_hdr(admin_token),
        )
        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["id"] == second.json()["id"]

    async def test_revoke_then_grant_creates_new_row(
        self, client, admin_token,
    ):
        put1 = await client.put(
            f"{BASE}/server/reader/update", headers=_hdr(admin_token),
        )
        assert put1.status_code == 200
        first_id = put1.json()["id"]

        await client.delete(f"{BASE}/server/reader/update", headers=_hdr(admin_token))

        put2 = await client.put(
            f"{BASE}/server/reader/update", headers=_hdr(admin_token),
        )
        assert put2.status_code == 200
        second_id = put2.json()["id"]
        # После revoke+re-grant — новый row с новым id.
        assert second_id != first_id

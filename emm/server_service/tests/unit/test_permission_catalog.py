"""Unit-тесты каталога описаний прав `src/core/permission_catalog.py`.

Главный инвариант: каждая пара `(entity_type, action)` из `ENTITY_ACTIONS`
имеет непустое описание сущности и действия. Если в матрицу добавили action
без описания — тест падает.
"""

from __future__ import annotations

import pytest

from src.core.constants import ENTITY_ACTIONS, EntityType
from src.core.permission_catalog import (
    ACTION_DESCRIPTIONS,
    ENTITY_DESCRIPTIONS,
    SENSITIVE_ACTIONS,
    WORKER_CALLBACK_ACTIONS,
    WORKER_ONLY_ACTIONS,
)
from src.services.permission_service import build_catalog


class TestDescriptionsCoverage:
    @pytest.mark.parametrize("entity_type", [e.value for e in EntityType])
    def test_every_entity_has_nonempty_description(self, entity_type):
        assert ENTITY_DESCRIPTIONS.get(entity_type, "").strip()

    def test_every_action_in_matrix_has_nonempty_description(self):
        missing = []
        for actions in ENTITY_ACTIONS.values():
            for action in actions:
                if not ACTION_DESCRIPTIONS.get(action, "").strip():
                    missing.append(action)
        assert missing == [], f"actions without description: {sorted(set(missing))}"


class TestFlagSets:
    def test_sensitive_actions_are_known_secrets_power_sudo(self):
        expected = {
            "view_password", "view_credentials",
            "rotate_password", "rotate_credentials",
            "grant_sudo", "power_on", "power_off", "power_reboot",
            "console", "manage_packages",
            "view_management_credentials",
            "acs_snapshot",
        }
        assert set(SENSITIVE_ACTIONS) == expected

    def test_worker_callback_actions(self):
        expected = {"inventory_submit", "provision_on_host", "prepare_callback"}
        assert set(WORKER_CALLBACK_ACTIONS) == expected

    def test_worker_only_actions_extend_callbacks_with_mgmt_creds(self):
        expected = {
            "inventory_submit", "provision_on_host", "prepare_callback",
            "view_management_credentials",
        }
        assert set(WORKER_ONLY_ACTIONS) == expected
        # mgmt-creds pull — служебный, но не callback; в callback-набор не входит.
        assert WORKER_CALLBACK_ACTIONS < WORKER_ONLY_ACTIONS

    def test_flag_sets_only_reference_actions_in_matrix(self):
        all_actions = {a for actions in ENTITY_ACTIONS.values() for a in actions}
        assert SENSITIVE_ACTIONS <= all_actions
        assert WORKER_CALLBACK_ACTIONS <= all_actions
        assert WORKER_ONLY_ACTIONS <= all_actions


class TestBuildCatalog:
    def test_covers_all_entities(self):
        catalog = build_catalog()
        got = {e["entity_type"] for e in catalog}
        assert got == {e.value for e in EntityType}

    def test_actions_match_entity_actions_per_entity(self):
        catalog = build_catalog()
        for entity in catalog:
            actions = {a["action"] for a in entity["actions"]}
            assert actions == set(ENTITY_ACTIONS[entity["entity_type"]])

    def test_descriptions_nonempty_for_every_action(self):
        catalog = build_catalog()
        for entity in catalog:
            assert entity["description"].strip()
            for action in entity["actions"]:
                assert action["description"].strip()

    def test_sensitive_and_worker_flags_match_sets(self):
        catalog = build_catalog()
        for entity in catalog:
            for action in entity["actions"]:
                assert action["sensitive"] == (action["action"] in SENSITIVE_ACTIONS)
                assert action["worker_only"] == (action["action"] in WORKER_ONLY_ACTIONS)

    def test_view_password_is_sensitive_not_worker(self):
        catalog = build_catalog()
        sa = next(e for e in catalog if e["entity_type"] == "server_account")
        vp = next(a for a in sa["actions"] if a["action"] == "view_password")
        assert vp["sensitive"] is True
        assert vp["worker_only"] is False

    def test_inventory_submit_is_worker_only(self):
        catalog = build_catalog()
        srv = next(e for e in catalog if e["entity_type"] == "server")
        inv = next(a for a in srv["actions"] if a["action"] == "inventory_submit")
        assert inv["worker_only"] is True
        assert inv["sensitive"] is False

    def test_view_management_credentials_is_worker_only_and_sensitive(self):
        catalog = build_catalog()
        srv = next(e for e in catalog if e["entity_type"] == "server")
        vmc = next(
            a for a in srv["actions"] if a["action"] == "view_management_credentials"
        )
        assert vmc["worker_only"] is True
        assert vmc["sensitive"] is True

    def test_plain_action_no_flags(self):
        catalog = build_catalog()
        srv = next(e for e in catalog if e["entity_type"] == "server")
        view = next(a for a in srv["actions"] if a["action"] == "view")
        assert view["sensitive"] is False
        assert view["worker_only"] is False

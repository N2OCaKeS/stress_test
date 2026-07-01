"""Unit-тесты каталога описаний прав `src/core/permission_catalog.py`.

Главный инвариант: каждая пара `(entity_type, action)` из `ENTITY_ACTIONS`
имеет непустое описание сущности и действия, а флаг `sensitive` в собранном
каталоге совпадает с `SENSITIVE_ACTIONS`.
"""

from __future__ import annotations

import pytest

from src.core.constants import ENTITY_ACTIONS, EntityType, SecretAction
from src.core.permission_catalog import (
    ACTION_DESCRIPTIONS,
    ENTITY_DESCRIPTIONS,
    SENSITIVE_ACTIONS,
)
from src.services.permission_service import build_catalog


# БД тут не нужна — перебиваем session-fixture из tests/unit/conftest.py.
@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    yield


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
    def test_sensitive_actions_are_reveal_and_privileged(self):
        expected = {
            SecretAction.REVEAL,
            SecretAction.DELETE,
            SecretAction.GRANT_ACL,
            SecretAction.GRANT_DEPT,
            SecretAction.MANAGE_STATUS,
        }
        assert set(SENSITIVE_ACTIONS) == expected

    def test_sensitive_only_references_matrix_actions(self):
        all_actions = {a for actions in ENTITY_ACTIONS.values() for a in actions}
        assert SENSITIVE_ACTIONS <= all_actions

    def test_list_guest_not_in_matrix(self):
        assert SecretAction.LIST_GUEST not in ENTITY_ACTIONS[EntityType.SECRET]


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

    def test_sensitive_flags_match_set(self):
        catalog = build_catalog()
        for entity in catalog:
            for action in entity["actions"]:
                assert action["sensitive"] == (action["action"] in SENSITIVE_ACTIONS)

    def test_reveal_sensitive_read_and_write_not(self):
        catalog = build_catalog()
        flat = {a["action"]: a for e in catalog for a in e["actions"]}
        assert flat["reveal"]["sensitive"] is True
        assert flat["read"]["sensitive"] is False
        assert flat["write"]["sensitive"] is False

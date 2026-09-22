"""Unit-тесты least-privilege grants для роли `allta_bridge`.

Роль выдаётся внешнему боту `allta_app_service`: резолвит номер стенда в
server_id и читает расшифрованные IPMI-credentials, чтобы дёргать iLO.
Миграция `ad5f3fcf1352_seed_allta_bridge_entity_permissions.py` должна
сидеть ровно 2 строки entity_permissions для role=`allta_bridge` и ничего
лишнего:

  (server, view)
  (ipmi_controller, view_credentials)
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

from src.core.constants import is_valid_action

_MIGRATIONS_DIR = (
    pathlib.Path(__file__).resolve().parents[2]
    / "src" / "db" / "migrations" / "versions"
)
_DEFAULT_SEED_PATH = _MIGRATIONS_DIR / "831ba55543e9_seed_default_entity_permissions.py"
_ALLTA_BRIDGE_SEED_PATH = _MIGRATIONS_DIR / "ad5f3fcf1352_seed_allta_bridge_entity_permissions.py"


def _load(path: pathlib.Path, mod_name: str):
    spec = importlib.util.spec_from_file_location(mod_name, path)
    assert spec and spec.loader, f"migration not found at {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def allta_bridge_seed():
    return _load(_ALLTA_BRIDGE_SEED_PATH, "allta_bridge_seed_mod")


@pytest.fixture(scope="module")
def default_seed():
    return _load(_DEFAULT_SEED_PATH, "allta_bridge_default_seed_mod")


class TestAlltaBridgeGrants:
    EXPECTED: set[tuple[str, str]] = {
        ("server", "view"),
        ("ipmi_controller", "view_credentials"),
    }

    def test_grants_match_least_privilege_whitelist_exactly(self, allta_bridge_seed):
        got = set(allta_bridge_seed.ALLTA_BRIDGE_GRANTS)
        assert got == self.EXPECTED

    def test_grant_count_is_exactly_two(self, allta_bridge_seed):
        assert len(allta_bridge_seed.ALLTA_BRIDGE_GRANTS) == 2

    def test_no_duplicate_grants(self, allta_bridge_seed):
        grants = allta_bridge_seed.ALLTA_BRIDGE_GRANTS
        assert len(grants) == len(set(grants))

    def test_every_grant_passes_is_valid_action(self, allta_bridge_seed):
        invalid = [
            (entity_type, action)
            for entity_type, action in allta_bridge_seed.ALLTA_BRIDGE_GRANTS
            if not is_valid_action(entity_type, action)
        ]
        assert invalid == [], f"unwhitelisted allta_bridge grants: {invalid}"


class TestAlltaBridgeForbiddenActions:
    @pytest.mark.parametrize("forbidden", [
        ("server", "create"),
        ("server", "update"),
        ("server", "delete"),
        ("server", "power_on"),
        ("server", "power_off"),
        ("server", "power_reboot"),
        ("server_account", "view_password"),
        ("server_account", "rotate_password"),
        ("ipmi_controller", "view"),
        ("ipmi_controller", "rotate_credentials"),
        ("ipmi_controller", "create"),
        ("ipmi_controller", "update"),
        ("ipmi_controller", "delete"),
        ("permission", "permission_grant"),
        ("permission", "permission_revoke"),
    ])
    def test_forbidden_action_not_in_allta_bridge_grants(self, allta_bridge_seed, forbidden):
        got = set(allta_bridge_seed.ALLTA_BRIDGE_GRANTS)
        assert forbidden not in got, (
            f"allta_bridge must not receive {forbidden} — за пределами "
            f"«резолвить номер + читать IPMI-креды»"
        )


class TestAlltaBridgeIsolation:
    def test_allta_bridge_role_not_present_in_default_seed(self, default_seed):
        grants = default_seed._grants()
        roles = {role for (_e, role, _a) in grants}
        assert "allta_bridge" not in roles, (
            "allta_bridge не должен сидиться в default seed — он живёт "
            "в отдельной миграции ad5f3fcf1352_…"
        )

    def test_allta_bridge_grants_strictly_smaller_than_admin(self, allta_bridge_seed, default_seed):
        admin_pairs = {
            (entity_type, action)
            for (entity_type, role, action) in default_seed._grants()
            if role == "admin"
        }
        allta_bridge_pairs = set(allta_bridge_seed.ALLTA_BRIDGE_GRANTS)
        assert allta_bridge_pairs.issubset(admin_pairs)
        assert len(allta_bridge_pairs) < len(admin_pairs)


class TestAlltaBridgeMigrationShape:
    def test_revision_chains_after_stand_number_migration(self, allta_bridge_seed):
        assert allta_bridge_seed.down_revision == "36078ae57d75"

    def test_downgrade_targets_only_allta_bridge(self, allta_bridge_seed):
        import inspect
        src = inspect.getsource(allta_bridge_seed.downgrade)
        assert "allta_bridge" in src
        for forbidden_role in ("admin", "reader", "operator", "guest", "worker_bot"):
            assert forbidden_role not in src, (
                f"downgrade() trog'аеt роль '{forbidden_role}' — это ошибка"
            )

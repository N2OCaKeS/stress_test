"""Unit-тесты whitelist `ENTITY_ACTIONS` и default-grants миграции.

Цель:
* `is_valid_action` корректно отвергает неизвестные пары.
* `ENTITY_ACTIONS` в `core/constants.py` — superset baseline-сеяния
  `_ALL_ACTIONS`. После выноса service_role-management в auth_service
  baseline сеет `permission`-entity (view/permission_grant/permission_revoke)
  вместо прежнего `service_role`-entity.
* Default grants: admin 45, reader 7, operator 31, guest 0; нет дублей;
  каждый grant проходит `is_valid_action`.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

from src.core.constants import ENTITY_ACTIONS, EntityType, is_valid_action


# ── Загрузка seed-миграции как модуля (имя файла начинается с цифры) ────────

_MIGRATION_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "src" / "db" / "migrations" / "versions"
    / "831ba55543e9_seed_default_entity_permissions.py"
)


def _load_seed_migration():
    spec = importlib.util.spec_from_file_location("seed_grants_mod", _MIGRATION_PATH)
    assert spec and spec.loader, f"seed migration not found at {_MIGRATION_PATH}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def seed():
    return _load_seed_migration()


# ── is_valid_action ──────────────────────────────────────────────────────────

class TestIsValidAction:
    def test_valid_pair_returns_true(self):
        assert is_valid_action("server", "view") is True
        assert is_valid_action("server_account", "rotate_password") is True

    def test_unknown_entity_returns_false(self):
        assert is_valid_action("nonexistent_entity", "view") is False

    def test_unknown_action_returns_false(self):
        assert is_valid_action("server", "UNKNOWN_ACTION") is False

    def test_empty_strings(self):
        assert is_valid_action("", "") is False
        assert is_valid_action("server", "") is False
        assert is_valid_action("", "view") is False

    @pytest.mark.parametrize("entity_type", [e.value for e in EntityType])
    def test_every_declared_entity_type_present_in_whitelist(self, entity_type):
        assert entity_type in ENTITY_ACTIONS

    def test_whitelist_has_no_unknown_entities(self):
        declared = {e.value for e in EntityType}
        assert set(ENTITY_ACTIONS).issubset(declared)


# ── Sync между constants.ENTITY_ACTIONS и migration _ALL_ACTIONS ─────────────

class TestConstantsMigrationSync:
    def test_all_actions_keys_match_entity_actions_keys(self, seed):
        """Baseline seed мог содержать сущности, которые были выпилены
        follow-on миграциями (например `cpu_model` удалена миграцией
        `b6f3a91d27e8`). Live `ENTITY_ACTIONS` должен быть подмножеством
        ключей baseline'а, но не обязательно равенством.
        """
        assert set(ENTITY_ACTIONS).issubset(set(seed._ALL_ACTIONS))

    def test_action_sets_match_per_entity(self, seed):
        """Baseline seed `_ALL_ACTIONS` — снимок матрицы на момент 831ba55543e9.

        `ENTITY_ACTIONS` в `constants.py` со временем менялся: actions
        добавлялись (`a2c1d8e4b9f5_add_reveal_password_grants`,
        `b8d4e3f9a712_…` добавил `reveal_credentials`), удалялись (тот же
        `b8d4e3f9a712_…` снял `boot_order_*`/`pxe_boot`/`reinstall_*`),
        entity_type'ы тоже выпадали (`b6f3a91d27e8_drop_cpu_models_…`,
        `c8e4f6a9b1d2_drop_installed_packages_table`). Поэтому равенство
        не требуется — baseline без removed-actions должен быть подмножеством.
        Размер самого baseline-сеяния защищён `test_total_count_matches_status_md`.
        """
        # Actions, выпиленные follow-on миграциями после 831ba55543e9 — baseline
        # их ещё содержит, но в текущей constants.ENTITY_ACTIONS их нет.
        removed_actions: dict[str, set[str]] = {
            "server": {
                "boot_order_view", "boot_order_set",
                "pxe_boot", "reinstall_start", "reinstall_status_submit",
            },
        }
        for entity_type, actions in ENTITY_ACTIONS.items():
            migration_actions = set(seed._ALL_ACTIONS[entity_type])
            migration_actions -= removed_actions.get(entity_type, set())
            constants_actions = set(actions)
            assert migration_actions.issubset(constants_actions), (
                f"Mismatch for '{entity_type}': baseline seed (minus removed) "
                f"contains {migration_actions - constants_actions} not present in constants"
            )

    def test_operator_grants_are_subset_of_all_actions(self, seed):
        for entity_type, actions in seed._OPERATOR_GRANTS.items():
            assert entity_type in seed._ALL_ACTIONS, f"unknown entity '{entity_type}' in _OPERATOR_GRANTS"
            assert set(actions).issubset(set(seed._ALL_ACTIONS[entity_type])), (
                f"_OPERATOR_GRANTS['{entity_type}'] is not subset of _ALL_ACTIONS"
            )


# ── Default grants — счёт и состав ───────────────────────────────────────────

class TestDefaultGrants:
    def test_no_duplicate_grants(self, seed):
        grants = seed._grants()
        assert len(grants) == len(set(grants))

    def test_total_count_matches_status_md(self, seed):
        """Размер baseline'а — инвариант, который не должен ломаться
        без явного обновления документации.

        После удаления service_role-entity (5 admin + 1 reader + 1 operator =
        7 rows) и переезда на permission-entity (3 admin + 1 reader + 1 operator =
        5 rows) общее число уменьшается на 2 относительно legacy-варианта.
        """
        assert len(seed._grants()) == 82

    def test_admin_gets_every_action_of_every_entity(self, seed):
        grants = seed._grants()
        admin = {(e, a) for (e, r, a) in grants if r == "admin"}
        expected = {
            (entity_type, action)
            for entity_type, actions in seed._ALL_ACTIONS.items()
            for action in actions
        }
        assert admin == expected

    def test_admin_total(self, seed):
        admin = [g for g in seed._grants() if g[1] == "admin"]
        # 17 (server) + 7 (server_account) + 4 (os_version) + 6 (ipmi_controller)
        # + 4 (cpu_model) + 4 (disk) + 3 (permission) = 45.
        assert len(admin) == 45

    def test_reader_only_gets_view(self, seed):
        reader = [g for g in seed._grants() if g[1] == "reader"]
        assert all(a == "view" for (_e, _r, a) in reader)
        # 7 entities имеют `view`: server / server_account / os_version /
        # ipmi_controller / cpu_model / disk / permission.
        assert len(reader) == 7

    def test_operator_grants_match_explicit_table(self, seed):
        grants = seed._grants()
        operator = {(e, a) for (e, r, a) in grants if r == "operator"}
        expected = {
            (entity_type, action)
            for entity_type, actions in seed._OPERATOR_GRANTS.items()
            for action in actions
        }
        assert operator == expected

    def test_guest_has_no_grants(self, seed):
        guest = [g for g in seed._grants() if g[1] == "guest"]
        assert guest == []

    def test_every_grant_passes_is_valid_action(self, seed):
        """Любая пара (entity_type, action) в seed должна быть в whitelist —
        кроме сущностей и actions, выпиленных follow-on миграциями
        (`cpu_model` удалена `b6f3a91d27e8`; boot_order/pxe_boot/reinstall_*
        удалены `b8d4e3f9a712`). Их seed-строки удаляются на upgrade-step'е
        соответствующей миграции, но в baseline-снимке остаются.
        """
        removed_entities = {"cpu_model"}
        removed_actions_server = {
            "boot_order_view", "boot_order_set",
            "pxe_boot", "reinstall_start",
        }
        invalid = []
        for entity, role, action in seed._grants():
            if entity in removed_entities:
                continue
            if entity == "server" and action in removed_actions_server:
                continue
            if not is_valid_action(entity, action):
                invalid.append((entity, role, action))
        assert invalid == [], f"unwhitelisted grants in seed: {invalid}"


# ── Sensitive actions never default-granted to operator ──────────────────────

class TestSensitiveActionsClosed:
    @pytest.mark.parametrize("sensitive", [
        ("server_account", "view_password"),
        ("server_account", "grant_sudo"),
        ("ipmi_controller", "view_credentials"),
    ])
    def test_operator_does_not_have_sensitive_action_by_default(self, seed, sensitive):
        operator = {(e, a) for (e, r, a) in seed._grants() if r == "operator"}
        assert sensitive not in operator, (
            f"{sensitive} must be opt-in via explicit /permissions grant, "
            f"not part of operator defaults"
        )

    def test_operator_can_rotate_but_not_view_password(self, seed):
        """Ротация позволена (нужна для рабочей операционки), а просмотр plaintext — нет."""
        operator = {(e, a) for (e, r, a) in seed._grants() if r == "operator"}
        assert ("server_account", "rotate_password") in operator
        assert ("server_account", "view_password") not in operator

    def test_permission_management_actions_admin_only(self, seed):
        """permission_grant/revoke — не должны быть у reader/operator/guest.

        Service-role management (role_create/role_delete) живёт только в
        auth_service — у server_service entity-типа под него нет вообще.
        """
        for forbidden_role in ("reader", "operator", "guest"):
            grants = {(e, a) for (e, r, a) in seed._grants() if r == forbidden_role}
            for management_action in ("permission_grant", "permission_revoke"):
                assert ("permission", management_action) not in grants, (
                    f"{forbidden_role} must not get permission/{management_action}"
                )

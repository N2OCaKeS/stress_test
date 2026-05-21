"""Unit-тесты least-privilege grants для роли `worker_bot`.

Закрывает баг «worker_bot_token PAT = глобальный admin». Проверяет, что
миграция `43cf9cfef9e1_seed_worker_bot_entity_permissions.py` сидит ровно
4 строки entity_permissions для role=`worker_bot` и ничего лишнего:

  (server_account, view_password)
  (server_account, rotate_password)
  (ipmi_controller, view_credentials)
  (ipmi_controller, rotate_credentials)

Опасные действия (`server.delete`, `permission.grant`, `power_on`, …) НЕ
должны попадать в worker_bot ни через эту, ни через `831ba55543e9_…`
миграцию (которая сидит только admin/reader/operator/guest).

Тесты работают с обеими миграциями: подтверждают, что общий набор default
grants после применения двух seed-миграций корректен и worker_bot не
пересекается с admin/operator/reader через побочные effect'ы.
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

from src.core.constants import is_valid_action


# ── Загрузка обеих seed-миграций как модулей ─────────────────────────────────

_MIGRATIONS_DIR = (
    pathlib.Path(__file__).resolve().parents[2]
    / "src" / "db" / "migrations" / "versions"
)
_DEFAULT_SEED_PATH = _MIGRATIONS_DIR / "831ba55543e9_seed_default_entity_permissions.py"
_WORKER_BOT_SEED_PATH = _MIGRATIONS_DIR / "43cf9cfef9e1_seed_worker_bot_entity_permissions.py"


def _load(path: pathlib.Path, mod_name: str):
    spec = importlib.util.spec_from_file_location(mod_name, path)
    assert spec and spec.loader, f"migration not found at {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def worker_bot_seed():
    return _load(_WORKER_BOT_SEED_PATH, "worker_bot_seed_mod")


@pytest.fixture(scope="module")
def default_seed():
    return _load(_DEFAULT_SEED_PATH, "default_seed_mod")


# ── Состав worker_bot grants ─────────────────────────────────────────────────

class TestWorkerBotGrants:
    """Что должно быть."""

    EXPECTED: set[tuple[str, str]] = {
        ("server_account", "view_password"),
        ("server_account", "rotate_password"),
        ("ipmi_controller", "view_credentials"),
        ("ipmi_controller", "rotate_credentials"),
    }

    def test_grants_match_least_privilege_whitelist_exactly(self, worker_bot_seed):
        got = set(worker_bot_seed.WORKER_BOT_GRANTS)
        assert got == self.EXPECTED

    def test_grant_count_is_exactly_four(self, worker_bot_seed):
        assert len(worker_bot_seed.WORKER_BOT_GRANTS) == 4

    def test_no_duplicate_grants(self, worker_bot_seed):
        grants = worker_bot_seed.WORKER_BOT_GRANTS
        assert len(grants) == len(set(grants))

    def test_every_grant_passes_is_valid_action(self, worker_bot_seed):
        invalid = [
            (entity_type, action)
            for entity_type, action in worker_bot_seed.WORKER_BOT_GRANTS
            if not is_valid_action(entity_type, action)
        ]
        assert invalid == [], f"unwhitelisted worker_bot grants: {invalid}"


# ── Чего НЕ должно быть ──────────────────────────────────────────────────────

class TestWorkerBotForbiddenActions:
    """Список действий, которые worker_bot НЕ должен получать."""

    @pytest.mark.parametrize("forbidden", [
        # CRUD никаких entity типов
        ("server", "create"),
        ("server", "update"),
        ("server", "delete"),
        ("server_account", "create"),
        ("server_account", "update"),
        ("server_account", "delete"),
        ("server_account", "grant_sudo"),
        ("ipmi_controller", "create"),
        ("ipmi_controller", "update"),
        ("ipmi_controller", "delete"),
        # Управление состоянием серверов
        ("server", "power_on"),
        ("server", "power_off"),
        ("server", "power_reboot"),
        ("server", "power_status"),
        ("server", "busy_acquire"),
        ("server", "busy_release"),
        ("server", "os_sync"),
        ("server", "reinstall_start"),
        ("server", "pxe_boot"),
        ("server", "boot_order_set"),
        # Управление матрицей прав. (Каталог самих service-ролей живёт в
        # auth_service — у server_service такого entity-типа нет, поэтому
        # role_create/role_delete сюда не попадают.)
        ("permission", "permission_grant"),
        ("permission", "permission_revoke"),
        # Чтение password у server_account ≠ доступ к view самого аккаунта
        # (worker_bot работает по server_id+account_id, и ему достаточно `view_password`).
        ("server_account", "view"),
        # Чтение IPMI-метаданных тоже не нужно — есть `view_credentials`.
        ("ipmi_controller", "view"),
        # CRUD катaлогов
        ("os_version", "create"),
        ("disk", "create"),
    ])
    def test_forbidden_action_not_in_worker_bot_grants(self, worker_bot_seed, forbidden):
        got = set(worker_bot_seed.WORKER_BOT_GRANTS)
        assert forbidden not in got, (
            f"worker_bot must not receive {forbidden} — это полномочия "
            f"admin/operator или вообще опасные на сервисе действия"
        )


# ── Изоляция: worker_bot не пересекается с системными ролями ─────────────────

class TestWorkerBotIsolation:
    """Гарантия, что worker_bot живёт отдельно от admin/operator/reader/guest."""

    def test_worker_bot_role_not_present_in_default_seed(self, default_seed):
        """`831ba55543e9_…` миграция сидит только admin/reader/operator/guest —
        worker_bot должен идти отдельной миграцией, чтобы его легко было
        откатить без затрагивания baseline grants."""
        grants = default_seed._grants()
        roles = {role for (_e, role, _a) in grants}
        assert "worker_bot" not in roles, (
            "worker_bot не должен сидиться в default seed — он живёт "
            "в отдельной миграции 43cf9cfef9e1_…"
        )

    def test_default_seed_total_unchanged(self, default_seed):
        """Defence-in-depth: миграция worker_bot не задевает baseline.

        После follow-on миграций (`c8e4f6a9b1d2` drop installed_package,
        `a1b2c3d4e5f6` rename service_role → permission, `b6f3a91d27e8` drop
        cpu_model) baseline сидит 82 строки (раньше было 90).
        """
        assert len(default_seed._grants()) == 82

    def test_worker_bot_grants_strictly_smaller_than_admin(self, worker_bot_seed, default_seed):
        """worker_bot — подмножество admin'а по составу (entity, action),
        и при этом строго меньше: admin имеет 50 пар, worker_bot — 4."""
        admin_pairs = {
            (entity_type, action)
            for (entity_type, role, action) in default_seed._grants()
            if role == "admin"
        }
        worker_bot_pairs = set(worker_bot_seed.WORKER_BOT_GRANTS)
        assert worker_bot_pairs.issubset(admin_pairs), (
            "worker_bot grants должны быть подмножеством admin'а — иначе "
            "появилось новое action, которое admin не имеет"
        )
        assert len(worker_bot_pairs) < len(admin_pairs), (
            "least-privilege: worker_bot.grants ⊂ admin.grants должно быть строгим"
        )


# ── Миграционная обратимость ─────────────────────────────────────────────────

class TestWorkerBotMigrationShape:
    def test_revision_chains_after_default_seed(self, worker_bot_seed, default_seed):
        assert worker_bot_seed.down_revision == default_seed.revision

    def test_downgrade_targets_only_worker_bot(self, worker_bot_seed):
        """Downgrade-SQL должен удалять только worker_bot-строки —
        регрессия удаляющая admin/reader/operator поломала бы прод."""
        import inspect
        src = inspect.getsource(worker_bot_seed.downgrade)
        assert "worker_bot" in src
        for forbidden_role in ("admin", "reader", "operator", "guest"):
            assert forbidden_role not in src, (
                f"downgrade() trog'аеt роль '{forbidden_role}' — это ошибка"
            )

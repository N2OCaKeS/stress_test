"""Тесты Alembic-миграций — проверяем что upgrade/downgrade рабочи и
финальная схема содержит ожидаемые таблицы, колонки, constraints, индексы.

Запускаются на собственной чистой БД (`auth_db_migr_test`), чтобы не ломать
основную тестовую с зашитой схемой `head` (она используется живыми тестами).
"""

from __future__ import annotations

import os
import subprocess

import pytest
from sqlalchemy import create_engine, text


# Отдельная БД для миграционных тестов
_BASE_URL = os.environ["DATABASE_URL"]
_MIGR_DB = "auth_db_migr_test"
_MIGR_URL = _BASE_URL.rsplit("/", 1)[0] + f"/{_MIGR_DB}"


@pytest.fixture(scope="module", autouse=True)
def _ensure_migr_db():
    """Создаём пустую БД для миграционных тестов."""
    admin_url = _BASE_URL.rsplit("/", 1)[0] + "/postgres"
    eng = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{_MIGR_DB}"'))
        conn.execute(text(f'CREATE DATABASE "{_MIGR_DB}"'))
    eng.dispose()
    yield
    eng = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{_MIGR_DB}"'))
    eng.dispose()


def _alembic(*args: str) -> subprocess.CompletedProcess:
    service_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    env = {**os.environ, "DATABASE_URL": _MIGR_URL, "PYTHONPATH": "."}
    return subprocess.run(
        ["python", "-m", "alembic", *args],
        cwd=service_dir, env=env, check=False,
        capture_output=True, text=True,
    )


@pytest.fixture
def fresh_db():
    """Чистая схема перед каждым тестом (DROP/CREATE public)."""
    eng = create_engine(_MIGR_URL, isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    eng.dispose()
    yield


def _inspect(query: str, params: dict | None = None):
    eng = create_engine(_MIGR_URL)
    try:
        with eng.connect() as conn:
            rows = conn.execute(text(query), params or {}).all()
            return [tuple(r) for r in rows]
    finally:
        eng.dispose()


# ── Базовый upgrade ──────────────────────────────────────────────────────────

class TestUpgrade:
    def test_upgrade_head_succeeds(self, fresh_db):
        res = _alembic("upgrade", "head")
        assert res.returncode == 0, res.stderr

    def test_upgrade_head_creates_core_tables(self, fresh_db):
        _alembic("upgrade", "head")
        rows = _inspect(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' ORDER BY table_name"
        )
        tables = {r[0] for r in rows}
        expected = {
            "users", "departments", "sessions", "personal_access_tokens",
            "bot_accounts", "bot_tokens", "bans",
            "platform_services", "department_service_access",
            "service_role_definitions", "user_service_roles",
            "user_groups", "user_group_memberships",
            "group_service_access", "group_service_roles",
            "oauth_clients", "oauth_authorization_codes",
            "department_docker_registry",
            "bot_service_roles",  # последняя миграция
        }
        missing = expected - tables
        assert not missing, f"missing tables: {missing}"

    def test_alembic_version_at_head(self, fresh_db):
        _alembic("upgrade", "head")
        rows = _inspect("SELECT version_num FROM alembic_version")
        assert len(rows) == 1
        # head ревизия — текущий head из alembic.script, не хардкод.
        # Иначе тест ломается на каждый новый revision.
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        service_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        cfg = Config(os.path.join(service_dir, "alembic.ini"))
        cfg.set_main_option("script_location", os.path.join(service_dir, "src/db/migrations"))
        expected_head = ScriptDirectory.from_config(cfg).get_current_head()
        assert rows[0][0] == expected_head

    def test_upgrade_is_idempotent(self, fresh_db):
        """Повторный upgrade head на уже-актуальной БД — no-op, returncode 0."""
        first = _alembic("upgrade", "head")
        assert first.returncode == 0
        second = _alembic("upgrade", "head")
        assert second.returncode == 0


# ── Per-department service_role_definitions ──────────────────────────────────

class TestPerDeptRoleDefs:
    def test_table_has_department_id_column(self, fresh_db):
        _alembic("upgrade", "head")
        rows = _inspect(
            "SELECT column_name, is_nullable FROM information_schema.columns "
            "WHERE table_name='service_role_definitions' "
            "  AND column_name IN ('department_id', 'is_system', 'service_name', 'role_name')"
            " ORDER BY column_name"
        )
        cols = dict(rows)
        assert cols == {
            "department_id": "NO",
            "is_system": "NO",
            "role_name": "NO",
            "service_name": "NO",
        }

    def test_unique_constraint_includes_dept(self, fresh_db):
        _alembic("upgrade", "head")
        rows = _inspect(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid='service_role_definitions'::regclass AND contype='u'"
        )
        names = {r[0] for r in rows}
        assert "uq_dept_service_role_name" in names
        # старого uq_service_role_name быть не должно
        assert "uq_service_role_name" not in names

    def test_dept_fk_present(self, fresh_db):
        _alembic("upgrade", "head")
        rows = _inspect(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid='service_role_definitions'::regclass AND contype='f'"
        )
        names = {r[0] for r in rows}
        assert "fk_service_role_definitions_department_id" in names


# ── user_groups → department-scoped ──────────────────────────────────────────

class TestUserGroupsDeptScoped:
    def test_user_groups_has_department_id(self, fresh_db):
        _alembic("upgrade", "head")
        rows = _inspect(
            "SELECT column_name, is_nullable FROM information_schema.columns "
            "WHERE table_name='user_groups' AND column_name='department_id'"
        )
        assert rows == [("department_id", "NO")]

    def test_unique_name_per_department(self, fresh_db):
        _alembic("upgrade", "head")
        rows = _inspect(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid='user_groups'::regclass AND contype='u'"
        )
        names = {r[0] for r in rows}
        assert "uq_dept_user_group_name" in names
        assert "uq_user_group_name" not in names


# ── bot_service_roles ────────────────────────────────────────────────────────

class TestBotServiceRolesTable:
    def test_table_exists_with_expected_columns(self, fresh_db):
        _alembic("upgrade", "head")
        rows = _inspect(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='bot_service_roles' ORDER BY column_name"
        )
        cols = {r[0] for r in rows}
        expected = {
            "id", "bot_id", "service_name", "role",
            "is_active", "assigned_at", "assigned_by",
        }
        assert expected <= cols

    def test_unique_bot_service_role_constraint(self, fresh_db):
        _alembic("upgrade", "head")
        rows = _inspect(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid='bot_service_roles'::regclass AND contype='u'"
        )
        assert ("uq_bot_service_role",) in rows

    def test_bot_id_index_present(self, fresh_db):
        _alembic("upgrade", "head")
        rows = _inspect(
            "SELECT indexname FROM pg_indexes "
            "WHERE tablename='bot_service_roles'"
        )
        names = {r[0] for r in rows}
        assert "ix_bot_service_roles_bot_id" in names


# ── Downgrade ────────────────────────────────────────────────────────────────

def _resolve_revision_before(target_revision: str) -> str:
    """Найти ревизию-предка `target_revision` через alembic.script.

    Возвращает `down_revision` указанной ревизии — нужен чтобы downgrade'ать
    «до момента ПЕРЕД target», т.е. снести ровно её, не зависая от того,
    сколько миграций поверх неё накатилось с момента написания теста.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    service_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    cfg = Config(os.path.join(service_dir, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(service_dir, "src/db/migrations"))
    sd = ScriptDirectory.from_config(cfg)
    script = sd.get_revision(target_revision)
    assert script is not None, f"revision {target_revision} not found"
    down = script.down_revision
    assert isinstance(down, str), f"expected single down_revision for {target_revision}, got {down!r}"
    return down


class TestDowngrade:
    # head ревизия, на которой `bot_service_roles` появилась. Резолв в
    # `down_revision` через `_resolve_revision_before` даёт точку «прямо
    # перед этой миграцией» — индекс шагов больше не считаем руками.
    BOT_SERVICE_ROLES_REV = "a3b4c5d6e7f8"
    DEPT_SCOPE_REV = "f2a3b4c5d6e7"

    def test_downgrade_one_step_drops_bot_service_roles(self, fresh_db):
        _alembic("upgrade", "head")
        target = _resolve_revision_before(self.BOT_SERVICE_ROLES_REV)
        res = _alembic("downgrade", target)
        assert res.returncode == 0, res.stderr
        rows = _inspect(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name='bot_service_roles'"
        )
        assert rows == []

    def test_downgrade_dept_scope_restores_old_uniq(self, fresh_db):
        """Откат f2a3b4c5d6e7 убирает department_id и возвращает старый uniq."""
        _alembic("upgrade", "head")
        target = _resolve_revision_before(self.DEPT_SCOPE_REV)
        res = _alembic("downgrade", target)
        assert res.returncode == 0, res.stderr
        rows = _inspect(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid='service_role_definitions'::regclass AND contype='u'"
        )
        names = {r[0] for r in rows}
        # вернулся старый uniq
        assert "uq_service_role_name" in names
        assert "uq_dept_service_role_name" not in names

    def test_full_downgrade_to_base(self, fresh_db):
        _alembic("upgrade", "head")
        res = _alembic("downgrade", "base")
        assert res.returncode == 0, res.stderr
        rows = _inspect(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name NOT IN ('alembic_version')"
        )
        # все таблицы дропнуты (кроме служебной alembic_version)
        assert rows == []

    def test_full_cycle_upgrade_downgrade_upgrade(self, fresh_db):
        """upgrade head → downgrade base → upgrade head — без ошибок."""
        assert _alembic("upgrade", "head").returncode == 0
        assert _alembic("downgrade", "base").returncode == 0
        assert _alembic("upgrade", "head").returncode == 0
        # снова на head — резолвим dynamically, не хардкодим ревизию.
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        service_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        cfg = Config(os.path.join(service_dir, "alembic.ini"))
        cfg.set_main_option("script_location", os.path.join(service_dir, "src/db/migrations"))
        expected_head = ScriptDirectory.from_config(cfg).get_current_head()
        rows = _inspect("SELECT version_num FROM alembic_version")
        assert rows[0][0] == expected_head


# ── h5i6j7k8l9m0 display-names downgrade guard ───────────────────────────────

class TestDisplayNamesDowngradeGuard:
    """downgrade INSERT-only сервисов (auth_service/server_worker) удаляет их
    из platform_services. Шесть таблиц висят на service_name через CASCADE —
    слепой DELETE утащил бы выданные роли/доступы. Guard валит downgrade, если
    на удаляемый сервис кто-то завязался."""

    DISPLAY_NAMES_REV = "h5i6j7k8l9m0"

    def _exec(self, stmt: str, params: dict | None = None) -> None:
        eng = create_engine(_MIGR_URL, isolation_level="AUTOCOMMIT")
        try:
            with eng.connect() as conn:
                conn.execute(text(stmt), params or {})
        finally:
            eng.dispose()

    def test_downgrade_blocked_when_dependent_role_exists(self, fresh_db):
        _alembic("upgrade", self.DISPLAY_NAMES_REV)
        # auth_service зарегистрирован INSERT'ом этой миграции. Заводим
        # минимальный отдел и DepartmentServiceAccess на него — зависимость,
        # которую CASCADE снёс бы молча.
        # На ревизии h5i6j7k8l9m0 у departments ещё есть NOT NULL display_name
        # (дропается позже в m0n1o2p3q4r5) — заполняем, иначе INSERT падает на
        # NotNullViolation и до guard'а в downgrade тест не доходит.
        self._exec(
            "INSERT INTO departments (id, name, display_name, is_active, created_at, updated_at) "
            "VALUES ('dep_guard', 'guard', 'Guard', TRUE, now(), now())"
        )
        self._exec(
            "INSERT INTO department_service_access "
            "(id, department_id, service_name, is_active, granted_at) "
            "VALUES ('dsa_guard', 'dep_guard', 'auth_service', TRUE, now())"
        )
        target = _resolve_revision_before(self.DISPLAY_NAMES_REV)
        res = _alembic("downgrade", target)
        assert res.returncode != 0, (
            "downgrade должен упасть при зависимых строках, а не CASCADE-снести их"
        )
        combined = (res.stderr + res.stdout).lower()
        assert "cannot downgrade" in combined or "dependent" in combined, combined
        # platform_services.auth_service всё ещё на месте — ничего не удалили.
        rows = _inspect(
            "SELECT service_name FROM platform_services WHERE service_name='auth_service'"
        )
        assert rows == [("auth_service",)]

    def test_downgrade_succeeds_when_no_dependents(self, fresh_db):
        _alembic("upgrade", self.DISPLAY_NAMES_REV)
        target = _resolve_revision_before(self.DISPLAY_NAMES_REV)
        res = _alembic("downgrade", target)
        assert res.returncode == 0, res.stderr
        # INSERT-only сервисы удалены, UPDATE-сервисы откатились к доменным именам.
        rows = _inspect(
            "SELECT service_name FROM platform_services "
            "WHERE service_name IN ('auth_service', 'server_worker')"
        )
        assert rows == []

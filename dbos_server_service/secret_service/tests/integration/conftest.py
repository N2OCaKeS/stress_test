"""Fixtures для integration-тестов secret_service.

Поднимаем реальный FastAPI app поверх живой Postgres из
`tests/docker-compose.test.yml`. Outbound HTTP (auth_service introspect и
loging_service audit) полностью замоканы — для них в этой плоскости тестов
сеть не нужна, нужна только проверка контракта.

* `client` — ASGI client поверх настоящего app (lifespan включён, sweep loop
  отключён через `SWEEP_ENABLED=false`).
* `db` — async-сессия, дублирующая ту, что endpoint'ы получают через
  `Depends(get_db)`. Используется для прямого посева/чтения.
* `clean_db` — TRUNCATE всех таблиц между тестами (без savepoint'ов: app
  делает `commit()` внутри сервисов, savepoint'ная схема ломала бы их).
* `mock_auth_service` — патч `_introspect` в `dependencies/auth` так, что
  каждый bearer-токен резолвится в заранее зарегистрированный Identity body.
* `mock_logging_service` — патч `audit_service.emit` через capture-list:
  каждый эмит складываем в `captured`, ничего не отправляем по сети.
* `identity_factory` — фабрика валидных Identity dict'ов разных ролей.
* `cred_factory` — создание Credential через service-layer, в обход API.
"""

from __future__ import annotations

import base64
import os
import subprocess
import uuid
from typing import Callable

# DB / app-окружение настраиваем ДО любого import'а из `src`. Без этого
# Settings подхватит дефолты из top-level tests/conftest.py с localhost
# DSN — а в Docker нам нужен test-postgres.
os.environ.setdefault(
    "DATABASE_URL",
    os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql+psycopg://app_user:app_password@test-postgres:5432/secret_db_test",
    ),
)
os.environ.setdefault("AUTH_SERVICE_URL", "http://auth-not-used-by-integration-tests")
os.environ.setdefault("LOGGING_SERVICE_URL", "")
os.environ.setdefault("LOGGING_SERVICE_API_KEY", "")
os.environ.setdefault(
    "SECRET_ENCRYPTION_KEY",
    "test-secret-encryption-key-do-not-use-anywhere-else",
)
os.environ.setdefault("SECRET_ENCRYPTION_KEY_VERSION", "2")
os.environ.setdefault("HKDF_SALT_HEX", "deadbeefcafebabe0011223344556677")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("SWEEP_ENABLED", "false")
os.environ.setdefault("SLOWAPI_RATE_LIMIT", "10000/minute")
os.environ.setdefault("SERVICE_API_KEY", "internal-test-key")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine


TEST_DATABASE_URL = os.environ["DATABASE_URL"]

# Отдельный engine — чтобы не делить пул с приложением. Для truncate
# используем sync, для запросов из тестов — async.
_sync_engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
_async_engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)


# ── Schema lifecycle ─────────────────────────────────────────────────────────


def _reset_schema() -> None:
    with _sync_engine.connect() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.execute(text("DROP TYPE IF EXISTS credential_scope"))
        conn.execute(text("DROP TYPE IF EXISTS credential_status"))
        conn.commit()


def _run_migrations() -> None:
    service_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    env = {**os.environ, "DATABASE_URL": TEST_DATABASE_URL, "PYTHONPATH": "."}
    subprocess.run(
        ["python", "-m", "alembic", "upgrade", "head"],
        cwd=service_dir,
        env=env,
        check=True,
        capture_output=True,
    )


@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    _reset_schema()
    _run_migrations()
    yield


@pytest.fixture(autouse=True)
def clean_db():
    """TRUNCATE всех таблиц между тестами. App делает commit() внутри
    сервисов, поэтому savepoint-стратегия (как в unit-тестах) тут ломается:
    inner-commit нельзя «откатить» через rollback внешней транзакции."""
    yield
    with _sync_engine.connect() as conn:
        conn.execute(text(
            "TRUNCATE TABLE role_acls, dept_grants, credentials RESTART IDENTITY CASCADE"
        ))
        conn.commit()


# ── Async session — для прямого посева/чтения из тестов ────────────────────


@pytest_asyncio.fixture
async def db() -> AsyncSession:
    """Прямая async-сессия к test-postgres. НЕ совпадает с той, что
    endpoint'ы получают через get_db — те открывают свои сессии в lifespan'е
    app'а. Эта нужна тестам для arrange/assert на уровне строк."""
    async with AsyncSession(_async_engine, expire_on_commit=False) as session:
        yield session


# ── Mock auth (introspect) ──────────────────────────────────────────────────

# Регистрация тестовых токенов: token → introspect-body.
_FAKE_INTROSPECT: dict[str, dict] = {}


def _identity_body(
    *,
    user_id: str,
    username: str = "tester",
    department_id: str | None = None,
    platform_role: str | None = None,
    service_roles: dict[str, list[str]] | None = None,
    allowed_services: list[str] | None = None,
    active: bool = True,
    is_banned: bool = False,
    subject_type: str = "user",
) -> dict:
    if allowed_services is None:
        allowed_services = ["secret_service"]
    return {
        "active": active,
        "sub": user_id,
        "username": username,
        "department_id": department_id,
        "platform_role": platform_role,
        "allowed_services": allowed_services,
        "service_roles": service_roles or {},
        "is_banned": is_banned,
        "subject_type": subject_type,
    }


@pytest.fixture(autouse=True)
def mock_auth_service(monkeypatch):
    """Подменяет `_introspect` так, что каждый bearer резолвится через
    локальную регистрацию. Кэш introspect'а внутри `dependencies/auth`
    тоже сбрасываем — TTL=5s; в долгих прогонах остаточный кэш ловит
    одного и того же актора."""
    from src.dependencies import auth as auth_dep

    async def fake_introspect(token: str) -> dict:
        body = _FAKE_INTROSPECT.get(token)
        if body is None:
            return {"active": False}
        return body

    monkeypatch.setattr(auth_dep, "_introspect", fake_introspect)
    auth_dep._cache_clear_for_tests()
    _FAKE_INTROSPECT.clear()
    yield _FAKE_INTROSPECT
    _FAKE_INTROSPECT.clear()
    auth_dep._cache_clear_for_tests()


# ── Mock logging (audit) ────────────────────────────────────────────────────


class _AuditCapture(list):
    """Список перехваченных вызовов audit_service.emit. list-наследник,
    чтобы тесты привычно делали `len(captured)`/индексацию."""

    def by_action(self, action: str) -> list[dict]:
        return [e for e in self if e["action"] == action]

    def find_one(self, action: str) -> dict | None:
        events = self.by_action(action)
        return events[-1] if events else None


@pytest.fixture(autouse=True)
def mock_logging_service(monkeypatch):
    """Перехватывает все audit-эмиты. Дёргается ИЗ ВСЕХ мест, где
    `audit_service.emit` импортирован by-reference (services/*); монкипатчим
    напрямую функцию в модуле — это покрывает все importers, которые
    делают `from src.services import audit_service` + `audit_service.emit(...)`.

    Для importer'ов с `from ... import emit as ...` пришлось бы патчить
    конкретный модуль; в данный момент таких нет (см. `grep -rn 'from
    src.services.audit_service import emit' src/`)."""
    from src.services import audit_service

    captured = _AuditCapture()

    def fake_emit(action, actor_id=None, **kwargs):
        # Подтянем actor_id/контекст так же, как реальный emit, чтобы
        # тесты могли инспектировать.
        from src.services import audit_context

        ctx = audit_context.get_context()
        resolved_actor = actor_id if actor_id is not None else ctx.actor_id
        captured.append({
            "action": action,
            "actor_id": resolved_actor,
            "actor_type": kwargs.get("actor_type") or ctx.subject_type or "user",
            "target_id": kwargs.get("target_id"),
            "target_type": kwargs.get("target_type"),
            "status": kwargs.get("status", "success"),
            "allowed": kwargs.get("allowed", True),
            "severity": kwargs.get("severity"),
            "details": kwargs.get("details") or {},
            "request_id": kwargs.get("request_id") or ctx.request_id,
            "department_id": kwargs.get("department_id") or ctx.department_id,
            "username": kwargs.get("username") or ctx.username,
        })

    monkeypatch.setattr(audit_service, "emit", fake_emit)
    yield captured


# ── Identity / token factories ──────────────────────────────────────────────


@pytest.fixture
def identity_factory() -> Callable[..., str]:
    """Возвращает фабрику: regiserируем токен в `_FAKE_INTROSPECT` и
    возвращаем bearer-строку (формат, который проходит shape-check в
    `dependencies/auth`).
    """

    def _factory(
        *,
        user_id: str | None = None,
        username: str = "tester",
        department_id: str | None = None,
        platform_role: str | None = None,
        service_roles: dict[str, list[str]] | None = None,
        allowed_services: list[str] | None = None,
        active: bool = True,
        is_banned: bool = False,
        subject_type: str = "user",
    ) -> str:
        # `dbos_pat_` префикс проходит `_is_token_shape_valid`.
        token = f"dbos_pat_{uuid.uuid4().hex}"
        _FAKE_INTROSPECT[token] = _identity_body(
            user_id=user_id or f"usr_{uuid.uuid4().hex[:12]}",
            username=username,
            department_id=department_id,
            platform_role=platform_role,
            service_roles=service_roles or {"secret_service": ["operator"]},
            allowed_services=allowed_services,
            active=active,
            is_banned=is_banned,
            subject_type=subject_type,
        )
        return token

    return _factory


def auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── ASGI client ─────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def client():
    """ASGI client — реальный app с lifespan'ом и middleware.

    Lifespan делает: register_events (best-effort, моки логгинга глушат),
    sweep_loop (выключен env'ом). `engine.dispose()` на shutdown.
    """
    # Сбрасываем lru_cache на Settings — env переменные могли поменяться
    # между парами integration ↔ unit, а Settings закешируется первым.
    from src.core import config as config_mod
    config_mod.get_settings.cache_clear()

    # Импортируем app ПОСЛЕ env-инициализации и cache_clear.
    from src.main import app

    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):  # type: ignore[arg-type]
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


# ── Credential service-layer factory ────────────────────────────────────────


@pytest_asyncio.fixture
async def cred_factory(db):
    """Создать Credential напрямую через service-layer (минуя API).

    Удобно для arrange-фазы: тест проверяет endpoint X, ему нужна готовая
    cred — но не нужно гонять весь POST. Identity передаём в фабрику
    конструируемо, чтобы соответствовать ownership-инвариантам.
    """
    from src.dependencies.auth import Identity
    from src.schemas.credentials import CredentialCreate
    from src.services import credential_service

    async def _factory(
        *,
        owner_user_id: str | None = None,
        owner_dept_id: str | None = None,
        scope: str = "personal",
        service: str = "jira",
        name: str | None = None,
        login: str | None = "alice@example.com",
        secret: str = "supersecret-value",
        platform_role: str | None = None,
        service_roles: dict[str, list[str]] | None = None,
    ):
        # Identity тут чисто для прохождения use-case проверок.
        if scope == "personal":
            actor_user_id = owner_user_id or f"usr_{uuid.uuid4().hex[:12]}"
            actor_dept = "dep_owner"
            actor_roles = service_roles or {"secret_service": ["operator"]}
            actor_platform = platform_role
            owner_user_id = actor_user_id
        else:
            actor_user_id = f"usr_{uuid.uuid4().hex[:12]}"
            actor_dept = owner_dept_id or f"dep_{uuid.uuid4().hex[:8]}"
            actor_roles = service_roles or {"secret_service": ["operator"]}
            actor_platform = platform_role or "department_admin"
            owner_dept_id = actor_dept

        identity = Identity(
            user_id=actor_user_id,
            username="factory",
            actor_type="user",
            department_id=actor_dept,
            allowed_services=["secret_service"],
            service_roles=actor_roles,
            is_banned=False,
            platform_role=actor_platform,
        )

        payload = CredentialCreate(
            name=name or f"cred_{uuid.uuid4().hex[:6]}",
            service=service,
            scope=scope,  # type: ignore[arg-type]
            login=login,
            secret_b64=base64.b64encode(secret.encode()).decode(),
            owner_dept_id=owner_dept_id if scope != "personal" else None,
        )
        return await credential_service.create(db, identity, payload)

    return _factory


# ── Reveal throttle reset (in-memory state per-process) ─────────────────────


@pytest.fixture(autouse=True)
def _reset_throttle():
    """In-memory throttle живёт в module-globals; сбрасываем между тестами."""
    from src.services import reveal_throttle

    reveal_throttle._reset_for_tests()
    yield
    reveal_throttle._reset_for_tests()

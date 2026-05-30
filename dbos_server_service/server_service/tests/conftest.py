"""Pytest fixtures for server_service tests.

* Real Postgres with per-test SAVEPOINT rollback (миграции один раз на сессию).
* Mock `_introspect` через monkeypatch так, чтобы Bearer-токены тестов
  отдавали predetermined identity без сети.
* Helpers для создания серверов / identity / fake-токенов разных ролей.
"""

from __future__ import annotations

import os
import subprocess
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

os.environ.setdefault(
    "DATABASE_URL",
    os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://app_user:app_password@postgres:5432/server_db_test"),
)
os.environ.setdefault("SERVER_ENCRYPTION_KEY", "test-server-encryption-key-do-not-use-anywhere-else")
os.environ.setdefault("SERVER_ENCRYPTION_KEY_VERSION", "2")
# Тестовый stable HKDF salt — 32 hex (16 байт). В production/staging пустой
# `HKDF_SALT_HEX` отбивается Settings, в dev/test/local допустим (fallback из
# secrets_service подхватится сам). Здесь выставляем явно, чтобы прогон тестов
# был детерминированным и не зависел от наличия .env в репо.
os.environ.setdefault("HKDF_SALT_HEX", "deadbeefcafebabe0011223344556677")
os.environ.setdefault("AUTH_SERVICE_URL", "http://auth-not-used")
os.environ.setdefault("SERVER_SERVICE_PAT", "dbos_pat_test_not_used")

TEST_DATABASE_URL = os.environ["DATABASE_URL"]

_sync_engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
_async_engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)


# ── DB lifecycle ─────────────────────────────────────────────────────────────

def _ensure_test_db_exists() -> None:
    from urllib.parse import urlparse
    parsed = urlparse(TEST_DATABASE_URL)
    db_name = parsed.path.lstrip("/")
    admin_url = TEST_DATABASE_URL.replace(f"/{db_name}", "/postgres")
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": db_name},
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    finally:
        engine.dispose()


def _reset_schema() -> None:
    engine = create_engine(TEST_DATABASE_URL, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
    finally:
        engine.dispose()


def _run_migrations() -> None:
    service_dir = os.path.dirname(os.path.dirname(__file__))
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
    """One-time: ensure DB exists, wipe public schema, run migrations."""
    _ensure_test_db_exists()
    _reset_schema()
    _run_migrations()
    yield
    _reset_schema()


@pytest_asyncio.fixture()
async def db():
    """AsyncSession over outer transaction + SAVEPOINT (rollback on teardown)."""
    conn = await _async_engine.connect()
    await conn.begin()
    await conn.begin_nested()
    session = AsyncSession(bind=conn, expire_on_commit=False)

    @event.listens_for(session.sync_session, "after_transaction_end")
    def restart_savepoint(sess, transaction):
        if transaction.nested and not transaction._parent.nested:
            sess.begin_nested()

    yield session
    await session.close()
    await conn.rollback()
    await conn.close()


# ── Identity / introspect mock ───────────────────────────────────────────────

# token-строка → introspect-body
_FAKE_INTROSPECT: dict[str, dict] = {}


def register_token(token: str, body: dict) -> None:
    _FAKE_INTROSPECT[token] = body


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
    subject_type: str | None = None,
) -> dict:
    # Дефолт allowed_services: обычным пользователям и department_admin
    # отдаём `["server_service"]`, чтобы они проходили `SERVICE_ACCESS_DENIED`
    # гейт в `dependencies/auth.py`. Platform-admin'ам (`account_admin`,
    # `loging_admin`, `loging_reader`) по модели §7 пустой список — они
    # вообще не имеют сервисных ролей в прикладных сервисах, а доступ к
    # business endpoint'ам режется в platform_admin_guard middleware.
    if allowed_services is None:
        if platform_role in {"account_admin", "loging_admin", "loging_reader"}:
            allowed_services = []
        else:
            allowed_services = ["server_service"]
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
def _patch_introspect(monkeypatch):
    """Перехватывает `_introspect` чтобы тесты работали без auth_service.

    Кэша introspect нет — каждый запрос идёт свежим `_introspect`.
    """
    async def fake_introspect(token: str) -> dict:
        body = _FAKE_INTROSPECT.get(token)
        if body is None:
            return {"active": False}
        return body

    monkeypatch.setattr("src.dependencies.auth._introspect", fake_introspect)
    _FAKE_INTROSPECT.clear()
    yield
    _FAKE_INTROSPECT.clear()


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Сбрасывает per-IP rate-limit между тестами.

    Все тесты идут с одного `127.0.0.1` через ASGITransport — без reset'а
    счётчик slowapi сохранил бы состояние между кейсами и при 168+ запросах
    мог бы случайно отбить тест c 429. Глобальный лимит (`global_rate_limit`,
    по умолчанию 500/minute) штатно проверяется в `test_rate_limit.py`.
    """
    from src.core.limiter import endpoint_limiter
    from src.main import app

    limiter = getattr(app.state, "limiter", None)
    if limiter is not None:
        limiter.reset()
    endpoint_limiter.reset()
    yield
    if limiter is not None:
        limiter.reset()
    endpoint_limiter.reset()


# ── Token factories ──────────────────────────────────────────────────────────

@pytest.fixture
def make_token():
    """Возвращает фабрику: create_token(role=..., department_id=...) → str."""

    def _factory(
        *,
        platform_role: str | None = None,
        department_id: str | None = None,
        service_roles: dict[str, list[str]] | None = None,
        allowed_services: list[str] | None = None,
        user_id: str | None = None,
        username: str = "tester",
        active: bool = True,
        is_banned: bool = False,
        subject_type: str | None = None,
    ) -> str:
        token = f"tok_{uuid.uuid4().hex}"
        register_token(token, _identity_body(
            user_id=user_id or f"usr_{uuid.uuid4().hex[:8]}",
            username=username,
            department_id=department_id,
            platform_role=platform_role,
            service_roles=service_roles,
            allowed_services=allowed_services,
            active=active,
            is_banned=is_banned,
            subject_type=subject_type,
        ))
        return token

    return _factory


# ── Departments / Servers helpers ────────────────────────────────────────────

@pytest.fixture
def dept_a() -> str:
    return "dep_a"


@pytest.fixture
def dept_b() -> str:
    return "dep_b"


@pytest_asyncio.fixture
async def admin_token(make_token, dept_a) -> str:
    """Department admin (platform_role) с сервисной ролью `admin` в `dep_a`.

    Раньше эта фикстура отдавала ``platform_role=account_admin`` для
    проверок «admin может всё». После разделения админ-плоскостей
    `account_admin` не имеет доступа к бизнес-данным `server_service`
    (блокируется в `platform_admin_guard` middleware). Поэтому
    существующие тесты переведены на ``department_admin`` — он имеет
    легитимный доступ к бизнес-данным **своего** департамента.
    Сервисная роль `admin` в `dep_a` даёт ему full-CRUD через матрицу.

    Для проверок самого guard middleware — отдельные фикстуры
    ``account_admin_token`` / ``loging_admin_token`` / ``loging_reader_token``.
    """
    return make_token(
        platform_role="department_admin",
        department_id=dept_a,
        service_roles={"server_service": ["admin"]},
    )


@pytest_asyncio.fixture
async def admin_token_b(make_token, dept_b) -> str:
    """Симметрично ``admin_token``, но для `dep_b`.

    Нужен для тестов, которые раньше использовали `account_admin` для
    создания/удаления ресурсов в `dep_b` — теперь это делает
    department_admin своего отдела.
    """
    return make_token(
        platform_role="department_admin",
        department_id=dept_b,
        service_roles={"server_service": ["admin"]},
    )


@pytest_asyncio.fixture
async def account_admin_token(make_token) -> str:
    """Platform-роль ``account_admin`` без департамента (§7 модели безопасности).

    По §7-8 имеет нулевой доступ к бизнес-данным `server_service` —
    `platform_admin_guard` middleware отбивает любой business endpoint
    403 ``PLATFORM_ADMIN_BUSINESS_DATA_DENIED``. Используется только в
    `tests/integration/test_platform_admin_block.py` для проверки самого
    guard'а.
    """
    return make_token(platform_role="account_admin")


@pytest_asyncio.fixture
async def loging_admin_token(make_token) -> str:
    """Platform-роль ``loging_admin``. Тоже блокируется guard'ом."""
    return make_token(platform_role="loging_admin")


@pytest_asyncio.fixture
async def loging_reader_token(make_token) -> str:
    """Platform-роль ``loging_reader``. Тоже блокируется guard'ом."""
    return make_token(platform_role="loging_reader")


@pytest_asyncio.fixture
async def reader_token_a(make_token, dept_a) -> str:
    return make_token(
        department_id=dept_a,
        service_roles={"server_service": ["reader"]},
    )


@pytest_asyncio.fixture
async def operator_token_a(make_token, dept_a) -> str:
    return make_token(
        department_id=dept_a,
        service_roles={"server_service": ["operator"]},
    )


@pytest_asyncio.fixture
async def admin_role_token_a(make_token, dept_a) -> str:
    return make_token(
        department_id=dept_a,
        service_roles={"server_service": ["admin"]},
    )


@pytest_asyncio.fixture
async def guest_token_a(make_token, dept_a) -> str:
    return make_token(
        department_id=dept_a,
        service_roles={"server_service": ["guest"]},
    )


@pytest_asyncio.fixture
async def no_role_token_a(make_token, dept_a) -> str:
    """Авторизация валидна, но без ролей в server_service."""
    return make_token(
        department_id=dept_a,
        service_roles={},
    )


@pytest_asyncio.fixture
async def reader_token_b(make_token, dept_b) -> str:
    return make_token(
        department_id=dept_b,
        service_roles={"server_service": ["reader"]},
    )


@pytest_asyncio.fixture
async def operator_token_b(make_token, dept_b) -> str:
    return make_token(
        department_id=dept_b,
        service_roles={"server_service": ["operator"]},
    )


# ── ASGI client ──────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def client(db):
    """ASGI client с переопределённой DB-сессией."""
    from src.dependencies.db import get_db
    from src.main import app

    async def _override_get_db():
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


# ── Server helpers ───────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def make_server(db):
    """Factory для создания сервера: await make_server(department_id='dep_a').

    `with_ipmi=True` дополнительно создаёт IPMI controller row для сервера —
    нужно для power-success тестов: `_dispatch_power` в `endpoints/ipmi.py`
    требует наличие записи в `ipmi_controllers`, иначе возвращает 404
    NO_IPMI_CONTROLLER. Тесты, проверяющие 401/403 ДО IPMI-проверки, либо
    409 SERVER_DECOMMISSIONED (проверка DECOMMISSIONED идёт раньше IPMI),
    либо непосредственно NO_IPMI_CONTROLLER поведение — не нуждаются в
    `with_ipmi=True` и оставляют дефолт.
    """
    from src.models import IpmiController, Server
    from src.services import secrets_service
    from src.utils.ids import _new_id
    from src.utils.ids import server_id as new_id

    async def _factory(
        *,
        department_id: str = "dep_a",
        hostname: str | None = None,
        ip_address: str | None = None,
        serial_number: str | None = None,
        with_ipmi: bool = False,
    ) -> Server:
        suffix = uuid.uuid4().hex[:6]
        srv = Server(
            id=new_id(),
            hostname=hostname or f"srv-{suffix}",
            ip_address=ip_address or f"10.{int(suffix[:2], 16) % 256}.0.{int(suffix[2:4], 16) % 256}",
            ssh_port=22,
            department_id=department_id,
            serial_number=serial_number,
        )
        db.add(srv)
        await db.flush()
        if with_ipmi:
            ctrl_id = _new_id("ipm_")
            ctrl = IpmiController(
                id=ctrl_id,
                server_id=srv.id,
                kind="idrac",
                endpoint_url="https://idrac.example.com",
                username="ipmi_user",
                password_encrypted=secrets_service.encrypt(
                    "ipmi-plaintext-secret",
                    aad=secrets_service.aad_for_ipmi_credential(ctrl_id),
                ),
            )
            db.add(ctrl)
            await db.flush()
        return srv

    return _factory


@pytest_asyncio.fixture
async def make_ipmi(db):
    """Создаёт IPMI controller для сервера с зашифрованным паролем."""
    from src.models import IpmiController
    from src.services import secrets_service
    from src.utils.ids import _new_id

    async def _factory(
        *, server_id: str, kind: str = "idrac",
        endpoint_url: str = "https://idrac.example.com",
        username: str = "ipmi_user",
        password: str = "ipmi-plaintext-secret",
    ) -> IpmiController:
        ctrl_id = _new_id("ipm_")
        ctrl = IpmiController(
            id=ctrl_id,
            server_id=server_id,
            kind=kind,
            endpoint_url=endpoint_url,
            username=username,
            password_encrypted=secrets_service.encrypt(
                password,
                aad=secrets_service.aad_for_ipmi_credential(ctrl_id),
            ),
        )
        db.add(ctrl)
        await db.flush()
        return ctrl

    return _factory


@pytest_asyncio.fixture
async def make_account(db):
    """Создаёт server_account (M2M) с (опционально) зашифрованным паролем.

    Аккаунт привязывается к одному или нескольким серверам. Принимает либо
    `server_id=` (один сервер), либо `server_ids=` (список). `department_id`
    аккаунта берётся из первого сервера.
    """
    from sqlalchemy import select

    from src.models import Server, ServerAccount, ServerAccountServer
    from src.services import secrets_service
    from src.utils.ids import _new_id

    async def _factory(
        *, server_id: str | None = None,
        server_ids: list[str] | None = None,
        login: str = "root",
        password: str | None = "account-plaintext-pwd",
        has_sudo: bool = False,
        shell: str | None = None,
        home_dir: str | None = None,
        unix_groups: list[str] | None = None,
    ) -> ServerAccount:
        ids = list(server_ids) if server_ids is not None else []
        if server_id is not None:
            ids.append(server_id)
        if not ids:
            raise ValueError("make_account requires server_id or server_ids")
        srv = (await db.execute(
            select(Server).where(Server.id == ids[0])
        )).scalar_one()
        acc_id = _new_id("acc_")
        acc = ServerAccount(
            id=acc_id,
            department_id=srv.department_id,
            login=login,
            password_encrypted=secrets_service.encrypt(
                password,
                aad=secrets_service.aad_for_server_account_password(acc_id),
            ) if password else None,
            has_sudo=has_sudo,
            shell=shell,
            home_dir=home_dir,
            unix_groups=list(unix_groups) if unix_groups is not None else [],
        )
        db.add(acc)
        await db.flush()
        for sid in ids:
            db.add(ServerAccountServer(
                id=_new_id("acs_"),
                account_id=acc_id,
                server_id=sid,
                login=login,
            ))
        await db.flush()
        await db.refresh(acc)
        return acc

    return _factory


@pytest.fixture
def soft_dept_mode(monkeypatch):
    """Force `internal_require_dept_header=False` (soft mode) для теста.

    Default в проде/staging — True (strict). В тестах часть классов проверяет
    permission/access-логику без header'а — им явно нужен soft mode. Clear'им
    lru_cache на get_settings до и после, чтобы override детерминированно
    подхватился и не утёк в соседний тест.
    """
    from src.core.config import get_settings

    monkeypatch.setenv("INTERNAL_REQUIRE_DEPT_HEADER", "false")
    get_settings.cache_clear()  # type: ignore[attr-defined]
    yield
    get_settings.cache_clear()  # type: ignore[attr-defined]


@pytest_asyncio.fixture
async def worker_pat_token(make_token, dept_a):
    """Симулирует PAT воркера — admin-роль с полным доступом к ipmi/accounts.

    Используется в существующих тестах, где исторически worker = admin. Новые
    тесты, которые проверяют least-privilege контракт `worker_bot`, должны
    использовать ``worker_bot_token_a`` ниже.
    """
    return make_token(
        department_id=dept_a,
        service_roles={"server_service": ["admin"]},
        subject_type="bot",
    )


@pytest_asyncio.fixture
async def worker_bot_token_a(make_token, dept_a):
    """PAT воркера с least-privilege ролью `worker_bot` в `dep_a`.

    Гранты этой роли сидятся миграцией
    ``43cf9cfef9e1_seed_worker_bot_entity_permissions.py``:

    * ``server_account`` — ``view_password`` / ``rotate_password``;
    * ``ipmi_controller`` — ``view_credentials`` / ``rotate_credentials``.

    Никаких power/CRUD/permission_grant. Тесты должны проверять, что 4
    разрешённых действия 200, а все остальные 403.
    """
    return make_token(
        department_id=dept_a,
        service_roles={"server_service": ["worker_bot"]},
        subject_type="bot",
    )

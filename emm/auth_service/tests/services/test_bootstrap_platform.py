"""Startup-bootstrap: реестр платформенных сервисов + worker-бот из ENV.

Покрывает `bootstrap_service.bootstrap_platform_services` и
`bootstrap_service.bootstrap_worker_bot`:

* на чистой БД регистрируются все канонические сервисы (`PLATFORM_SERVICES`);
* worker-бот создаётся, его токен проходит introspect (hash совпал с
  `WORKER_BOT_TOKEN`), роль — `worker_bot@server_service`;
* повторный вызов — no-op (идемпотентность);
* пустой `WORKER_BOT_TOKEN` — шаг worker-бота пропускается.
"""

from __future__ import annotations

import pytest

from src.core.constants import (
    PLATFORM_SERVICES,
    WORKER_BOT_NAME,
    WORKER_BOT_ROLE,
    WORKER_BOT_SERVICE,
)
from src.services import bootstrap_service


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    """`get_settings` — lru_cache на процесс; между тестами сбрасываем, чтобы
    подменённый `WORKER_BOT_TOKEN` не протекал в соседние тесты."""
    from src.core import config as config_mod

    config_mod.get_settings.cache_clear()
    yield
    config_mod.get_settings.cache_clear()


def _silence_audit(monkeypatch) -> list[tuple[str, str | None]]:
    """Заглушить `audit_service.emit` (общий модуль) и собрать `(action, target_id)`."""
    emitted: list[tuple[str, str | None]] = []

    def _spy(action, *a, **k):
        emitted.append((action, k.get("target_id")))

    monkeypatch.setattr(bootstrap_service.audit_service, "emit", _spy)
    return emitted


# ── платформенные сервисы ─────────────────────────────────────────────────────


async def test_bootstrap_registers_all_platform_services(db, monkeypatch):
    """На чистой БД регистрируются все 5 сервисов из `PLATFORM_SERVICES`."""
    from src.repositories.services import ServiceRepository

    _silence_audit(monkeypatch)
    await bootstrap_service.bootstrap_platform_services(db)

    repo = ServiceRepository(db)
    for service_name, _ in PLATFORM_SERVICES:
        assert await repo.exists(service_name), f"{service_name} не зарегистрирован"
    # secret_service обязателен — без него grant отдела падает 404.
    assert await repo.exists("secret_service")


async def test_bootstrap_platform_services_idempotent(db, monkeypatch):
    """Повторный вызов не плодит дублей и не эмитит `service.create`.

    Миграция сеет 4 сервиса, но не secret_service — именно его bootstrap и
    досеивает (закрывает 404 на grant отдела к secret_service).
    """
    from src.repositories.services import ServiceRepository

    emitted = _silence_audit(monkeypatch)
    await bootstrap_service.bootstrap_platform_services(db)
    created = [target for (action, target) in emitted if action == "service.create"]
    assert "secret_service" in created, created

    emitted.clear()
    await bootstrap_service.bootstrap_platform_services(db)
    assert emitted == [], "второй прогон должен быть no-op"

    active = [s.service_name for s in await ServiceRepository(db).list_active()]
    for service_name, _ in PLATFORM_SERVICES:
        assert active.count(service_name) == 1, service_name


async def test_bootstrap_preserves_preexisting_service(db, monkeypatch):
    """Уже существующая запись (server_service из миграции) не перетирается."""
    from src.repositories.services import ServiceRepository

    repo = ServiceRepository(db)
    svc = await repo.get("server_service")
    assert svc is not None, "server_service должен быть посеян миграцией"
    svc.description = "manual description"
    await db.commit()

    _silence_audit(monkeypatch)
    await bootstrap_service.bootstrap_platform_services(db)

    refreshed = await repo.get("server_service")
    assert refreshed.description == "manual description"


# ── worker-бот ────────────────────────────────────────────────────────────────


async def test_bootstrap_worker_bot_creates_bot_and_valid_token(db, monkeypatch):
    """Worker-бот заведён; сырой WORKER_BOT_TOKEN проходит introspect с ролью
    worker_bot@server_service."""
    from src.core import config as config_mod
    from src.repositories.bot_roles import BotRoleRepository
    from src.repositories.bots import BotRepository
    from src.services import authorization_service

    _silence_audit(monkeypatch)
    token = "dbos_bot_" + "a" * 43
    monkeypatch.setenv("WORKER_BOT_TOKEN", token)
    config_mod.get_settings.cache_clear()

    await bootstrap_service.bootstrap_platform_services(db)
    await bootstrap_service.bootstrap_worker_bot(db)

    bot = await BotRepository(db).first_by_name(WORKER_BOT_NAME)
    assert bot is not None
    assert bot.allowed_services == [WORKER_BOT_SERVICE]

    roles = await BotRoleRepository(db).get_roles_by_service(bot.id, WORKER_BOT_SERVICE)
    assert WORKER_BOT_ROLE in roles

    # introspect по сырому токену: hash совпал → активный бот с ролью.
    res = await authorization_service.introspect(db, token)
    assert res.active is True
    assert res.sub == bot.id
    assert res.service_roles.get(WORKER_BOT_SERVICE) == [WORKER_BOT_ROLE]


async def test_bootstrap_worker_bot_skipped_when_token_empty(db, monkeypatch):
    """Пустой WORKER_BOT_TOKEN — worker-бот не создаётся."""
    from src.core import config as config_mod
    from src.repositories.bots import BotRepository

    _silence_audit(monkeypatch)
    monkeypatch.delenv("WORKER_BOT_TOKEN", raising=False)
    config_mod.get_settings.cache_clear()

    await bootstrap_service.bootstrap_platform_services(db)
    await bootstrap_service.bootstrap_worker_bot(db)

    assert await BotRepository(db).first_by_name(WORKER_BOT_NAME) is None


async def test_bootstrap_worker_bot_sets_is_service_bot(db, monkeypatch):
    """Свежий worker-бот заводится сразу с `is_service_bot=True`."""
    from src.core import config as config_mod
    from src.repositories.bots import BotRepository

    _silence_audit(monkeypatch)
    token = "dbos_bot_" + "e" * 43
    monkeypatch.setenv("WORKER_BOT_TOKEN", token)
    config_mod.get_settings.cache_clear()

    await bootstrap_service.bootstrap_platform_services(db)
    await bootstrap_service.bootstrap_worker_bot(db)

    bot = await BotRepository(db).first_by_name(WORKER_BOT_NAME)
    assert bot.is_service_bot is True


async def test_bootstrap_worker_bot_backfills_flag_on_legacy_bot_slow_path(db, monkeypatch):
    """Бот и роль заведены прежней версией бутстрапа (флаг ещё не существовал,
    дефолт False), но токен ЕЩЁ не создан — следующий рестарт с новым токеном
    должен пройти через основную ветку и проставить флаг."""
    from src.core import config as config_mod
    from src.core.constants import SYSTEM_DEPARTMENT_NAME
    from src.repositories.bots import BotRepository
    from src.repositories.departments import DepartmentRepository

    _silence_audit(monkeypatch)
    token = "dbos_bot_" + "f" * 43
    monkeypatch.setenv("WORKER_BOT_TOKEN", token)
    config_mod.get_settings.cache_clear()

    await bootstrap_service.bootstrap_platform_services(db)

    dept_repo = DepartmentRepository(db)
    dept = await dept_repo.create(SYSTEM_DEPARTMENT_NAME)
    bot = await BotRepository(db).create(
        name=WORKER_BOT_NAME,
        department_id=dept.id,
        allowed_services=[WORKER_BOT_SERVICE],
        description="legacy bootstrap, no flag yet",
        created_by="bootstrap",
    )
    await db.commit()
    assert bot.is_service_bot is False

    await bootstrap_service.bootstrap_worker_bot(db)

    refreshed = await BotRepository(db).first_by_name(WORKER_BOT_NAME)
    assert refreshed.is_service_bot is True


async def test_bootstrap_worker_bot_backfills_flag_on_legacy_bot_fast_path(db, monkeypatch):
    """Тот же legacy-бот, но токен уже заведён (хэш совпадает) — быстрый
    идемпотентный путь тоже должен донастроить флаг, а не только no-op."""
    from src.core import config as config_mod
    from src.core.constants import SYSTEM_DEPARTMENT_NAME
    from src.core.security import hash_opaque_token
    from src.repositories.bot_tokens import BotTokenRepository
    from src.repositories.bots import BotRepository
    from src.repositories.departments import DepartmentRepository

    emitted = _silence_audit(monkeypatch)
    token = "dbos_bot_" + "g" * 43
    monkeypatch.setenv("WORKER_BOT_TOKEN", token)
    config_mod.get_settings.cache_clear()

    await bootstrap_service.bootstrap_platform_services(db)

    dept_repo = DepartmentRepository(db)
    dept = await dept_repo.create(SYSTEM_DEPARTMENT_NAME)
    bot = await BotRepository(db).create(
        name=WORKER_BOT_NAME,
        department_id=dept.id,
        allowed_services=[WORKER_BOT_SERVICE],
        description="legacy bootstrap, token already issued",
        created_by="bootstrap",
    )
    await BotTokenRepository(db).create(
        bot_id=bot.id,
        name="bootstrap",
        token_hash=hash_opaque_token(token),
        token_prefix=token[:8],
        expires_at=None,
    )
    await db.commit()
    assert bot.is_service_bot is False

    emitted.clear()
    await bootstrap_service.bootstrap_worker_bot(db)

    refreshed = await BotRepository(db).first_by_name(WORKER_BOT_NAME)
    assert refreshed.is_service_bot is True


async def test_bootstrap_worker_bot_idempotent(db, monkeypatch):
    """Повторный вызов — no-op: ровно один токен с хэшем WORKER_BOT_TOKEN."""
    from src.core import config as config_mod
    from src.core.security import hash_opaque_token
    from src.repositories.bot_tokens import BotTokenRepository
    from src.repositories.bots import BotRepository

    emitted = _silence_audit(monkeypatch)
    token = "dbos_bot_" + "b" * 43
    monkeypatch.setenv("WORKER_BOT_TOKEN", token)
    config_mod.get_settings.cache_clear()

    await bootstrap_service.bootstrap_platform_services(db)
    await bootstrap_service.bootstrap_worker_bot(db)
    assert any(action == "bot.create" for (action, _) in emitted)

    emitted.clear()
    await bootstrap_service.bootstrap_worker_bot(db)
    assert emitted == [], "второй прогон worker-бота должен быть no-op"

    bot = await BotRepository(db).first_by_name(WORKER_BOT_NAME)
    tokens = await BotTokenRepository(db).list_for_bot(bot.id)
    assert len(tokens) == 1
    assert tokens[0].token_hash == hash_opaque_token(token)


# ── testing_service-бот ──────────────────────────────────────────────────────


async def test_bootstrap_server_service_bot_is_idempotent_and_can_only_enter_secrets(db, monkeypatch):
    from src.repositories.bots import BotRepository
    from src.services import authorization_service
    _silence_audit(monkeypatch)
    token = "dbos_bot_" + "s" * 43
    monkeypatch.setenv("SERVER_SERVICE_BOT_TOKEN", token)
    await bootstrap_service.bootstrap_platform_services(db)
    await bootstrap_service.bootstrap_server_service_bot(db)
    first = await BotRepository(db).first_by_name("server_service")
    await bootstrap_service.bootstrap_server_service_bot(db)
    second = await BotRepository(db).first_by_name("server_service")
    assert first.id == second.id
    identity = await authorization_service.introspect(db, token)
    assert identity.active and identity.is_service_bot
    assert identity.allowed_services == ["secret_service"]
    assert identity.service_roles == {"secret_service": ["guest"]}


async def test_bootstrap_server_service_bot_without_token_is_noop(db, monkeypatch):
    from src.repositories.bots import BotRepository
    monkeypatch.setenv("SERVER_SERVICE_BOT_TOKEN", "")
    await bootstrap_service.bootstrap_server_service_bot(db)
    assert await BotRepository(db).first_by_name("server_service") is None


async def test_bootstrap_testing_service_bot_grants_server_and_secret_service(
    db, monkeypatch
):
    """Бот заведён с ролью guest на server_service И secret_service."""
    from src.core import config as config_mod
    from src.core.constants import (
        TESTING_SERVICE_BOT_NAME,
        TESTING_SERVICE_BOT_ROLE,
        TESTING_SERVICE_BOT_SECRET_SERVICE,
        TESTING_SERVICE_BOT_SERVICE,
    )
    from src.repositories.bot_roles import BotRoleRepository
    from src.repositories.bots import BotRepository
    from src.services import authorization_service

    _silence_audit(monkeypatch)
    token = "dbos_bot_" + "c" * 43
    monkeypatch.setenv("TESTING_SERVICE_BOT_TOKEN", token)
    config_mod.get_settings.cache_clear()

    await bootstrap_service.bootstrap_platform_services(db)
    await bootstrap_service.bootstrap_testing_service_bot(db)

    bot = await BotRepository(db).first_by_name(TESTING_SERVICE_BOT_NAME)
    assert bot is not None
    assert set(bot.allowed_services) == {
        TESTING_SERVICE_BOT_SERVICE,
        TESTING_SERVICE_BOT_SECRET_SERVICE,
    }

    role_repo = BotRoleRepository(db)
    assert TESTING_SERVICE_BOT_ROLE in await role_repo.get_roles_by_service(
        bot.id, TESTING_SERVICE_BOT_SERVICE
    )
    assert TESTING_SERVICE_BOT_ROLE in await role_repo.get_roles_by_service(
        bot.id, TESTING_SERVICE_BOT_SECRET_SERVICE
    )

    assert bot.is_service_bot is True

    res = await authorization_service.introspect(db, token)
    assert res.active is True
    assert res.service_roles.get(TESTING_SERVICE_BOT_SECRET_SERVICE) == [
        TESTING_SERVICE_BOT_ROLE
    ]
    assert res.is_service_bot is True


async def test_bootstrap_testing_service_bot_backfills_secret_grant_on_preexisting_bot(
    db, monkeypatch
):
    """Бот, заведённый прежней версией бутстрапа (только server_service),
    догоняет secret_service-грант без пересоздания токена."""
    from src.core import config as config_mod
    from src.core.constants import (
        SYSTEM_DEPARTMENT_NAME,
        TESTING_SERVICE_BOT_NAME,
        TESTING_SERVICE_BOT_ROLE,
        TESTING_SERVICE_BOT_SECRET_SERVICE,
        TESTING_SERVICE_BOT_SERVICE,
    )
    from src.core.security import hash_opaque_token
    from src.repositories.bot_roles import BotRoleRepository
    from src.repositories.bot_tokens import BotTokenRepository
    from src.repositories.bots import BotRepository
    from src.repositories.departments import DepartmentRepository

    emitted = _silence_audit(monkeypatch)
    token = "dbos_bot_" + "d" * 43
    monkeypatch.setenv("TESTING_SERVICE_BOT_TOKEN", token)
    config_mod.get_settings.cache_clear()

    await bootstrap_service.bootstrap_platform_services(db)

    dept_repo = DepartmentRepository(db)
    dept = await dept_repo.create(SYSTEM_DEPARTMENT_NAME)
    await dept_repo.grant_access(
        dept.id, TESTING_SERVICE_BOT_SERVICE, granted_by="bootstrap"
    )
    bot = await BotRepository(db).create(
        name=TESTING_SERVICE_BOT_NAME,
        department_id=dept.id,
        allowed_services=[TESTING_SERVICE_BOT_SERVICE],
        description="legacy bootstrap",
        created_by="bootstrap",
    )
    await BotTokenRepository(db).create(
        bot_id=bot.id,
        name="bootstrap",
        token_hash=hash_opaque_token(token),
        token_prefix=token[:8],
        expires_at=None,
    )
    await db.commit()
    assert bot.is_service_bot is False

    emitted.clear()
    await bootstrap_service.bootstrap_testing_service_bot(db)

    refreshed = await BotRepository(db).first_by_name(TESTING_SERVICE_BOT_NAME)
    assert TESTING_SERVICE_BOT_SECRET_SERVICE in refreshed.allowed_services
    assert TESTING_SERVICE_BOT_ROLE in await BotRoleRepository(db).get_roles_by_service(
        refreshed.id, TESTING_SERVICE_BOT_SECRET_SERVICE
    )
    tokens = await BotTokenRepository(db).list_for_bot(refreshed.id)
    assert len(tokens) == 1, "не должен пересоздать токен"
    assert any(action == "bot.roles_assign" for (action, _) in emitted)
    assert not any(action == "bot.create" for (action, _) in emitted)
    # Флаг заведён до появления колонки (дефолт False) — этот рестарт
    # должен его донастроить, даже когда токен уже существовал.
    assert refreshed.is_service_bot is True

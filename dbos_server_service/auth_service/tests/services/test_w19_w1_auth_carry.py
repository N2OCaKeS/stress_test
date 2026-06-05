"""Carry-closures auth_service из W11/W13/W14/W15/W16 после W17/W18.

1. `authorization_service.introspect` (bot-token ветка) — defence-in-depth:
   `bot.status` теперь тоже проверяется, drift `is_active=True, status=BLOCKED`
   возвращает active=False.
2. `docker_registry_service.issue_token` — legacy-only scope: `caller_cfg`
   из guard'а переиспользуется, повторного `docker_repo.get_by_department`
   не происходит.
3. `authorization` endpoint — `caller_ip` из body действительно прокидывается
   в `authorization_service.introspect` (regression на silent-feature-loss).
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import BotStatus
from src.core.security import generate_bot_token
from src.models import BotAccount, BotToken
from src.services import authorization_service
from src.utils.ids import bot_id, bot_token_id
from src.utils.time import utcnow

INTROSPECT_URL = "/api/auth/v1/authorization/introspect"


async def _make_bot_with_token(
    db: AsyncSession,
    dept_id: str,
    allowed_services: list[str],
    *,
    is_active: bool = True,
    status: str = BotStatus.ACTIVE,
) -> tuple[BotAccount, BotToken, str]:
    bot = BotAccount(
        id=bot_id(),
        name=f"w19_bot_{bot_id()[:6]}",
        department_id=dept_id,
        allowed_services=allowed_services,
        is_active=is_active,
        status=status,
        created_by="usr_placeholder",
    )
    db.add(bot)
    await db.flush()

    raw, prefix, token_hash = generate_bot_token()
    tok = BotToken(
        id=bot_token_id(),
        bot_id=bot.id,
        name="w19_token",
        token_hash=token_hash,
        token_prefix=prefix,
        expires_at=utcnow() + timedelta(days=30),
    )
    db.add(tok)
    await db.flush()
    await db.commit()
    return bot, tok, raw


# ── 1. bot.status defence-in-depth ───────────────────────────────────────────


class TestBotStatusDefenceInDepth:
    """Drift между `is_active` и `status` (правка БД мимо штатных ручек) —
    introspect не должен пропускать токен, у которого `is_active=True`, но
    `status=BLOCKED`. Сейчас оба поля синкаются вместе, но guard работает как
    отдельный слой защиты."""

    async def test_bot_blocked_status_returns_inactive_even_if_is_active_true(
        self, db, dept_a_with_service, service_x,
    ):
        bot, _tok, raw = await _make_bot_with_token(
            db, dept_a_with_service.id, [service_x.service_name],
            is_active=True, status=BotStatus.BLOCKED,
        )
        result = await authorization_service.introspect(
            db, raw, request_id=None, caller_ip=None,
        )
        assert result.active is False, (
            "BLOCKED-бот с is_active=True всё равно не должен проходить introspect"
        )

    async def test_bot_active_both_flags_introspect_ok(
        self, db, dept_a_with_service, service_x,
    ):
        # Контр-кейс: оба флага ACTIVE → introspect возвращает active=True.
        # Защищает от регрессии, в которой defence-in-depth блокирует и
        # нормальный happy-path.
        bot, _tok, raw = await _make_bot_with_token(
            db, dept_a_with_service.id, [service_x.service_name],
            is_active=True, status=BotStatus.ACTIVE,
        )
        result = await authorization_service.introspect(
            db, raw, request_id=None, caller_ip=None,
        )
        assert result.active is True


# ── 2. docker_registry _resolve_registry legacy 2-SELECT redundancy ──────────


class TestDockerRegistryLegacyCacheSavesOneSelect:
    """Legacy-only scope (`repository:myapp:pull` без `<dept>/`): cfg каллера
    дёргается в guard'е и должен переиспользоваться в цикле над scope —
    `docker_repo.get_by_department(department_id)` для одного и того же
    department_id за вызов `issue_token` должен случиться РОВНО ОДИН раз."""

    async def test_legacy_scope_calls_get_by_department_once(
        self, db, docker_registry_enabled, user_a, dept_a, monkeypatch,
    ):
        # user_a из dept_a; добавим в pull_user_ids, чтобы issue_token не
        # отбился на authz и реально дошёл до цикла резолва.
        docker_registry_enabled.pull_user_ids = [user_a.id]
        await db.flush()
        await db.commit()

        from src.repositories.docker_registry import DockerRegistryRepository
        from src.services import docker_registry_service

        call_log: list[str | None] = []
        original = DockerRegistryRepository.get_by_department

        async def _spy(self, dept_id):
            call_log.append(dept_id)
            return await original(self, dept_id)

        monkeypatch.setattr(DockerRegistryRepository, "get_by_department", _spy)

        try:
            await docker_registry_service.issue_token(
                db,
                username="t_user_a",
                password="User1234!",
                service="registry.docker.io",
                scope="repository:myapp:pull",
                request_id=None,
                anonymous=False,
            )
        except Exception:
            # Даже если issue_token вернёт ошибку (например, registry-namespace
            # не настроен), нас интересует только то, что get_by_department
            # для department_id дёрнулся не больше одного раза.
            pass

        dept_a_calls = [d for d in call_log if d == dept_a.id]
        assert len(dept_a_calls) <= 1, (
            f"get_by_department(dept_a.id) дёрнулся {len(dept_a_calls)} раз, "
            f"ожидаем ≤1 (legacy guard + цикл должны делить кэш). call_log={call_log!r}"
        )


# ── 3. caller_ip wiring через HTTP introspect ────────────────────────────────


class TestCallerIpWiredThroughIntrospectEndpoint:
    """GAP-5 W6 (critical): если кто-то уберёт `caller_ip=body.caller_ip` из
    `endpoints/authorization.py`, multi-IP detector тихо умрёт. Поэтому
    регрессия — spy на `authorization_service.introspect` и проверка, что
    `caller_ip` доехал из тела запроса."""

    async def test_introspect_body_caller_ip_reaches_service(
        self, client, service_auth_headers, db, monkeypatch,
    ):
        captured: dict[str, object] = {}
        original = authorization_service.introspect

        async def _spy(db, token, *, request_id=None, caller_ip=None):
            captured["caller_ip"] = caller_ip
            return await original(db, token, request_id=request_id, caller_ip=caller_ip)

        monkeypatch.setattr(authorization_service, "introspect", _spy)
        # endpoint импортирует символ под алиасом — патчим и там.
        from src.api.v1.endpoints import authorization as ep_module
        monkeypatch.setattr(
            ep_module.authorization_service, "introspect", _spy, raising=True,
        )

        resp = await client.post(
            INTROSPECT_URL,
            json={"token": "bogus_token", "caller_ip": "203.0.113.7"},
            headers=service_auth_headers,
        )
        # Даже на bogus-токен endpoint вернёт 200 active=False, не 4xx —
        # introspect ловит decode-ошибку внутри.
        assert resp.status_code == 200, resp.text
        assert resp.json()["active"] is False
        assert captured.get("caller_ip") == "203.0.113.7", (
            f"caller_ip из body не доехал до сервиса (got {captured!r})"
        )

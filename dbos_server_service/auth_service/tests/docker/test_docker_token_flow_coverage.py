"""Дополнительное покрытие docker-registry token flow.

Логика самого `docker_registry_service.issue_token` богаче, чем покрывают
`test_docker_token.py`, `test_docker_per_dept_push.py` и
`test_docker_token_coverage.py`: остаются ветки про expired bot-token,
direct-password путь для banned/inactive юзера, anon-смешанный pull+push
scope, multi-resource scope (несколько частей в `scope`), резервный путь
`_parse_scope` для коротких segments, anon-pull на отсутствующий registry,
inactive bot без инкремента счётчика.
"""

from datetime import datetime, timedelta, timezone

import jwt as _jwt
import pytest_asyncio
from sqlalchemy import update

from src.models import User
from src.models.bot_account import BotAccount
from src.models.bot_token import BotToken
from src.models.department_docker_registry import DepartmentDockerRegistry
from src.utils.ids import _new_id
from tests._helpers.http import _basic  # noqa: F401 — общий helper
from datetime import timedelta
from src.utils.time import utcnow

TOKEN_URL = "/api/auth/v1/docker/token"
BOTS_URL = "/api/auth/v1/bots"


def _decode_access(token: str) -> list[dict]:
    return _jwt.decode(token, options={"verify_signature": False}).get("access", [])


def _capture_audit(monkeypatch) -> list[dict]:
    captured: list[dict] = []
    import src.services.audit_service as audit_mod
    original = audit_mod.emit

    def _spy(action, actor_id=None, **kw):
        captured.append({"action": action, "actor_id": actor_id, **kw})
        return original(action, actor_id, **kw)

    monkeypatch.setattr(audit_mod, "emit", _spy)
    return captured


@pytest_asyncio.fixture()
async def registry_a_pull_all(db, dept_a, user_a):
    cfg = DepartmentDockerRegistry(
        id=_new_id("ddr_"),
        department_id=dept_a.id,
        is_enabled=True,
        pull_policy="all",
        pull_user_ids=[],
        push_user_ids=[user_a.id],
    )
    db.add(cfg)
    await db.flush()
    return cfg


# ── Expired bot-token: line 288 (`bot_token.expires_at and is_expired`) ──────


class TestBotTokenExpired:
    async def test_expired_bot_token_denied(
        self, client, admin_token, dept_a, registry_a_pull_all, db, monkeypatch,
    ):
        """Валидный (не revoked) bot-token, но `expires_at` в прошлом →
        ветка `not (bot_token.expires_at and is_expired)` ложна → fall-through
        к failure-пути. Симметрия с PAT-expired (`test_expired_pat_denied`)."""
        bot_resp = await client.post(
            BOTS_URL,
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "name": "exp_bot",
                "department_id": dept_a.id,
                "allowed_services": [],
            },
        )
        assert bot_resp.status_code == 201, bot_resp.text
        bot_id = bot_resp.json()["bot_id"]

        tok_resp = await client.post(
            f"{BOTS_URL}/{bot_id}/tokens",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": "exp_tok", "expires_at": (utcnow() + timedelta(days=30)).isoformat()},
        )
        assert tok_resp.status_code == 201, tok_resp.text
        raw = tok_resp.json()["token"]
        tok_id = tok_resp.json()["token_id"]

        past = datetime.now(timezone.utc) - timedelta(hours=1)
        await db.execute(
            update(BotToken).where(BotToken.id == tok_id).values(expires_at=past)
        )
        await db.commit()

        captured = _capture_audit(monkeypatch)
        resp = await client.get(
            TOKEN_URL,
            headers=_basic("exp_bot", raw),
            params={"service": "registry.test"},
        )
        assert resp.status_code == 401, resp.text
        assert resp.json()["error_code"] == "INVALID_CREDENTIALS"

        failures = [
            e for e in captured
            if e["action"] == "docker.token_issued" and e.get("status") == "failure"
        ]
        assert failures, captured
        ev = failures[-1]
        assert ev["details"]["subject_type"] == "bot_token"
        assert ev["details"]["reason"] == "invalid_credentials"


# ── Banned user via password: line 342 (`if not user.is_active`) ─────────────


class TestBannedUserPasswordPath:
    async def test_banned_user_password_path_denied_without_argon_verify(
        self, client, user_a, dept_a, registry_a_pull_all, db, monkeypatch,
    ):
        """`status=BANNED` + `is_active=False` + правильный пароль → 401
        INVALID_CREDENTIALS ДО Argon2id verify. PAT-inactive путь покрыт отдельно,
        здесь — password-канал (отдельная ветка после `get_by_username`)."""
        await db.execute(
            update(User).where(User.id == user_a.id).values(
                is_active=False, status="banned",
            )
        )
        await db.commit()

        captured = _capture_audit(monkeypatch)
        resp = await client.get(
            TOKEN_URL,
            headers=_basic("t_user_a", "User12345678!"),
            params={"service": "registry.test"},
        )
        assert resp.status_code == 401, resp.text
        assert resp.json()["error_code"] == "INVALID_CREDENTIALS"

        failures = [
            e for e in captured
            if e["action"] == "docker.token_issued" and e.get("status") == "failure"
        ]
        assert failures, captured
        ev = failures[-1]
        assert ev["details"]["subject_type"] == "password"
        assert ev["details"]["reason"] == "invalid_credentials"


# ── Empty actions в scope: `repository:foo/bar:` → entry silently skipped ────


class TestEmptyActionsScope:
    async def test_scope_with_trailing_colon_empty_actions_no_grants(
        self, client, user_a, dept_a, registry_a_pull_all, monkeypatch,
    ):
        """`repository:dept/img:` парсится в `actions=['']`. Ни `'pull'`, ни
        `'push'` не присутствуют — обе ветки в цикле обработки entries
        пропускаются, access остаётся пустым. Никакого audit-шума, никакого
        granted — спокойный «handshake» сценарий."""
        captured = _capture_audit(monkeypatch)
        resp = await client.get(
            TOKEN_URL,
            headers=_basic("t_user_a", "User12345678!"),
            params={
                "service": "registry.test",
                "scope": f"repository:{dept_a.name}/image:",
            },
        )
        assert resp.status_code == 200, resp.text
        access = _decode_access(resp.json()["access_token"])
        assert access == [], access

        pull_denied = [e for e in captured if e["action"] == "docker.pull_denied"]
        push_denied = [e for e in captured if e["action"] == "docker.push_denied"]
        assert pull_denied == [], pull_denied
        assert push_denied == [], push_denied


# ── Multi-resource scope: несколько частей через пробел ──────────────────────


class TestMultiResourceScope:
    async def test_two_pull_resources_both_granted(
        self, client, user_a, dept_a, registry_a_pull_all,
    ):
        """`scope` с двумя repository-частями через пробел (Docker spec
        допускает несколько scope-entries в одном параметре) → каждый
        обрабатывается отдельно в цикле `for entry in requested_access`."""
        scope = (
            f"repository:{dept_a.name}/image_one:pull"
            f" repository:{dept_a.name}/image_two:pull"
        )
        resp = await client.get(
            TOKEN_URL,
            headers=_basic("t_user_a", "User12345678!"),
            params={"service": "registry.test", "scope": scope},
        )
        assert resp.status_code == 200, resp.text
        access = _decode_access(resp.json()["access_token"])
        names = {e["name"] for e in access if "pull" in e.get("actions", [])}
        assert names == {
            f"{dept_a.name}/image_one",
            f"{dept_a.name}/image_two",
        }, access


# ── _parse_scope: segments < 3 silently dropped ─────────────────────────────


class TestParseScopeShortSegments:
    async def test_scope_with_two_segments_silently_ignored(
        self, client, user_a, dept_a, registry_a_pull_all,
    ):
        """`repository:foo` — только 2 segment'а, нет `:actions`. Docker
        token spec разрешает такие «handshake» scope'ы (issuer должен
        вернуть пустой access, не 400). Покрывает `if len(segments) >= 3`
        false-ветку в `_parse_scope`."""
        resp = await client.get(
            TOKEN_URL,
            headers=_basic("t_user_a", "User12345678!"),
            params={"service": "registry.test", "scope": "repository:foo"},
        )
        assert resp.status_code == 200, resp.text
        access = _decode_access(resp.json()["access_token"])
        assert access == [], access


# ── Anonymous pull on non-existent registry: line 587 (silent, no audit) ────


class TestAnonymousPullNonexistentRegistry:
    async def test_anonymous_pull_on_ghost_registry_silently_omitted(
        self, client, dept_a, registry_a_pull_all, monkeypatch,
    ):
        """Анон pull на registry, которого нет в БД → cfg=None, ветка
        `if not anonymous` ложна → silent skip без аудита (анон и так
        получит 401 от registry'я на v2/_catalog)."""
        captured = _capture_audit(monkeypatch)
        resp = await client.get(
            TOKEN_URL,
            params={
                "service": "registry.test",
                "scope": "repository:ghost_dept_xyz/img:pull",
            },
        )
        assert resp.status_code == 200, resp.text
        access = _decode_access(resp.json()["access_token"])
        assert all("pull" not in e.get("actions", []) for e in access), access

        pull_denied = [e for e in captured if e["action"] == "docker.pull_denied"]
        assert pull_denied == [], pull_denied


# ── Inactive bot: line 290 false → no failure-increment branch ──────────────


class TestInactiveBotNoLockoutIncrement:
    async def test_inactive_bot_with_valid_token_denied_without_increment(
        self, client, admin_token, dept_a, registry_a_pull_all, db, monkeypatch,
    ):
        """Bot с `is_active=False` + валидный (не revoked, не expired) токен →
        ветка `bot_for_success.is_active` ложна. fall-through к target_bot;
        target_bot тоже `is_active=False`, поэтому ветка инкремента счётчика
        пропускается (DoS-vector guard: иначе disable-flag превращался бы в
        permanent lockout после реактивации). `failed_token_attempts` остаётся
        нетронутым."""
        bot_resp = await client.post(
            BOTS_URL,
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "name": "inactive_bot",
                "department_id": dept_a.id,
                "allowed_services": [],
            },
        )
        assert bot_resp.status_code == 201, bot_resp.text
        bot_id = bot_resp.json()["bot_id"]

        tok_resp = await client.post(
            f"{BOTS_URL}/{bot_id}/tokens",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": "inactive_tok", "expires_at": (utcnow() + timedelta(days=30)).isoformat()},
        )
        raw = tok_resp.json()["token"]

        await db.execute(
            update(BotAccount).where(BotAccount.id == bot_id).values(is_active=False)
        )
        await db.commit()

        captured = _capture_audit(monkeypatch)
        resp = await client.get(
            TOKEN_URL,
            headers=_basic("inactive_bot", raw),
            params={"service": "registry.test"},
        )
        assert resp.status_code == 401, resp.text
        assert resp.json()["error_code"] == "INVALID_CREDENTIALS"

        # Счётчик не должен был тикнуть для inactive бота.
        from sqlalchemy import select
        row = (await db.execute(
            select(BotAccount).where(BotAccount.id == bot_id)
        )).scalar_one()
        assert row.failed_token_attempts == 0, row.failed_token_attempts

        failures = [
            e for e in captured
            if e["action"] == "docker.token_issued" and e.get("status") == "failure"
        ]
        assert failures, captured
        assert failures[-1]["details"]["subject_type"] == "bot_token"


# ── Resource name с двоеточием внутри: ":".join(segments[1:-1]) ─────────────


class TestScopeResourceNameWithColon:
    async def test_scope_resource_name_preserves_internal_colon(
        self, client, user_a, dept_a, registry_a_pull_all,
    ):
        """Docker registry допускает имена с двоеточием (теги/digest:
        `repository:dept/img:tag:pull`). `_parse_scope` собирает имя через
        `":".join(segments[1:-1])` — 4-segment scope парсится в
        `name=dept/img:tag`. Покрывает ветку, где `len(segments) > 3`."""
        # registry_a_pull_all включает pull-policy=all, поэтому любой
        # depA/<name> pull проходит — assertion на сам resolve-цикл.
        scope = f"repository:{dept_a.name}/img:v1:pull"
        resp = await client.get(
            TOKEN_URL,
            headers=_basic("t_user_a", "User12345678!"),
            params={"service": "registry.test", "scope": scope},
        )
        assert resp.status_code == 200, resp.text
        access = _decode_access(resp.json()["access_token"])
        # `_parse_registry_name` берёт всё до первого `/`, поэтому registry
        # резолвится правильно. Имя сохраняет внутренний `:`.
        granted = [e for e in access if "pull" in e.get("actions", [])]
        assert granted, access
        assert granted[0]["name"] == f"{dept_a.name}/img:v1"

"""subject_type из introspect пробрасывается в audit `actor_type`.

Симметрично с auth_service: server_service хранит `subject_type` в
`IdentityContext`, кладёт в `audit_context.subject_type`, а
`audit_service.emit` подхватывает его как `actor_type` если caller не задал.

До фикса всё писалось `actor_type="user"` — worker_bot (PAT) и OAuth-клиенты
смешивались с человеческими действиями в SIEM-логе.
"""

from __future__ import annotations

import pytest

from src.dependencies import auth as auth_dep
from src.schemas.identity import IdentityContext
from src.services import audit_context, audit_service


def _body(subject_type: str | None) -> dict:
    return {
        "active": True,
        "sub": "usr_1",
        "username": "test",
        "department_id": "dep_a",
        "department_name": "Dept A",
        "platform_role": None,
        "service_roles": {"server_service": ["reader"]},
        "allowed_services": ["server_service"],
        "is_banned": False,
        "subject_type": subject_type,
    }


def test_to_identity_extracts_subject_type():
    """`_to_identity` достаёт subject_type из introspect-body."""
    for st in ("user", "bot", "pat", "oauth_client", None):
        identity = auth_dep._to_identity(_body(st))
        assert identity.subject_type == st


def test_audit_emit_picks_actor_type_from_context(monkeypatch):
    """`emit()` без explicit actor_type → берёт из ctx.subject_type."""
    captured = {}

    def fake_send(payload, *_a, **_kw):
        captured.update(payload)

    monkeypatch.setattr(audit_service, "_send_to_logging_service", fake_send)

    # Имитируем что middleware/identity-resolve уже поставил ctx.
    ctx = audit_context.AuditContext(
        actor_id="bot_1",
        username="server_worker_user",
        subject_type="bot",
    )
    token = audit_context.set_context(ctx)
    try:
        # Sync-path: нет running loop → emit падает в sync httpx.post fallback.
        # Поэтому monkeypatch'аем httpx.post тоже.
        sent = {}

        def fake_post(*args, **kwargs):
            sent["payload"] = kwargs.get("json")

            class _R:
                status_code = 200

            return _R()

        monkeypatch.setattr(audit_service.httpx, "post", fake_post)
        monkeypatch.setattr(
            audit_service,
            "get_settings",
            lambda: type("S", (), {
                "logging_service_url": "http://logging",
                "logging_service_api_key": "k",
            })(),
        )

        audit_service.emit("test.action", target_id="t1")
    finally:
        audit_context.reset_context(token)

    payload = sent["payload"]
    assert payload["actor_type"] == "bot"
    assert payload["actor_id"] == "bot_1"


def test_audit_emit_explicit_actor_type_wins(monkeypatch):
    """Явный `actor_type=...` имеет приоритет над ctx.subject_type."""
    sent = {}

    def fake_post(*args, **kwargs):
        sent["payload"] = kwargs.get("json")

        class _R:
            status_code = 200

        return _R()

    monkeypatch.setattr(audit_service.httpx, "post", fake_post)
    monkeypatch.setattr(
        audit_service,
        "get_settings",
        lambda: type("S", (), {
            "logging_service_url": "http://logging",
            "logging_service_api_key": "k",
        })(),
    )

    ctx = audit_context.AuditContext(actor_id="bot_x", subject_type="bot")
    token = audit_context.set_context(ctx)
    try:
        # service.started lifecycle — actor_type="system" должен победить.
        audit_service.emit("service.started", actor_type="system", actor_id="svc")
    finally:
        audit_context.reset_context(token)

    assert sent["payload"]["actor_type"] == "system"
    assert sent["payload"]["actor_id"] == "svc"


def test_audit_emit_no_context_falls_back_to_user(monkeypatch):
    """Без ctx (анонимный путь / startup) — fallback `actor_type="user"`."""
    sent = {}

    def fake_post(*args, **kwargs):
        sent["payload"] = kwargs.get("json")

        class _R:
            status_code = 200

        return _R()

    monkeypatch.setattr(audit_service.httpx, "post", fake_post)
    monkeypatch.setattr(
        audit_service,
        "get_settings",
        lambda: type("S", (), {
            "logging_service_url": "http://logging",
            "logging_service_api_key": "k",
        })(),
    )

    # Сбрасываем ctx (нет current context).
    audit_context._current.set(None)
    audit_service.emit("anon.action")

    assert sent["payload"]["actor_type"] == "user"


@pytest.mark.asyncio
async def test_get_current_identity_writes_subject_type_to_context(monkeypatch):
    """`get_current_identity` пишет subject_type в audit_context."""
    from fastapi import Request

    auth_dep._clear_introspect_cache()

    async def fake_introspect(token: str) -> dict:
        return _body("bot")

    monkeypatch.setattr(auth_dep, "_introspect", fake_introspect)

    # Фабрика минимального Request с Bearer-header'ом.
    scope = {
        "type": "http",
        "headers": [(b"authorization", b"Bearer " + b"dbos_bot_xxxxxxxxxxxxxxxxxxxx")],
        "client": ("127.0.0.1", 0),
    }
    request = Request(scope)

    # Сбрасываем ctx.
    audit_context._current.set(None)

    identity = await auth_dep.get_current_identity(request)
    assert identity.subject_type == "bot"

    ctx = audit_context.get_context()
    assert ctx.subject_type == "bot"
    assert ctx.actor_id == "usr_1"

"""Тесты `services/audit_service.py` — emit, redaction, retry-after, fallback."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.services import audit_service


# Перебиваем `tests/unit/conftest.py::_create_schema` — БД не трогаем.
@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    yield


@pytest.fixture(autouse=True)
def _reset_dropped():
    audit_service._reset_dropped_429_for_tests()
    yield
    audit_service._reset_dropped_429_for_tests()


@pytest.fixture
def configured_audit(monkeypatch):
    """Settings с URL и API-key, чтобы emit пошёл по сетевой ветке."""
    settings = MagicMock()
    settings.logging_service_url = "http://logging.test"
    settings.logging_service_api_key = "test-key"
    monkeypatch.setattr(audit_service, "get_settings", lambda: settings)
    return settings


# ── emit + cleanup pending tasks ────────────────────────────────────────────


async def _drain_pending() -> None:
    """Подождать завершения всех task'ов, которые emit зашедулил."""
    while audit_service._pending_audit_tasks:
        await asyncio.gather(*list(audit_service._pending_audit_tasks), return_exceptions=True)


async def test_emit_success_posts_payload(configured_audit):
    captured = []

    async def fake_post(path, json, headers):
        captured.append({"path": path, "json": json, "headers": headers})
        return MagicMock(status_code=200, headers={})

    mock_client = MagicMock()
    mock_client.post = AsyncMock(side_effect=fake_post)
    with patch.object(audit_service, "_audit_client", mock_client):
        audit_service.emit(
            "tokens.create",
            actor_id="usr_1",
            actor_type="user",
            target_id="cred_1",
            target_type="credential",
            status="success",
            allowed=True,
            details={"name": "jira_bot"},
        )
        await _drain_pending()

    assert len(captured) == 1
    payload = captured[0]["json"]
    assert payload["action"] == "tokens.create"
    assert payload["actor_id"] == "usr_1"
    assert payload["status"] == "success"
    assert payload["severity"] == "INFO"
    assert payload["details"]["name"] == "jira_bot"


async def test_emit_failure_status_picks_failure_severity(configured_audit):
    captured = []

    async def fake_post(path, json, headers):
        captured.append(json)
        return MagicMock(status_code=200, headers={})

    mock_client = MagicMock()
    mock_client.post = AsyncMock(side_effect=fake_post)
    with patch.object(audit_service, "_audit_client", mock_client):
        audit_service.emit("tokens.delete", status="failure", allowed=False)
        await _drain_pending()

    assert captured[0]["severity"] == "ERROR"


# ── redaction ───────────────────────────────────────────────────────────────


async def test_emit_masks_sensitive_fields(configured_audit):
    captured = []

    async def fake_post(path, json, headers):
        captured.append(json)
        return MagicMock(status_code=200, headers={})

    mock_client = MagicMock()
    mock_client.post = AsyncMock(side_effect=fake_post)
    with patch.object(audit_service, "_audit_client", mock_client):
        audit_service.emit(
            "tokens.revealed",
            details={
                "login": "alice",
                "secret": "super-plaintext",
                "secret_b64": "U1VQRVJTRUNSRVQ=",
                "password": "p",
                "token": "tok",
                "master_key": "mk",
                "acl_dump": {"dep_1": ["read"]},
                "safe_field": "safe-value",
            },
        )
        await _drain_pending()

    details = captured[0]["details"]
    assert details["login"] == "<PASSWORD>"
    assert details["secret"] == "<SECRET>"
    assert details["secret_b64"] == "<PASSWORD>"
    assert details["password"] == "<PASSWORD>"
    assert details["token"] == "<TOKEN>"
    assert details["master_key"] == "<SECRET>"
    assert details["acl_dump"] == "<CREDENTIAL>"
    assert details["safe_field"] == "safe-value"


# ── retry on 429 + retry-after honored ──────────────────────────────────────


async def test_retry_after_header_used_over_backoff(configured_audit, monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    responses = [
        MagicMock(status_code=429, headers={"Retry-After": "2"}),
        MagicMock(status_code=200, headers={}),
    ]
    mock_client = MagicMock()
    mock_client.post = AsyncMock(side_effect=responses)

    monkeypatch.setattr(audit_service.asyncio, "sleep", fake_sleep)
    with patch.object(audit_service, "_audit_client", mock_client):
        audit_service.emit("tokens.create")
        await _drain_pending()

    assert mock_client.post.await_count == 2
    # Retry-After=2s должен быть >= 0.5s базы — фактический sleep ~= max(jitter*0.5, 2) = 2.
    assert sleeps and sleeps[0] >= 2.0


async def test_three_429s_drop_increments_counter(configured_audit, monkeypatch):
    async def fake_sleep(_delay):
        pass

    responses = [
        MagicMock(status_code=429, headers={}),
        MagicMock(status_code=429, headers={}),
        MagicMock(status_code=429, headers={}),
    ]
    mock_client = MagicMock()
    mock_client.post = AsyncMock(side_effect=responses)
    monkeypatch.setattr(audit_service.asyncio, "sleep", fake_sleep)
    with patch.object(audit_service, "_audit_client", mock_client):
        audit_service.emit("tokens.create")
        await _drain_pending()

    assert audit_service.get_dropped_429_total() == 1


# ── fallback на error ───────────────────────────────────────────────────────


async def test_http_error_falls_back_to_sanitized_stub(configured_audit, caplog):
    mock_client = MagicMock()
    mock_client.post = AsyncMock(side_effect=httpx.ConnectError("nope"))
    caplog.set_level(logging.INFO, logger="audit")
    with patch.object(audit_service, "_audit_client", mock_client):
        audit_service.emit(
            "tokens.create",
            actor_id="usr_1",
            details={"secret": "leaky", "name": "x"},
        )
        await _drain_pending()

    fallback_records = [r for r in caplog.records if "audit_event_fallback" in r.getMessage()]
    assert fallback_records, "fallback log line expected"
    msg = fallback_records[0].getMessage()
    # Sanitized stub — без поля `details` (только action+actor+status+request_id).
    assert "tokens.create" in msg
    assert "usr_1" in msg
    assert "leaky" not in msg
    assert "secret" not in msg


async def test_no_logging_url_writes_local_stub_only(monkeypatch, caplog):
    settings = MagicMock()
    settings.logging_service_url = ""
    settings.logging_service_api_key = ""
    monkeypatch.setattr(audit_service, "get_settings", lambda: settings)

    caplog.set_level(logging.INFO, logger="audit")
    audit_service.emit(
        "tokens.create",
        actor_id="usr_1",
        details={"secret": "leaky"},
    )
    msgs = [r.getMessage() for r in caplog.records if "audit_event" in r.getMessage()]
    assert msgs and "leaky" not in msgs[-1]


# ── parse retry-after edge cases ────────────────────────────────────────────


def test_parse_retry_after_seconds_handles_invalid():
    assert audit_service._parse_retry_after_seconds(None) is None
    assert audit_service._parse_retry_after_seconds("") is None
    assert audit_service._parse_retry_after_seconds("abc") is None
    assert audit_service._parse_retry_after_seconds("-1") is None
    assert audit_service._parse_retry_after_seconds("1") == 1.0
    # Cap на 30s.
    assert audit_service._parse_retry_after_seconds("9999") == 30.0

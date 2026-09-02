"""Тесты `/ready` payload с counters: secrets_total / blocked_total /
redis_connected / audit_dropped_429_total + degraded на падении БД.

Top-level `conftest._mock_db_connect` подсовывает MagicMock-engine для всех
тестов — здесь его переопределяем per-test, чтобы вернуть конкретные COUNT'ы
или сломать `connect()`. Redis также мокается на уровне `reveal_throttle`.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.api.v1.endpoints import health as health_mod
from src.services import audit_service, reveal_throttle


def _engine_with_counts(total: int, blocked: int) -> MagicMock:
    """Фейк-engine: SELECT 1 → MagicMock, оба COUNT'а → scalar()-овые int'ы."""

    results: list[MagicMock] = []

    select_one = MagicMock()
    select_one.scalar = MagicMock(return_value=1)
    results.append(select_one)

    total_result = MagicMock()
    total_result.scalar = MagicMock(return_value=total)
    results.append(total_result)

    blocked_result = MagicMock()
    blocked_result.scalar = MagicMock(return_value=blocked)
    results.append(blocked_result)

    fake_conn = MagicMock()
    fake_conn.execute = AsyncMock(side_effect=results)

    class _Ctx:
        async def __aenter__(self):
            return fake_conn

        async def __aexit__(self, exc_type, exc, tb):
            return False

    fake_engine = MagicMock()
    fake_engine.connect = lambda: _Ctx()
    return fake_engine


def _broken_engine() -> MagicMock:
    """Engine, чей connect() бросает на входе в async-контекст."""

    class _Ctx:
        async def __aenter__(self):
            raise RuntimeError("db down")

        async def __aexit__(self, exc_type, exc, tb):
            return False

    fake_engine = MagicMock()
    fake_engine.connect = lambda: _Ctx()
    return fake_engine


@pytest.fixture
def _reset_audit_counter():
    audit_service._reset_dropped_429_for_tests()
    yield
    audit_service._reset_dropped_429_for_tests()


@pytest.mark.asyncio
async def test_ready_counters_reflect_db_state(client, monkeypatch, _reset_audit_counter):
    monkeypatch.setattr(health_mod, "engine", _engine_with_counts(total=5, blocked=2))
    # Redis pinger возвращает True — мокаем `_ensure_redis_client` на объект с
    # async ping().
    fake_redis = MagicMock()
    fake_redis.ping = AsyncMock(return_value=True)
    monkeypatch.setattr(reveal_throttle, "_ensure_redis_client", lambda: fake_redis)

    response = await client.get("/api/secret/v1/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["db"] is True
    assert body["secrets_total"] == 5
    assert body["blocked_total"] == 2
    assert body["redis_connected"] is True
    assert body["audit_dropped_429_total"] == 0


@pytest.mark.asyncio
async def test_ready_degraded_when_db_down(client, monkeypatch, _reset_audit_counter):
    monkeypatch.setattr(health_mod, "engine", _broken_engine())
    monkeypatch.setattr(reveal_throttle, "_ensure_redis_client", lambda: None)

    response = await client.get("/api/secret/v1/ready")
    # На сбое БД ready остаётся 200, но status=degraded + db=false; payload
    # летит оператору. k8s reacts на тег, не на HTTP-код.
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["db"] is False
    assert body["secrets_total"] == 0
    assert body["blocked_total"] == 0
    assert body["redis_connected"] is False


@pytest.mark.asyncio
async def test_ready_redis_disconnected_does_not_degrade(client, monkeypatch, _reset_audit_counter):
    monkeypatch.setattr(health_mod, "engine", _engine_with_counts(total=0, blocked=0))
    monkeypatch.setattr(reveal_throttle, "_ensure_redis_client", lambda: None)

    response = await client.get("/api/secret/v1/ready")
    assert response.status_code == 200
    body = response.json()
    # Redis отвалился, но БД жива → ok (Redis best-effort'ный, fallback на in-memory).
    assert body["status"] == "ok"
    assert body["redis_connected"] is False
    assert body["secrets_total"] == 0
    assert body["blocked_total"] == 0


@pytest.mark.asyncio
async def test_ready_audit_drop_counter_reported(client, monkeypatch, _reset_audit_counter):
    monkeypatch.setattr(health_mod, "engine", _engine_with_counts(total=1, blocked=0))
    monkeypatch.setattr(reveal_throttle, "_ensure_redis_client", lambda: None)

    audit_service._audit_dropped_429 = 7  # type: ignore[attr-defined]

    response = await client.get("/api/secret/v1/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["audit_dropped_429_total"] == 7

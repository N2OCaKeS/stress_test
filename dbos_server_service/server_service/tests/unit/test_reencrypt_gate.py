"""Maintenance-gate форсированной перешифровки (HTTP-слой).

При активном force-режиме сервис отвечает 503 REENCRYPT_IN_PROGRESS +
Retry-After на всё, кроме статуса перешифровки и health/ready. Проверяем:

* force выключен → обычные эндпоинты работают как раньше (gate прозрачен);
* force включён → бизнес-эндпоинт получает 503 + Retry-After + envelope;
* force включён → статус перешифровки и health по-прежнему доступны (exempt).

Force-состояние gate'а читает через `force_gate_state_cached`, открывающий
собственную сессию. В тестах это не пересекается с savepoint-изоляцией
`db`-фикстуры, поэтому активный force подкладываем monkeypatch'ем самой
функции — так проверяется именно поведение middleware, а не запись в БД.
"""

from __future__ import annotations

BASE = "/api/server/v1"
HEALTH = f"{BASE}/health"
ADMIN_STATUS = f"{BASE}/admin/encryption/migration_status"
BUSINESS = f"{BASE}/servers"


def _patch_force(monkeypatch, *, active: bool, remaining: int = 500,
                 retry_after: int = 40, eta_seconds: float = 20.0) -> None:
    async def fake_state():
        return {
            "force_active": active,
            "remaining": remaining,
            "retry_after": retry_after,
            "eta_seconds": eta_seconds,
        }

    monkeypatch.setattr(
        "src.services.secrets_migration_service.force_gate_state_cached",
        fake_state,
    )


class TestGateInactive:
    async def test_business_endpoint_normal_when_force_off(
        self, client, monkeypatch
    ):
        _patch_force(monkeypatch, active=False)
        # Без токена бизнес-эндпоинт отдаёт 401 (обычный путь), не 503.
        resp = await client.get(BUSINESS)
        assert resp.status_code != 503
        assert resp.status_code == 401

    async def test_health_ok_when_force_off(self, client, monkeypatch):
        _patch_force(monkeypatch, active=False)
        resp = await client.get(HEALTH)
        assert resp.status_code == 200


class TestGateActive:
    async def test_business_endpoint_blocked_with_503(self, client, monkeypatch):
        _patch_force(monkeypatch, active=True, remaining=500, retry_after=40)
        resp = await client.get(BUSINESS)
        assert resp.status_code == 503
        assert resp.headers.get("Retry-After") == "40"
        body = resp.json()
        assert body["error_code"] == "REENCRYPT_IN_PROGRESS"
        assert body["details"]["retry_after"] == 40
        assert body["details"]["remaining"] == 500
        assert body["details"]["eta_seconds"] == 20.0

    async def test_blocked_response_carries_security_headers(self, client, monkeypatch):
        _patch_force(monkeypatch, active=True)
        resp = await client.get(BUSINESS)
        assert resp.status_code == 503
        # SecurityHeadersMiddleware — самый внешний слой, оборачивает и 503.
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"

    async def test_status_endpoint_exempt_under_force(
        self, client, monkeypatch, account_admin_token
    ):
        _patch_force(monkeypatch, active=True)
        resp = await client.get(
            ADMIN_STATUS, headers={"Authorization": f"Bearer {account_admin_token}"}
        )
        # Gate не трогает статус — доходит до endpoint'а и отдаёт 200.
        assert resp.status_code == 200, resp.text
        assert resp.status_code != 503

    async def test_health_exempt_under_force(self, client, monkeypatch):
        _patch_force(monkeypatch, active=True)
        resp = await client.get(HEALTH)
        assert resp.status_code == 200

    async def test_unauthenticated_business_gets_503_not_401(
        self, client, monkeypatch
    ):
        """Gate короткозамкнут ДО auth: 503 приходит раньше 401."""
        _patch_force(monkeypatch, active=True, retry_after=15)
        resp = await client.get(BUSINESS)
        assert resp.status_code == 503
        assert resp.headers.get("Retry-After") == "15"

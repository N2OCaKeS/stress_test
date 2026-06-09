"""`/health` и `/ready` — публичные endpoint'ы:

* отвечают 200 без авторизации;
* возвращают компактный JSON со статусом и именем сервиса;
* НЕ пишут audit-события (path в `_HEALTH_PATHS`).
"""

import pytest

HEALTH_URL = "/api/auth/v1/health"
READY_URL = "/api/auth/v1/ready"


@pytest.fixture()
def capture_audit_payloads(monkeypatch):
    """Перехватывает audit payloads через sync и async path emit'а."""
    captured: list[dict] = []

    def fake_sync_post(url, json, headers, timeout):
        captured.append(json)

    class _AsyncClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def post(self, url, json, headers):
            captured.append(json)
            class R: status_code = 201
            return R()

    monkeypatch.setattr("src.services.audit_service.httpx.post", fake_sync_post)
    monkeypatch.setattr("src.services.audit_service.httpx.AsyncClient", _AsyncClient)
    monkeypatch.setattr(
        "src.services.audit_service.get_settings",
        lambda: type("S", (), {
            "logging_service_url": "http://test",
            "logging_service_api_key": "k",
        })(),
    )
    return captured


# ── /health ──────────────────────────────────────────────────────────────────

class TestHealth:
    async def test_health_returns_200(self, client):
        resp = await client.get(HEALTH_URL)
        assert resp.status_code == 200

    async def test_health_body_shape(self, client):
        resp = await client.get(HEALTH_URL)
        assert resp.json() == {"status": "ok", "service": "auth_service"}

    async def test_health_does_not_require_authorization(self, client):
        """Без заголовка Authorization — всё равно 200."""
        resp = await client.get(HEALTH_URL)
        assert resp.status_code == 200

    async def test_health_ignores_bad_token(self, client):
        """Невалидный Bearer не должен ломать health (он публичный)."""
        resp = await client.get(HEALTH_URL, headers={"Authorization": "Bearer garbage"})
        assert resp.status_code == 200


# ── /ready ───────────────────────────────────────────────────────────────────

class TestReady:
    async def test_ready_returns_200(self, client):
        resp = await client.get(READY_URL)
        assert resp.status_code == 200

    async def test_ready_body_shape(self, client):
        resp = await client.get(READY_URL)
        body = resp.json()
        # После W2E `/ready` несёт operational counters; базовые поля остаются.
        assert body["status"] == "ready"
        assert body["service"] == "auth_service"
        assert "counters" in body and isinstance(body["counters"], dict)


# ── Audit skip ───────────────────────────────────────────────────────────────

class TestAuditSkip:
    async def test_health_does_not_emit_audit(self, client, capture_audit_payloads):
        await client.get(HEALTH_URL)
        for ev in capture_audit_payloads:
            assert ev["action"] not in {"http.success", "http.failure", "http.denied"}, (
                f"/health must be skipped from audit (path in _HEALTH_PATHS); got {ev}"
            )

    async def test_ready_does_not_emit_audit(self, client, capture_audit_payloads):
        await client.get(READY_URL)
        for ev in capture_audit_payloads:
            assert ev["action"] not in {"http.success", "http.failure", "http.denied"}

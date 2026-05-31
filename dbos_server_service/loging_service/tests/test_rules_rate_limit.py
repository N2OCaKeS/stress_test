"""GET /rules и GET /rules/{id} ограничены `audit_query_rate_limit`.

Симметрично GET /events: read-канал rules не должен открывать DoS-вектор
через `limit=1000` спам.
"""

from __future__ import annotations

from tests.conftest import make_rule

RULES_URL = "/api/logging/v1/rules"


class TestGetRulesRateLimit:
    def test_list_rules_burst_triggers_429(self, admin_client, monkeypatch):
        monkeypatch.setenv("AUDIT_QUERY_RATE_LIMIT", "2/minute")
        from src.core.config import get_settings

        get_settings.cache_clear()
        from src.main import limiter

        limiter.reset()

        for i in range(2):
            r = admin_client.get(RULES_URL)
            assert r.status_code == 200, f"request #{i} got {r.status_code}: {r.text}"

        r = admin_client.get(RULES_URL)
        assert r.status_code == 429, f"expected 429, got {r.status_code}: {r.text}"
        body = r.json()
        assert body["error_code"] == "RATE_LIMIT_EXCEEDED"

    def test_get_rule_burst_triggers_429(self, admin_client, monkeypatch):
        # Сначала создадим правило, чтобы было что GET'ать. Создание идёт
        # под другим limit-decorator'ом не висит — POST /rules не лимитируется.
        created = admin_client.post(RULES_URL, json=make_rule(name="rate-limited")).json()
        rule_id = created["id"]

        monkeypatch.setenv("AUDIT_QUERY_RATE_LIMIT", "2/minute")
        from src.core.config import get_settings

        get_settings.cache_clear()
        from src.main import limiter

        limiter.reset()

        for i in range(2):
            r = admin_client.get(f"{RULES_URL}/{rule_id}")
            assert r.status_code == 200, f"request #{i} got {r.status_code}: {r.text}"

        r = admin_client.get(f"{RULES_URL}/{rule_id}")
        assert r.status_code == 429, f"expected 429, got {r.status_code}: {r.text}"
        body = r.json()
        assert body["error_code"] == "RATE_LIMIT_EXCEEDED"

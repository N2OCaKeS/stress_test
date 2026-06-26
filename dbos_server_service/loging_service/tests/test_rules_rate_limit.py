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
        # Сначала создадим правило, чтобы было что GET'ать. Read-канал
        # (`AUDIT_QUERY_RATE_LIMIT`) и write-канал (`RULE_WRITE_RATE_LIMIT`) —
        # отдельные decorator'ы; одиночный POST в этот лимит не упирается.
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


class TestRuleWriteRateLimit:
    """POST/PATCH/DELETE /rules ограничены `RULE_WRITE_RATE_LIMIT`.

    Защита от flood'а скомпрометированным `loging_admin`-токеном: каждая
    write-операция сбрасывает rule-cache и пишет в БД rule + self-audit под
    одной транзакцией. Лимит per-user (fallback на IP).
    """

    def test_create_rule_burst_triggers_429(self, admin_client, monkeypatch):
        monkeypatch.setenv("RULE_WRITE_RATE_LIMIT", "2/minute")
        from src.core.config import get_settings

        get_settings.cache_clear()
        from src.main import limiter

        limiter.reset()

        for i in range(2):
            r = admin_client.post(RULES_URL, json=make_rule(name=f"w-{i}"))
            assert r.status_code == 201, f"request #{i} got {r.status_code}: {r.text}"

        r = admin_client.post(RULES_URL, json=make_rule(name="w-over"))
        assert r.status_code == 429, f"expected 429, got {r.status_code}: {r.text}"
        assert r.json()["error_code"] == "RATE_LIMIT_EXCEEDED"

    def test_patch_rule_burst_triggers_429(self, admin_client, monkeypatch):
        from src.core.config import get_settings
        from src.main import limiter

        # Setup-POST идёт под дефолтным write-лимитом, поэтому clear+reset ДО
        # него: иначе stale `get_settings`-кеш от соседнего теста мог бы
        # держать тесный лимит и зарубить сам create. Сразу после setup'а
        # ставим тесный лимит и reset'им счётчик — burst считается с нуля.
        get_settings.cache_clear()
        limiter.reset()
        created = admin_client.post(RULES_URL, json=make_rule(name="patch-target")).json()
        rule_id = created["id"]

        monkeypatch.setenv("RULE_WRITE_RATE_LIMIT", "2/minute")
        get_settings.cache_clear()
        limiter.reset()

        for i in range(2):
            r = admin_client.patch(f"{RULES_URL}/{rule_id}", json={"priority": 100 + i})
            assert r.status_code == 200, f"request #{i} got {r.status_code}: {r.text}"

        r = admin_client.patch(f"{RULES_URL}/{rule_id}", json={"priority": 300})
        assert r.status_code == 429, f"expected 429, got {r.status_code}: {r.text}"
        assert r.json()["error_code"] == "RATE_LIMIT_EXCEEDED"

    def test_delete_rule_burst_triggers_429(self, admin_client, monkeypatch):
        from src.core.config import get_settings
        from src.main import limiter

        # Burst по НЕсуществующему id, без единого setup-POST'а: limiter
        # считает запрос ДО выполнения хендлера, поэтому DELETE на отсутствующее
        # правило тоже инкрементит bucket. Так limiter видит ТОЛЬКО delete-burst,
        # никакая setup-запись к /rules не загрязняет ни счётчик, ни
        # `get_settings`-кеш. clear+reset идут НЕПОСРЕДСТВЕННО перед burst'ом.
        monkeypatch.setenv("RULE_WRITE_RATE_LIMIT", "2/minute")
        get_settings.cache_clear()
        limiter.reset()

        missing_id = "rl_doesnotexist"

        for i in range(2):
            r = admin_client.delete(f"{RULES_URL}/{missing_id}")
            assert r.status_code == 404, f"request #{i} got {r.status_code}: {r.text}"

        # Лимит исчерпан — следующие DELETE'ы отбиваются 429 ДО того, как
        # хендлер успеет вернуть 404.
        for i in range(2, 5):
            r = admin_client.delete(f"{RULES_URL}/{missing_id}")
            assert r.status_code == 429, (
                f"DELETE #{i} expected 429, got {r.status_code}: {r.text}"
            )
            assert r.json()["error_code"] == "RATE_LIMIT_EXCEEDED"

    def test_write_limit_separate_from_read_limit(self, admin_client, monkeypatch):
        # Read- и write-каналы считаются раздельно: исчерпанный read-bucket
        # не должен блокировать write (и наоборот). Тонкий write-лимит при
        # щедром read-лимите — GET проходит, POST упирается.
        monkeypatch.setenv("RULE_WRITE_RATE_LIMIT", "1/minute")
        monkeypatch.setenv("AUDIT_QUERY_RATE_LIMIT", "100/minute")
        from src.core.config import get_settings

        get_settings.cache_clear()
        from src.main import limiter

        limiter.reset()

        assert admin_client.post(RULES_URL, json=make_rule(name="sep-1")).status_code == 201
        # Read всё ещё открыт.
        assert admin_client.get(RULES_URL).status_code == 200
        # Второй write упёрся в свой отдельный bucket.
        r = admin_client.post(RULES_URL, json=make_rule(name="sep-2"))
        assert r.status_code == 429, f"expected 429, got {r.status_code}: {r.text}"

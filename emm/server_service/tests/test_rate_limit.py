"""Тесты для per-IP rate-limit middleware (slowloris защита часть b).

Проверяем:
* лимит работает: атакующий с одного IP, пробивший порог, получает 429
  с нашим стандартным error-envelope (request_id, error_code, timestamp);
* в пределах лимита всё проходит штатно (401 для невалидного токена, 200
  для валидного — НЕ 429);
* health-endpoints (`/health`, `/ready`) полностью исключены — k8s probe
  не должен попадать под slowapi;
* конфиг `global_rate_limit` корректно подхватывается из `settings`;
* exception-shape совместим с `AppException`-форматом, чтобы UI/clients
  обрабатывали 429 как и остальные ошибки.

Чтобы не палить 500+ HTTP-вызовов в каждом тесте, локально подменяем
лимит на малое значение через `LimitGroup` — это эквивалентно полному
прогону, но укладывается в секунды.
"""

from __future__ import annotations

import pytest
from slowapi.wrappers import LimitGroup

from src.core.config import get_settings
from src.main import app

HEALTH = "/api/server/v1/health"
READY = "/api/server/v1/ready"
PROTECTED = "/api/server/v1/servers"


from tests._helpers import assert_error, auth_hdr as _hdr  # noqa: E402


@pytest.fixture
def tight_limit():
    """Заменяет default-лимит на жёсткий (`<N>/minute`) для конкретного теста.

    Восстанавливает оригинальные лимиты после теста. Каждый тест получает
    свежий счётчик через autouse `_reset_rate_limiter` в conftest.
    """
    limiter = app.state.limiter
    original = list(limiter._default_limits)

    def _apply(limit_str: str) -> None:
        # `_default_limits` хранит список `LimitGroup`-объектов; в
        # `_check_request_limit` они разворачиваются через
        # `itertools.chain(*...)` → каждый LimitGroup итерируется и
        # производит конкретные `Limit`-инстансы (см. wrappers.py).
        # Подсовываем одну группу с нужной строкой лимита.
        new_group = LimitGroup(
            limit_provider=limit_str,
            key_function=limiter._key_func,
            scope=None,
            per_method=False,
            methods=None,
            error_message=None,
            exempt_when=None,
            cost=1,
            override_defaults=False,
        )
        limiter._default_limits = [new_group]
        limiter.reset()

    yield _apply

    limiter._default_limits = original
    limiter.reset()


# ── happy-path: лимит большой, всё проходит ─────────────────────────────────

class TestWithinLimit:
    """В пределах глобального лимита (по умолчанию 120/second) ничего не
    должно превращаться в 429."""

    async def test_no_token_returns_401_not_429(self, client):
        """Анонимный GET /servers — это 401 от auth-dep, не 429."""
        resp = await client.get(PROTECTED)
        assert_error(resp, 401, "ACCESS_TOKEN_MISSING")

    async def test_garbage_token_returns_401_not_429(self, client):
        """Trash-shape Bearer — отбивается bearer-shape pre-check'ом, 401."""
        resp = await client.get(PROTECTED, headers=_hdr("bogus_unregistered"))
        assert_error(resp, 401, "ACCESS_TOKEN_INVALID")

    async def test_burst_under_limit_all_pass(self, client):
        """50 быстрых запросов c одного IP < 500/minute → ни одного 429."""
        for _ in range(50):
            resp = await client.get(PROTECTED)
            assert resp.status_code != 429, f"unexpected 429 within limit: {resp.text}"


# ── 429: лимит пробит ────────────────────────────────────────────────────────

class TestRateLimitExceeded:
    """С одного IP сверх лимита → 429 с нашим стандартным envelope'ом."""

    async def test_429_after_exceeding_limit(self, client, tight_limit):
        """Подменяем лимит на 5/minute и убеждаемся что 6-й запрос — 429."""
        tight_limit("5/minute")

        # Первые 5 проходят (или возвращают 401 — но точно не 429).
        for i in range(5):
            resp = await client.get(PROTECTED)
            assert resp.status_code != 429, f"premature 429 на запросе #{i + 1}: {resp.text}"

        # 6-й — должен быть отбит.
        resp = await client.get(PROTECTED)
        assert_error(resp, 429, "RATE_LIMIT_EXCEEDED")

    async def test_429_response_shape_matches_app_envelope(self, client, tight_limit):
        """429-ответ должен быть в нашем edge-format'е (error_code/request_id/timestamp)."""
        tight_limit("2/minute")
        await client.get(PROTECTED)
        await client.get(PROTECTED)
        resp = await client.get(PROTECTED)
        body = assert_error(resp, 429, "RATE_LIMIT_EXCEEDED")

        assert body["error"] == "too_many_requests"
        assert "Rate limit exceeded" in body["message"]
        assert "timestamp" in body
        # request_id может быть None (rate-limit срабатывает до middleware,
        # выставляющего request_id) — главное, что ключ есть в envelope'е.
        assert "request_id" in body

    async def test_429_includes_retry_after_header(self, client, tight_limit):
        """k8s ingress / клиент должны видеть Retry-After для backoff."""
        tight_limit("1/minute")
        await client.get(PROTECTED)
        resp = await client.get(PROTECTED)
        assert_error(resp, 429, "RATE_LIMIT_EXCEEDED")
        assert resp.headers.get("Retry-After") == "60"

    async def test_500_plus_requests_one_ip_yields_429(self, client, tight_limit):
        """Сценарий из требования: 500+ запросов с одного IP → 429.

        Используем лимит 500/minute явно, чтобы тест документировал именно
        требуемое поведение на минутном окне, а не работу с искусственно
        низким лимитом.
        """
        tight_limit("500/minute")

        # 500 запросов проходят (любым статусом кроме 429).
        first_429_at = None
        for i in range(501):
            resp = await client.get(PROTECTED)
            if resp.status_code == 429:
                first_429_at = i + 1
                break

        assert first_429_at is not None, "не дождались 429 за 501 запрос"
        # Допускаем небольшую погрешность: главное — 429 произошёл около 500.
        # slowapi отбивает первый запрос ПОСЛЕ исчерпания (501-й).
        assert first_429_at >= 501, f"429 произошёл слишком рано: на запросе #{first_429_at}"


# ── health-endpoints не лимитируются ─────────────────────────────────────────

class TestHealthNotRateLimited:
    """`/health` и `/ready` не должны попадать под per-IP rate-limit —
    k8s liveness/readiness probe иначе словит 429 и под нагрузкой
    pod'ы пойдут в CrashLoopBackOff."""

    async def test_health_bypasses_tight_limit(self, client, tight_limit):
        """Лимит 1/minute, делаем 10 запросов на /health — все 200."""
        tight_limit("1/minute")

        for i in range(10):
            resp = await client.get(HEALTH)
            assert resp.status_code == 200, f"health #{i + 1}: {resp.status_code}: {resp.text}"
            assert resp.json()["status"] == "ok"

    async def test_ready_bypasses_tight_limit(self, client, tight_limit):
        """Лимит 1/minute, /ready также не должен ловить 429."""
        tight_limit("1/minute")

        for i in range(10):
            resp = await client.get(READY)
            assert resp.status_code == 200, f"ready #{i + 1}: {resp.status_code}: {resp.text}"
            assert resp.json()["status"] == "ready"

    async def test_health_traffic_does_not_consume_protected_quota(self, client, tight_limit):
        """Запросы в /health не должны учитываться в счётчике для /servers."""
        tight_limit("3/minute")

        # 20 health-вызовов не сжигают квоту protected-endpoint'а.
        for _ in range(20):
            assert (await client.get(HEALTH)).status_code == 200

        # Теперь 3 запроса в protected проходят (любым non-429 статусом).
        for _ in range(3):
            resp = await client.get(PROTECTED)
            assert resp.status_code != 429

        # 4-й — 429 (квота исчерпана).
        resp = await client.get(PROTECTED)
        assert_error(resp, 429, "RATE_LIMIT_EXCEEDED")


# ── 429 НЕ эмитит audit-event (фикс 429-audit-amplification) ────────────────


class TestRateLimitNoAuditAmplification:
    """`rate_limit_middleware` зарегистрирован OUTER к `audit_access`. 429-ответ
    должен возвращаться ДО `audit_access`, без `http.client_error` audit-emit'а.

    Регрессия: раньше декораторы стояли в обратном порядке и slowloris
    с одного IP заполнял audit-канал шумом
    `audit_service.emit("http.client_error", status="failure")` каждый
    запрос сверх лимита.
    """

    async def test_429_does_not_emit_audit(self, client, tight_limit, monkeypatch):
        """6-й запрос за 5/minute → 429, но `audit_service.emit` не вызвался."""
        captured: list[dict] = []

        def fake_emit(action, actor_id=None, **kwargs):
            captured.append({"action": action, **kwargs})

        # Главный модуль — патчим тут, плюс импорт в main (используется в middleware).
        import src.services.audit_service as audit_mod
        import src.main as main_mod
        monkeypatch.setattr(audit_mod, "emit", fake_emit)
        monkeypatch.setattr(main_mod.audit_service, "emit", fake_emit)

        tight_limit("5/minute")

        # Первые 5 проходят
        for _ in range(5):
            await client.get(PROTECTED)

        # Очищаем — нас интересуют только emit'ы, которые случились ПОСЛЕ 429.
        captured.clear()

        # 6-й — 429
        resp = await client.get(PROTECTED)
        assert_error(resp, 429, "RATE_LIMIT_EXCEEDED")

        # Никаких `http.*` audit-event'ов от 429-ответа.
        rate_limited_emits = [
            e for e in captured
            if e["action"] in (
                "http.access_denied",
                "http.client_error",
                "http.server_error",
            )
        ]
        assert rate_limited_emits == [], (
            f"429-ответ породил audit-emit (amplification): {rate_limited_emits}"
        )

    async def test_burst_of_429s_emits_zero_audit_events(
        self, client, tight_limit, monkeypatch,
    ):
        """100 запросов сверх лимита → 0 audit-event'ов про `http.*` для них.

        Это и есть «защита от slowloris через audit-канал»: атакующий не может
        раздуть audit-emission через 429-spam.
        """
        captured: list[dict] = []

        def fake_emit(action, actor_id=None, **kwargs):
            captured.append({"action": action, **kwargs})

        import src.services.audit_service as audit_mod
        import src.main as main_mod
        monkeypatch.setattr(audit_mod, "emit", fake_emit)
        monkeypatch.setattr(main_mod.audit_service, "emit", fake_emit)

        tight_limit("1/minute")
        await client.get(PROTECTED)  # съел квоту

        captured.clear()

        # 100 запросов — все 429
        for _ in range(100):
            resp = await client.get(PROTECTED)
            assert_error(resp, 429, "RATE_LIMIT_EXCEEDED")

        http_emits = [
            e for e in captured
            if e["action"] in (
                "http.access_denied",
                "http.client_error",
                "http.server_error",
            )
        ]
        assert http_emits == [], (
            f"burst of 100 × 429 породил {len(http_emits)} audit-emit'ов "
            f"(должно быть 0): {http_emits[:5]}"
        )


# ── конфиг ───────────────────────────────────────────────────────────────────

class TestRateLimitConfig:
    """`global_rate_limit` должен попадать из settings в limiter."""

    def test_default_is_120_per_second(self):
        """По умолчанию лимит — '120/second' (production-baseline)."""
        settings = get_settings()
        assert settings.global_rate_limit == "120/second"

    def test_limiter_uses_configured_limit(self):
        """Limiter получил именно `settings.global_rate_limit`."""
        limiter = app.state.limiter
        assert limiter is not None, "app.state.limiter не зарегистрирован"
        # default_limits — список LimitGroup'ов; проверяем что хотя бы один
        # парсится в наш лимит.
        configured = []
        for group in limiter._default_limits:
            for lim in group:
                configured.append(str(lim.limit))
        assert any("120" in s for s in configured), f"120/second not in default_limits: {configured}"

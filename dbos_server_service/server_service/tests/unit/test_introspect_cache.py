"""Unit-тесты для TTL-кэша introspect.

После введения ``platform_admin_guard`` middleware и endpoint dependency
``get_current_identity`` оба зовут ``_introspect(token)`` per request — +1
outbound roundtrip per non-public path (double introspect). На 500 RPS это
= 1000 introspect/sec.

Фикс: module-level TTL-кэш ``_identity_cache`` с TTL 5s в
``src/dependencies/auth.py``. Cache key — ``sha256(token).hexdigest()``
(не сам token в памяти процесса). Helper ``_get_or_cache_introspect(token)``
проверяет кэш → если miss / expired → fresh ``_introspect()`` →
кэширует positive (``active=True``) ответ.

Тесты ставят ``_identity_cache.clear()`` и monkeypatch'ат ``_introspect``
на счётчик, чтобы инспектировать сколько физически введений уходит в
auth_service при разных паттернах нагрузки.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from src.dependencies import auth as auth_dep


def _make_introspect_counter(response: dict, *, every_call: bool = True):
    """Фабрика fake_introspect с встроенным счётчиком вызовов.

    every_call=True: каждый вызов отдаёт одинаковый response.
    every_call=False: чередуем active=True / active=False для проверок expiry.
    """
    state = {"calls": 0}

    async def fake_introspect(token: str) -> dict:
        state["calls"] += 1
        return response

    return fake_introspect, state


# ── Базовое поведение: same token → 1 introspect, остальное из кэша ────────


@pytest.mark.asyncio
async def test_same_token_30_requests_one_introspect(monkeypatch):
    """30 introspect'ов на один токен → ровно 1 outbound call (29 из кэша)."""
    auth_dep._clear_introspect_cache()

    fake, state = _make_introspect_counter({
        "active": True,
        "sub": "usr_1",
        "username": "alice",
        "department_id": "dep_a",
        "platform_role": None,
        "service_roles": {"server_service": ["reader"]},
        "allowed_services": ["server_service"],
        "is_banned": False,
    })
    monkeypatch.setattr(auth_dep, "_introspect", fake)

    token = "dbos_pat_same_token_xyz_0001"
    for _ in range(30):
        body = await auth_dep._get_or_cache_introspect(token)
        assert body["active"] is True
        assert body["sub"] == "usr_1"

    # КЛЮЧЕВОЕ: в auth_service ушло только 1 introspect, остальные 29 —
    # cache hit. Это и есть закрытие double introspect amplification'а.
    assert state["calls"] == 1


# ── TTL expiry: после 6s — новый introspect ────────────────────────────────


@pytest.mark.asyncio
async def test_cache_expires_after_ttl(monkeypatch):
    """После истечения TTL (5s) → новый introspect-roundtrip.

    Не спим реальные 5s — `monkeypatch`'ом подменяем `time.monotonic()`,
    чтобы за миллисекунды проверить корректность expiry.
    """
    auth_dep._clear_introspect_cache()

    fake, state = _make_introspect_counter({
        "active": True,
        "sub": "usr_2",
        "username": "bob",
        "department_id": "dep_a",
        "platform_role": None,
        "service_roles": {"server_service": ["reader"]},
        "allowed_services": ["server_service"],
        "is_banned": False,
    })
    monkeypatch.setattr(auth_dep, "_introspect", fake)

    # Подменяем monotonic — кеш использует его для expiry-чека.
    fake_clock = {"now": 1000.0}
    monkeypatch.setattr(auth_dep.time, "monotonic", lambda: fake_clock["now"])

    token = "dbos_pat_ttl_test_xyz_002"

    # 1й вызов — miss, 1 introspect.
    await auth_dep._get_or_cache_introspect(token)
    assert state["calls"] == 1

    # +3 секунды — всё ещё в окне 5s → hit, без новых вызовов.
    fake_clock["now"] = 1003.0
    await auth_dep._get_or_cache_introspect(token)
    assert state["calls"] == 1

    # +6 секунд от исходной точки — TTL=5 истёк → новый introspect.
    fake_clock["now"] = 1006.0
    await auth_dep._get_or_cache_introspect(token)
    assert state["calls"] == 2

    # Следующий вызов сразу после — снова cache hit на новой записи.
    await auth_dep._get_or_cache_introspect(token)
    assert state["calls"] == 2


# ── Разные токены = разные cache slot'ы ─────────────────────────────────────


@pytest.mark.asyncio
async def test_different_tokens_different_cache_slots(monkeypatch):
    """Кэш по token-hash — разные токены не путаются между собой."""
    auth_dep._clear_introspect_cache()

    # Возвращаем разные body для разных токенов.
    state = {"calls_by_token": {}}

    async def fake_introspect(token: str) -> dict:
        state["calls_by_token"][token] = state["calls_by_token"].get(token, 0) + 1
        # Body имитирует ответ auth_service: разный user_id по token'у.
        return {
            "active": True,
            "sub": f"usr_{token[-3:]}",
            "username": f"user_{token[-3:]}",
            "department_id": "dep_a",
            "platform_role": None,
            "service_roles": {"server_service": ["reader"]},
            "allowed_services": ["server_service"],
            "is_banned": False,
        }

    monkeypatch.setattr(auth_dep, "_introspect", fake_introspect)

    tok_a = "dbos_pat_user_alice_xx_001"
    tok_b = "dbos_pat_user_bob_xxx_002"
    tok_c = "dbos_pat_user_carol_xx_003"

    # 5 introspect'ов tok_a + 3 tok_b + 1 tok_c → 3 уникальных physical-call'а.
    for _ in range(5):
        body = await auth_dep._get_or_cache_introspect(tok_a)
        assert body["sub"] == "usr_001"
    for _ in range(3):
        body = await auth_dep._get_or_cache_introspect(tok_b)
        assert body["sub"] == "usr_002"
    body = await auth_dep._get_or_cache_introspect(tok_c)
    assert body["sub"] == "usr_003"

    assert state["calls_by_token"][tok_a] == 1
    assert state["calls_by_token"][tok_b] == 1
    assert state["calls_by_token"][tok_c] == 1


# ── Middleware + endpoint dep — общий cache slot для одного токена ──────────


@pytest.mark.asyncio
async def test_middleware_then_endpoint_dep_one_introspect(monkeypatch):
    """Платформенный паттерн: middleware дёргает introspect, endpoint dep — второй.

    Раньше это был **double introspect** — 2 roundtrip'а на 1 request.
    Теперь оба вызова идут через `_get_or_cache_introspect` → второй
    cache hit. Закрытие amplification'а на любом non-public path.
    """
    auth_dep._clear_introspect_cache()

    fake, state = _make_introspect_counter({
        "active": True,
        "sub": "usr_pipeline",
        "username": "pipeline_user",
        "department_id": "dep_a",
        "platform_role": None,
        "service_roles": {"server_service": ["operator"]},
        "allowed_services": ["server_service"],
        "is_banned": False,
    })
    monkeypatch.setattr(auth_dep, "_introspect", fake)

    token = "dbos_pat_pipeline_token_xx12345"

    # Симулируем middleware: первый вызов
    middleware_body = await auth_dep._get_or_cache_introspect(token)
    # Симулируем endpoint dep: второй вызов с тем же токеном
    endpoint_body = await auth_dep._get_or_cache_introspect(token)

    # Оба видят одно и то же body
    assert middleware_body == endpoint_body
    # Но в auth_service физически ушёл ровно 1 introspect, не 2.
    assert state["calls"] == 1


# ── Negative responses (active=False) НЕ кэшируются ────────────────────────


@pytest.mark.asyncio
async def test_negative_response_not_cached(monkeypatch):
    """Banned/expired tokens (`active=False`) НЕ кэшируются.

    Это важно для hot-revoke: если ban'нули токен в auth_service, мы не
    хотим, чтобы он жил в cache на 5 секунд. Negative roundtrip дешёвый
    (auth_service видит проверку быстро), кэшировать его — лишь
    маскировать ошибку.
    """
    auth_dep._clear_introspect_cache()

    fake, state = _make_introspect_counter({"active": False})
    monkeypatch.setattr(auth_dep, "_introspect", fake)

    token = "dbos_pat_banned_token_xy_001"

    # 3 проверки → 3 introspect'а, потому что negative не кэшируется.
    for _ in range(3):
        body = await auth_dep._get_or_cache_introspect(token)
        assert body["active"] is False
    assert state["calls"] == 3


# ── token-hash invariant: токены с одинаковыми "хвостами" не сталкиваются ──


def test_token_cache_key_uses_sha256():
    """Cache key — SHA-256 hex digest, не сам токен.

    Это значит: plaintext bearer НЕ хранится в `_identity_cache` (защита
    от heap-dump утечки при coredump процесса).
    """
    tok = "dbos_pat_secret_xxxx12345"
    key = auth_dep._token_cache_key(tok)
    # Cache key никогда не должен содержать исходный токен.
    assert tok not in key
    # SHA-256 hex = 64 символа.
    assert len(key) == 64
    # Same token → same key (детерминизм).
    assert auth_dep._token_cache_key(tok) == key
    # Different token → different key.
    assert auth_dep._token_cache_key(tok + "x") != key


# ── Clear helper ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clear_cache_helper(monkeypatch):
    """`_clear_introspect_cache()` сбрасывает все записи."""
    auth_dep._clear_introspect_cache()

    fake, state = _make_introspect_counter({
        "active": True,
        "sub": "usr_clear",
        "username": "clear",
        "department_id": "dep_a",
        "platform_role": None,
        "service_roles": {"server_service": ["reader"]},
        "allowed_services": ["server_service"],
        "is_banned": False,
    })
    monkeypatch.setattr(auth_dep, "_introspect", fake)

    token = "dbos_pat_clear_test_xyz12345"

    await auth_dep._get_or_cache_introspect(token)
    assert state["calls"] == 1

    # Cache hit
    await auth_dep._get_or_cache_introspect(token)
    assert state["calls"] == 1

    # Сбрасываем кэш — следующий вызов снова miss.
    auth_dep._clear_introspect_cache()
    await auth_dep._get_or_cache_introspect(token)
    assert state["calls"] == 2


# ── Исключения _introspect не кэшируются ────────────────────────────────────


@pytest.mark.asyncio
async def test_introspect_exception_not_cached(monkeypatch):
    """Если `_introspect` бросает исключение — кэш не апдейтится.

    Иначе сетевой сбой на одну итерацию навсегда «закэшировал» бы
    отсутствие данных под этим token-hash'ем.
    """
    auth_dep._clear_introspect_cache()

    state = {"calls": 0}

    async def flaky_introspect(token: str) -> dict:
        state["calls"] += 1
        if state["calls"] == 1:
            raise RuntimeError("network glitch")
        return {
            "active": True,
            "sub": "usr_flaky",
            "username": "flaky",
            "department_id": "dep_a",
            "platform_role": None,
            "service_roles": {"server_service": ["reader"]},
            "allowed_services": ["server_service"],
            "is_banned": False,
        }

    monkeypatch.setattr(auth_dep, "_introspect", flaky_introspect)

    token = "dbos_pat_flaky_token_xy_001"

    with pytest.raises(RuntimeError, match="network glitch"):
        await auth_dep._get_or_cache_introspect(token)

    # Кэш пустой — следующий вызов снова идёт в auth_service и теперь успешен.
    body = await auth_dep._get_or_cache_introspect(token)
    assert body["active"] is True
    assert state["calls"] == 2

"""Юнит-тесты server_service: cleanup-кластер.

Покрывают:

1. `audit_service._send_to_logging_service` уважает числовой
   `Retry-After` на 429.
2. Sync-path `_send_sync` тоже ретраит 429 и инкрементит drop counter.
3. `_rate_limit_exceeded_response` ставит `X-RateLimit-*` headers.
4. `dispatch_task` / `dispatch_task_with_hit` — два явных метода.
5. `redaction._SECRET_KEYS` маскирует `ssh_public_key`.
6. `redaction.redact()` больше не принимает `_parent_key`.
7. `_generate_strong_password` ограничен `_STRONG_PWD_MAX_ATTEMPTS`.
8. `password_policy._POLICY_MESSAGE` содержит реальные числа из констант.
9. `installed_packages` payload содержит `max_rows` cap.
10. `ipmi_controller.get_controller` эмитит view-success ПОСЛЕ reveal'а.
11. `server_account.get_account` симметрично — view-success после reveal'а.
"""
from __future__ import annotations

import inspect

import httpx
import pytest

from src.core import password_policy
from src.services import audit_service
from src.services import redaction
from src.services import server_account as sa_svc


# ── 1. audit retry: Retry-After header ──────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_counter():
    audit_service._reset_dropped_429_for_tests()
    yield
    audit_service._reset_dropped_429_for_tests()


class TestRetryAfterParsing:
    def test_int_seconds_parsed(self):
        assert audit_service._parse_retry_after_seconds("5") == 5.0

    def test_float_seconds_parsed(self):
        assert audit_service._parse_retry_after_seconds("1.5") == 1.5

    def test_negative_returns_none(self):
        assert audit_service._parse_retry_after_seconds("-3") is None

    def test_http_date_returns_none(self):
        # RFC 7231 HTTP-date — не поддерживаем
        assert (
            audit_service._parse_retry_after_seconds("Wed, 21 Oct 2015 07:28:00 GMT")
            is None
        )

    def test_empty_returns_none(self):
        assert audit_service._parse_retry_after_seconds("") is None
        assert audit_service._parse_retry_after_seconds(None) is None

    def test_capped_at_30(self):
        assert audit_service._parse_retry_after_seconds("999") == 30.0


@pytest.mark.asyncio
async def test_retry_after_overrides_smaller_backoff(monkeypatch):
    """429 с `Retry-After: 5` → sleep'ы получают `max(backoff, 5)` сек."""
    statuses = iter([429, 429, 201])

    def handler(request: httpx.Request) -> httpx.Response:
        code = next(statuses)
        if code == 201:
            return httpx.Response(201, json={"accepted": True})
        return httpx.Response(429, headers={"Retry-After": "5"})

    pooled = httpx.AsyncClient(
        base_url="http://loging-mock",
        transport=httpx.MockTransport(handler),
        timeout=2.0,
    )
    monkeypatch.setattr(audit_service, "_audit_client", pooled)

    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(audit_service.asyncio, "sleep", fake_sleep)

    try:
        await audit_service._send_to_logging_service(
            {"action": "server.power_on"}, "http://loging-mock", "k",
        )
    finally:
        await pooled.aclose()

    # Базовые backoff'ы ~0.5 и 1.5 — оба меньше 5, должны быть подняты.
    assert len(sleeps) == 2
    assert all(d >= 5.0 for d in sleeps)


@pytest.mark.asyncio
async def test_invalid_retry_after_ignored(monkeypatch):
    """Поломанный `Retry-After: not-a-number` не влияет на backoff."""
    statuses = iter([429, 201])

    def handler(request: httpx.Request) -> httpx.Response:
        code = next(statuses)
        if code == 201:
            return httpx.Response(201)
        return httpx.Response(429, headers={"Retry-After": "garbage"})

    pooled = httpx.AsyncClient(
        base_url="http://loging-mock",
        transport=httpx.MockTransport(handler),
        timeout=2.0,
    )
    monkeypatch.setattr(audit_service, "_audit_client", pooled)

    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(audit_service.asyncio, "sleep", fake_sleep)

    try:
        await audit_service._send_to_logging_service(
            {"action": "server.power_on"}, "http://loging-mock", "k",
        )
    finally:
        await pooled.aclose()

    # Без поломанного header'а: первый backoff 0.5 ± 0.2 jitter.
    assert len(sleeps) == 1
    assert sleeps[0] < 1.5


# ── 2. sync-path retry on 429 ────────────────────────────────────────────────


def test_sync_path_retries_on_429(monkeypatch):
    """Sync-путь ретраит 429 → успешный 201."""
    calls = {"n": 0}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(201)

    monkeypatch.setattr(audit_service.httpx, "post", fake_post)

    slept: list[float] = []

    def fake_sleep(d):
        slept.append(d)

    import time
    monkeypatch.setattr(time, "sleep", fake_sleep)

    audit_service._send_sync({"action": "server.x"}, "http://lm", "k")

    assert calls["n"] == 3
    assert audit_service.get_dropped_429_total() == 0
    assert len(slept) == 2


def test_sync_path_drops_after_three_429(monkeypatch):
    """3x 429 в sync-пути → counter +1, не виснет."""
    calls = {"n": 0}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls["n"] += 1
        return httpx.Response(429)

    monkeypatch.setattr(audit_service.httpx, "post", fake_post)

    import time
    monkeypatch.setattr(time, "sleep", lambda _d: None)

    audit_service._send_sync({"action": "server.x"}, "http://lm", "k")

    assert calls["n"] == 3
    assert audit_service.get_dropped_429_total() == 1


def test_sync_path_success_no_retry(monkeypatch):
    """201 сразу — без retry, counter не двигается."""
    calls = {"n": 0}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls["n"] += 1
        return httpx.Response(201)

    monkeypatch.setattr(audit_service.httpx, "post", fake_post)

    import time
    monkeypatch.setattr(time, "sleep", lambda _d: None)

    audit_service._send_sync({"action": "server.x"}, "http://lm", "k")

    assert calls["n"] == 1
    assert audit_service.get_dropped_429_total() == 0


def test_sync_path_caps_retry_after(monkeypatch):
    """Sync-путь сапит даже большой Retry-After ≤1s — shutdown не вис."""
    calls = {"n": 0}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "60"})
        return httpx.Response(201)

    monkeypatch.setattr(audit_service.httpx, "post", fake_post)

    slept: list[float] = []

    import time
    monkeypatch.setattr(time, "sleep", lambda d: slept.append(d))

    audit_service._send_sync({"action": "server.x"}, "http://lm", "k")

    assert slept == [1.0]
    assert calls["n"] == 2


# ── 3. endpoint_limiter / rate-limit 429 headers ────────────────────────────


def test_rate_limit_exceeded_response_includes_headers():
    from src.main import _rate_limit_exceeded_response

    class _Req:
        class _State:
            request_id = "req_test"
        state = _State()

    class _Exc:
        detail = "10/minute"

    resp = _rate_limit_exceeded_response(_Req(), _Exc())
    assert resp.status_code == 429
    assert resp.headers["Retry-After"] == "60"
    assert resp.headers["X-RateLimit-Limit"] == "10/minute"
    assert resp.headers["X-RateLimit-Remaining"] == "0"
    reset = int(resp.headers["X-RateLimit-Reset"])
    # Should be in the near future.
    import time as _t
    now = int(_t.time())
    assert now <= reset <= now + 120


# ── 4. worker_client split ───────────────────────────────────────────────────


@pytest.fixture
def _wc_dispatch_stubs(monkeypatch):
    """Минимальный набор патчей для запуска `dispatch_task[_with_hit]` без БД.

    Три точки, которые иначе ходят в Postgres: idempotency lookup, task-row
    insert и outbox-row insert. Возвращает модуль `worker_client` для
    использования в самом тесте.
    """
    from src.services import worker_client as wc

    async def fake_lookup(_k):
        return None

    async def fake_insert(**_k):
        pass

    async def fake_outbox_insert(_db, **_kw):
        return None

    monkeypatch.setattr(wc, "_get_task_by_idempotency_key", fake_lookup)
    monkeypatch.setattr(wc, "_insert_task_row", fake_insert)
    monkeypatch.setattr(
        "src.services.worker_client.dispatch_outbox_repo.insert",
        fake_outbox_insert,
    )
    return wc


class TestDispatchTaskSplit:
    def test_dispatch_task_signature(self):
        from src.services import worker_client as wc

        sig = inspect.signature(wc.dispatch_task)
        assert "return_hit" not in sig.parameters, (
            "dispatch_task no longer takes return_hit — use dispatch_task_with_hit"
        )

    @pytest.mark.asyncio
    async def test_dispatch_task_with_hit_accepts_all_dispatch_kwargs(
        self, _wc_dispatch_stubs,
    ):
        """Behavioral mirror контракта: dispatch_task_with_hit принимает тот
        же набор kwargs, что и dispatch_task (минус `return_hit`), и
        возвращает `(task_id, hit_bool)`.
        """
        from unittest.mock import AsyncMock

        wc = _wc_dispatch_stubs
        result = await wc.dispatch_task_with_hit(
            db=AsyncMock(),
            task_kind="power.on",
            target_server_id="srv_x",
            target_resource_id="res_x",
            payload={"k": "v"},
            created_by="usr_x",
            request_id="req_1",
            idempotency_key=None,
        )
        assert isinstance(result, tuple)
        task_id, hit = result
        assert isinstance(task_id, str) and task_id.startswith("tsk_")
        assert isinstance(hit, bool)

    @pytest.mark.asyncio
    async def test_dispatch_task_returns_str_only(self, _wc_dispatch_stubs):
        from unittest.mock import AsyncMock

        wc = _wc_dispatch_stubs
        result = await wc.dispatch_task(
            db=AsyncMock(),
            task_kind="power.on",
            target_server_id="srv_x",
            payload={},
            created_by="usr_x",
            request_id="req_1",
        )
        assert isinstance(result, str)
        assert result.startswith("tsk_")

    @pytest.mark.asyncio
    async def test_dispatch_task_with_hit_returns_tuple(self, _wc_dispatch_stubs):
        from unittest.mock import AsyncMock

        wc = _wc_dispatch_stubs
        task_id, hit = await wc.dispatch_task_with_hit(
            db=AsyncMock(),
            task_kind="power.on",
            target_server_id="srv_x",
            payload={},
            created_by="usr_x",
            request_id="req_1",
        )
        assert task_id.startswith("tsk_")
        assert hit is False


# ── 5. redaction.ssh_public_key ──────────────────────────────────────────────


def test_ssh_public_key_in_secret_keys():
    assert "ssh_public_key" in redaction._SECRET_KEYS


def test_ssh_public_key_masked_in_payload():
    payload = {
        "actor": "ops",
        "ssh_public_key": "ssh-ed25519 AAAA... user@host",
    }
    out = redaction.redact(payload)
    assert out["ssh_public_key"] == "<SECRET>"
    assert out["actor"] == "ops"


# ── 6. redaction._parent_key removed ─────────────────────────────────────────


def test_redact_does_not_accept_parent_key():
    sig = inspect.signature(redaction.redact)
    assert "_parent_key" not in sig.parameters


# ── 7. _generate_strong_password bounded ─────────────────────────────────────


def test_generate_strong_password_has_max_attempts_cap():
    # Constant exists и используется.
    assert hasattr(sa_svc, "_STRONG_PWD_MAX_ATTEMPTS")
    assert isinstance(sa_svc._STRONG_PWD_MAX_ATTEMPTS, int)
    assert sa_svc._STRONG_PWD_MAX_ATTEMPTS > 0

    # Источник не должен содержать `while True`.
    src = inspect.getsource(sa_svc._generate_strong_password)
    assert "while True" not in src


def test_generate_strong_password_raises_when_exhausted(monkeypatch):
    """При сломанном источнике (только буквы) cap всё-таки сработает."""
    monkeypatch.setattr(sa_svc, "_STRONG_PWD_MAX_ATTEMPTS", 5)
    # Алфавит без цифр/символов → has_digit / has_symbol всегда False.
    monkeypatch.setattr(sa_svc, "_STRONG_PWD_SYMBOLS", "")
    # secrets.choice сам ничего не вернёт нужного при alphabet=letters only
    import secrets as _s
    real_choice = _s.choice

    def only_letters(_seq):
        # Возвращает только буквы из ascii_letters
        import string as _st
        return real_choice(_st.ascii_letters)

    monkeypatch.setattr(sa_svc.secrets, "choice", only_letters)
    with pytest.raises(RuntimeError, match="could not generate password"):
        sa_svc._generate_strong_password()


def test_generate_strong_password_normal_path():
    """В обычных условиях возвращает пароль с буквой / цифрой / символом."""
    pwd = sa_svc._generate_strong_password()
    assert len(pwd) == sa_svc._STRONG_PWD_LENGTH
    assert any(ch.isalpha() for ch in pwd)
    assert any(ch.isdigit() for ch in pwd)
    assert any(ch in sa_svc._STRONG_PWD_SYMBOLS for ch in pwd)


# ── 8. password_policy message uses real constants ───────────────────────────


def test_policy_messages_use_constants():
    assert str(password_policy.MIN_PASSWORD_LENGTH) in password_policy._POLICY_MESSAGE
    assert (
        str(password_policy.MIN_STRONG_PASSWORD_LENGTH)
        in password_policy._STRONG_POLICY_MESSAGE
    )


# ── 9. installed_packages max_rows cap ───────────────────────────────────────


def test_installed_packages_module_has_max_rows():
    from src.api.v1.endpoints import installed_packages as ip

    assert hasattr(ip, "_MAX_INSTALLED_PACKAGES_ROWS")
    assert isinstance(ip._MAX_INSTALLED_PACKAGES_ROWS, int)
    assert ip._MAX_INSTALLED_PACKAGES_ROWS >= 1000


def test_installed_packages_handler_passes_max_rows(monkeypatch):
    """Endpoint кладёт max_rows в worker payload."""
    from src.api.v1.endpoints import installed_packages as ip

    captured: dict = {}

    async def fake_dispatch(**kwargs):
        captured.update(kwargs)
        return ("tsk_fake", False)

    monkeypatch.setattr(ip.worker_client, "dispatch_task_with_hit", fake_dispatch)

    # Чтобы не дёргать БД и permissions, подменим всё вокруг.
    # is_managed=True — resolve_inventory_account_id уходит по key-based ветке
    # (возвращает None сразу), не дёргая account_repo/БД; на проверку max_rows
    # это не влияет.
    class _Server:
        hostname = "h"
        ssh_port = 22
        department_id = "dep_a"
        status = "active"
        is_managed = True
        management_user = "dbos_sys"

    async def fake_get_server(_db, _identity, _id):
        return _Server()

    async def fake_require(_db, _ident, _ent, _act):
        return None

    monkeypatch.setattr(ip.server_svc, "get_server", fake_get_server)
    monkeypatch.setattr(ip.permissions, "require_action", fake_require)
    monkeypatch.setattr(ip, "audit_service", type("S", (), {"emit": lambda *a, **k: None})())

    # Skip decommissioned-gate с ServerStatus.DECOMMISSIONED — у нас "active".
    class _Req:
        class _State:
            request_id = "req_1"
        state = _State()
        headers = {}

    import asyncio as _aio

    class _Identity:
        user_id = "usr_x"
        department_id = "dep_a"

    from unittest.mock import AsyncMock

    _aio.run(
        ip.list_installed_packages(
            server_id="srv_1",
            identity=_Identity(),
            request=_Req(),
            pattern="*",
            db=AsyncMock(),
        )
    )

    assert captured["payload"]["max_rows"] == ip._MAX_INSTALLED_PACKAGES_ROWS


# ── 10. ipmi_controller view-success после reveal'а ─────────────────────────


@pytest.fixture
def _ipmi_view_stubs(monkeypatch):
    """Подменяет permissions/load/repo для `ipmi_controller.get_controller`.

    Возвращает `(ipmi_svc, captured)` — модуль и список перехваченных audit-
    эмиссий. Сам `_reveal_controller_password` остаётся на caller'е: одни
    тесты бросают DECRYPT_FAILED, другие возвращают plaintext.
    """
    from src.services import ipmi_controller as ipmi_svc

    captured: list[dict] = []

    def fake_emit(action, actor_id=None, **kw):
        captured.append({"action": action, **kw})

    class _Server:
        id = "srv_1"
        department_id = "dep_a"

    class _Ctrl:
        id = "ipm_1"
        server_id = "srv_1"
        password_encrypted = "v1$ct"

    async def fake_has_action(*a, **k):
        return True

    async def fake_require(*a, **k):
        return None

    async def fake_load(*a, **k):
        return _Server()

    async def fake_repo_get(*a, **k):
        return _Ctrl()

    monkeypatch.setattr(ipmi_svc.audit_service, "emit", fake_emit)
    monkeypatch.setattr(ipmi_svc.permissions, "has_action", fake_has_action)
    monkeypatch.setattr(ipmi_svc.permissions, "require_action", fake_require)
    monkeypatch.setattr(ipmi_svc, "load_visible_server", fake_load)
    monkeypatch.setattr(ipmi_svc.repo, "get_by_server_id", fake_repo_get)
    return ipmi_svc, captured


@pytest.mark.asyncio
async def test_ipmi_get_controller_no_success_if_reveal_fails(
    monkeypatch, _ipmi_view_stubs,
):
    """Если reveal падает DECRYPT_FAILED — view-success НЕ эмитится."""
    from src.core.exceptions import AppException

    ipmi_svc, captured = _ipmi_view_stubs

    async def boom_reveal(_db, _obj, _dep):
        raise AppException(
            error_code="DECRYPT_FAILED",
            message="bad tag",
            http_status=500,
        )

    monkeypatch.setattr(ipmi_svc, "_reveal_controller_password", boom_reveal)

    with pytest.raises(AppException):
        await ipmi_svc.get_controller(db=None, identity=None, server_id="srv_1")

    success = [e for e in captured if e.get("action") == "ipmi_controller.view"
               and e.get("status") == "success"]
    assert success == [], (
        "view-success не должен эмититься, если reveal упал DECRYPT_FAILED"
    )


@pytest.mark.asyncio
async def test_ipmi_get_controller_success_after_reveal_ok(
    monkeypatch, _ipmi_view_stubs,
):
    """Reveal прошёл → view-success эмитится один раз."""
    ipmi_svc, captured = _ipmi_view_stubs

    async def fake_reveal(_db, _obj, _dep):
        return "decoded-base64"

    monkeypatch.setattr(
        ipmi_svc, "_reveal_controller_password", fake_reveal,
    )

    obj, revealed = await ipmi_svc.get_controller(
        db=None, identity=None, server_id="srv_1",
    )
    assert revealed == "decoded-base64"
    success = [e for e in captured if e.get("action") == "ipmi_controller.view"
               and e.get("status") == "success"]
    assert len(success) == 1


# ── 11. server_account get_account симметрично ───────────────────────────────


@pytest.fixture
def _sa_view_stubs(monkeypatch):
    """Параллель `_ipmi_view_stubs` для `server_account.get_account`.

    Подменяет permissions + `_load_account_visible` + emit-capture; reveal
    остаётся на caller'е.
    """
    captured: list[dict] = []

    def fake_emit(action, actor_id=None, **kw):
        captured.append({"action": action, **kw})

    class _Acc:
        id = "acc_1"
        login = "ops"
        department_id = "dep_a"
        password_encrypted = "v1$ct"

    async def fake_has_action(*a, **k):
        return True

    async def fake_require(*a, **k):
        return None

    async def fake_load(_db, _ident, _id):
        return _Acc()

    monkeypatch.setattr(sa_svc.audit_service, "emit", fake_emit)
    monkeypatch.setattr(sa_svc.permissions, "has_action", fake_has_action)
    monkeypatch.setattr(sa_svc.permissions, "require_action", fake_require)
    monkeypatch.setattr(sa_svc, "_load_account_visible", fake_load)
    return captured


@pytest.mark.asyncio
async def test_server_account_get_no_success_if_reveal_fails(
    monkeypatch, _sa_view_stubs,
):
    from src.core.exceptions import AppException

    captured = _sa_view_stubs

    async def boom_reveal(_db, _acc):
        raise AppException(
            error_code="DECRYPT_FAILED",
            message="bad tag",
            http_status=500,
        )

    monkeypatch.setattr(sa_svc, "_reveal_account_password", boom_reveal)

    class _Identity:
        department_id = "dep_a"

    with pytest.raises(AppException):
        await sa_svc.get_account(db=None, identity=_Identity(), account_id="acc_1")

    success = [
        e for e in captured
        if e.get("action") == "server_account.view" and e.get("status") == "success"
    ]
    assert success == []

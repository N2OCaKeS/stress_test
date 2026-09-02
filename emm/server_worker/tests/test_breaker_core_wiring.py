"""Регрессы и верификация cleanup-фиксов worker'а.

В основном — verify-проверки уже закрытых пунктов (PEM-regex,
identifier validators, STASH-константы, PublishResult NamedTuple,
DASH-P regex, IPv6 в extract_bmc_host). Плюс:

* `_breaker_core` — extracted shared Python-обвязка для bmc и audit
  breaker'ов; здесь покрываем happy-path через FakeRedis и проверяем
  что keys/log_prefix/thresholds правильно проброшены.
* `_mask_password_in_argv` — redundant `i + 4 < len(safe)` снят;
  поведение не изменилось.

Не дублирует test_validators_docstrings_and_breaker_state.py /
test_redaction_and_identifier_validators.py по тем же проверкам.
"""

from __future__ import annotations

import logging
import re
from unittest.mock import AsyncMock

import pytest

from src.clients.ipmitool import _mask_password_in_argv
from src.core.constants import SCRUBBED_SENTINEL, STASH_TTL_SECONDS
from src.core.identifiers import validate_outbox_id, validate_task_id
from src.services import (
    _breaker_core,
    _breaker_lua,
    audit_publisher_breaker as audit_cb,
    bmc_circuit_breaker as bmc_cb,
)
from src.services._breaker_core import Thresholds
from src.services.audit_outbox_publisher import PublishResult
from src.tasks._bmc_helpers import extract_bmc_host
from src.utils.redaction import redact_error_message


# ────────────────────────────── PEM redact (verify) ─────────────────────────


class TestPEMRedaction:
    """`-----BEGIN ... -----END-----` → `<PRIVATE_KEY>`."""

    def test_openssh_private_key_redacted(self) -> None:
        msg = (
            "auth failed: -----BEGIN OPENSSH PRIVATE KEY-----\n"
            "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAA\n"
            "-----END OPENSSH PRIVATE KEY-----\nthen 401"
        )
        out = redact_error_message(msg)
        assert "<PRIVATE_KEY>" in out
        assert "BEGIN" not in out
        assert "b3BlbnNzaC1rZXkt" not in out

    def test_rsa_private_key_redacted(self) -> None:
        msg = "ssh err: -----BEGIN RSA PRIVATE KEY-----\nABCDEFG\n-----END RSA PRIVATE KEY-----"
        out = redact_error_message(msg)
        assert out.count("<PRIVATE_KEY>") == 1
        assert "ABCDEFG" not in out


# ─────────────────────── identifiers validators (verify) ─────────────────────


class TestIdentifierValidators:
    """`validate_task_id` / `validate_outbox_id` блокируют traversal."""

    @pytest.mark.parametrize("good", ["tsk_x", "tsk_" + "a" * 32, "abcDEF_-09"])
    def test_task_id_valid(self, good: str) -> None:
        assert validate_task_id(good) == good

    @pytest.mark.parametrize(
        "bad",
        [
            "tsk:colon", "../admin", "tsk_with spaces", "tsk_with/slash",
            "tsk_with.dot", "", "a" * 65,
        ],
    )
    def test_task_id_rejects(self, bad: str) -> None:
        with pytest.raises(ValueError):
            validate_task_id(bad)

    def test_task_id_non_str(self) -> None:
        with pytest.raises(ValueError):
            validate_task_id(None)  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "bad", ["rox/admin", "rox..", "rox with space", "rox:colon"],
    )
    def test_outbox_id_rejects(self, bad: str) -> None:
        with pytest.raises(ValueError):
            validate_outbox_id(bad)

    def test_outbox_id_valid(self) -> None:
        assert validate_outbox_id("rox_abc123") == "rox_abc123"


# ──────────────────────────── constants module ───────────────────────────────


class TestConstants:
    """STASH_TTL_SECONDS и SCRUBBED_SENTINEL — единый источник истины."""

    def test_stash_ttl_seconds_value(self) -> None:
        assert STASH_TTL_SECONDS == 1800

    def test_scrubbed_sentinel_value(self) -> None:
        assert SCRUBBED_SENTINEL == "<scrubbed>"

    def test_passwords_module_uses_constant(self) -> None:
        """`tasks/passwords` импортирует константу, литералов 1800 не осталось."""
        from src.tasks import passwords
        src = passwords.__file__
        with open(src, encoding="utf-8") as fh:
            body = fh.read()
        # Литералы 1800 — допустимы только в комментариях; считаем строки кода.
        non_comment_lines = [
            ln for ln in body.splitlines()
            if "1800" in ln and not ln.lstrip().startswith("#")
        ]
        # docstrings/строки могут содержать упоминание — фильтр по `= 1800`.
        offenders = [ln for ln in non_comment_lines if re.search(r"=\s*1800\b", ln)]
        assert not offenders, f"hard-coded 1800 ttl: {offenders}"


# ──────────────────────────── PublishResult tuple ────────────────────────────


class TestPublishResultNamedTuple:
    """PublishResult — NamedTuple, четыре поля."""

    def test_fields_are_named(self) -> None:
        r = PublishResult(closed=True, audit_emit_error=False, was_published=True)
        assert r.closed is True
        assert r.audit_emit_error is False
        assert r.was_published is True
        assert r.breaker_skipped is False  # default

    def test_breaker_skipped_optional(self) -> None:
        r = PublishResult(
            closed=True, audit_emit_error=False, was_published=False,
            breaker_skipped=True,
        )
        assert r.breaker_skipped is True

    def test_field_order_back_compat(self) -> None:
        # Old positional callers — должны по-прежнему работать.
        r = PublishResult(True, False, True)
        assert r.closed and not r.audit_emit_error and r.was_published


# ───────────────────────── DASH_P regex (verify) ─────────────────────────────


class TestDashPRedact:
    """`_DASH_P_RE` не ловит `-Path /foo`, ловит `-P pass`."""

    def test_path_powershell_not_redacted(self) -> None:
        out = redact_error_message("Get-Item -Path /foo/bar failed")
        assert "/foo/bar" in out

    def test_dash_p_space_redacted(self) -> None:
        out = redact_error_message("ipmitool -P plaintext lan print")
        assert "plaintext" not in out
        assert "<PASSWORD>" in out

    def test_dash_p_equal_redacted(self) -> None:
        out = redact_error_message("cmd -P=hunter2 next")
        assert "hunter2" not in out

    def test_dash_p_joined_nonletter_redacted(self) -> None:
        # `-P!secret` — слитное, после `-P` идёт `!`, не буква.
        out = redact_error_message("ipmitool -P!secret123 lan")
        assert "secret123" not in out


# ────────────────────── extract_bmc_host (IPv6 verify) ───────────────────────


class TestExtractBmcHost:
    """`_extract_bmc_host` ловит IPv6 в квадратных скобках и сохраняет порт."""

    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://10.0.0.1", "10.0.0.1"),
            ("https://10.0.0.1:443", "10.0.0.1:443"),
            ("10.0.0.1:623", "10.0.0.1:623"),
            ("10.0.0.1", "10.0.0.1"),
            ("https://[2001:db8::1]:443", "[2001:db8::1]:443"),
            ("[::1]:623", "[::1]:623"),
            ("https://user:pass@10.0.0.1:443", "10.0.0.1:443"),
            ("https://user:pass@[::1]:443", "[::1]:443"),
            ("", ""),
        ],
    )
    def test_extract(self, url: str, expected: str) -> None:
        assert extract_bmc_host(url) == expected


# ────────────────────── _mask_password_in_argv ───────────────────────────────


class TestMaskPasswordInArgv:
    """Redundant `i + 4 < len(safe)` снят; поведение сохранено."""

    def test_dash_p_masked(self) -> None:
        out = _mask_password_in_argv(["ipmitool", "-P", "pass", "lan", "print"])
        assert "pass" not in out
        assert out[2] == "***"

    def test_user_set_password_masked(self) -> None:
        out = _mask_password_in_argv(
            ["ipmitool", "user", "set", "password", "2", "newpass"],
        )
        assert "newpass" not in out
        assert out[-1] == "***"

    def test_user_set_password_too_short_no_crash(self) -> None:
        # Меньше 5 элементов: range(len-4) = range(<=0) → пустой цикл.
        for argv in [
            ["user", "set", "password"],
            ["user", "set", "password", "2"],
            ["ipmitool"],
            [],
        ]:
            assert _mask_password_in_argv(argv) == argv

    def test_user_set_password_exact_5_argv(self) -> None:
        # Граница: ровно 5 элементов — `range(len-4) = range(1)` → i=0.
        out = _mask_password_in_argv(["user", "set", "password", "2", "x"])
        assert out[4] == "***"

    def test_user_set_password_not_matched_when_prefix_differs(self) -> None:
        out = _mask_password_in_argv(["raw", "set", "password", "2", "x"])
        assert out == ["raw", "set", "password", "2", "x"]


# ──────────────────────────── _breaker_core wiring ───────────────────────────


class _FakeRedisStore:
    """Минимальный FakeRedis для core-тестов; не реализует Lua-семантику.

    Возвращает заранее заданные значения по script-id; считает вызовы
    aclose. Достаточно для проверки, что core правильно прокидывает
    keys/argv и парсит результат.
    """

    def __init__(self, eval_result):
        self._eval_result = eval_result
        self.last_eval = None
        self.aclose_count = 0
        self.delete_calls: list[tuple] = []

    async def eval(self, script, n, *args):
        self.last_eval = (script, n, args)
        return self._eval_result

    async def delete(self, *keys):
        self.delete_calls.append(keys)
        return len(keys)

    async def aclose(self):
        self.aclose_count += 1


@pytest.mark.asyncio
class TestBreakerCore:
    """Контракт `_breaker_core.eval_*` — единый канал для bmc/audit."""

    async def test_eval_check_closed(self, caplog) -> None:
        fake = _FakeRedisStore([b"closed", 0])

        async def factory():
            return fake

        state, retry = await _breaker_core.eval_check(
            client_factory=factory,
            keys=("k:f", "k:s", "k:o", "k:p"),
            cooldown_seconds=30,
            log_prefix="test",
            logger=logging.getLogger("test_breaker_core"),
            now=1_000_000.0,
        )
        assert state == "closed"
        assert retry == 0.0
        assert fake.aclose_count == 1
        # Lua вызван с правильным числом ключей и argv (now, cooldown).
        script, n, args = fake.last_eval
        assert script == _breaker_lua.CHECK_SCRIPT
        assert n == 4
        assert args[:4] == ("k:f", "k:s", "k:o", "k:p")
        assert args[4] == "1000000"  # now
        assert args[5] == "30"  # cooldown

    async def test_eval_check_open(self) -> None:
        fake = _FakeRedisStore(["open", 17])

        async def factory():
            return fake

        state, retry = await _breaker_core.eval_check(
            client_factory=factory,
            keys=("k:f", "k:s", "k:o", "k:p"),
            cooldown_seconds=30,
            log_prefix="test",
            logger=logging.getLogger("test_breaker_core"),
            now=1_000_000.0,
        )
        assert state == "open"
        assert retry == 17.0

    async def test_eval_check_redis_error_fail_open(self, caplog) -> None:
        from redis.exceptions import RedisError

        class Boom:
            async def eval(self, *a, **kw):
                raise RedisError("redis down")

            async def aclose(self):
                pass

        async def factory():
            return Boom()

        caplog.set_level(logging.WARNING)
        state, retry = await _breaker_core.eval_check(
            client_factory=factory,
            keys=("k:f", "k:s", "k:o", "k:p"),
            cooldown_seconds=30,
            log_prefix="testprefix",
            logger=logging.getLogger("test_breaker_core"),
            now=1_000_000.0,
        )
        assert state == "closed"
        assert retry == 0.0
        assert any("testprefix" in rec.message for rec in caplog.records)

    async def test_eval_record_failure_opens(self) -> None:
        fake = _FakeRedisStore([b"open", 5])

        async def factory():
            return fake

        result = await _breaker_core.eval_record_failure(
            client_factory=factory,
            keys=("k:f", "k:s", "k:o", "k:p"),
            thresholds=Thresholds(
                failure_threshold=5, window_seconds=60, cooldown_seconds=30,
            ),
            log_prefix="test",
            logger=logging.getLogger("test_breaker_core"),
            now=1_700_000_000.0,
        )
        assert result == ("open", 5)
        # Lua argv: now, threshold, window, cooldown
        _script, _n, args = fake.last_eval
        assert args[4:] == ("1700000000", "5", "60", "30")

    async def test_eval_record_failure_redis_error_returns_none(self) -> None:
        from redis.exceptions import RedisError

        class Boom:
            async def eval(self, *a, **kw):
                raise RedisError("oops")

            async def aclose(self):
                pass

        async def factory():
            return Boom()

        result = await _breaker_core.eval_record_failure(
            client_factory=factory,
            keys=("k:f", "k:s", "k:o", "k:p"),
            thresholds=Thresholds(5, 60, 30),
            log_prefix="t",
            logger=logging.getLogger("test_breaker_core"),
        )
        assert result is None

    async def test_eval_record_success_calls_lua(self) -> None:
        fake = _FakeRedisStore(1)

        async def factory():
            return fake

        await _breaker_core.eval_record_success(
            client_factory=factory,
            keys=("k:f", "k:s", "k:o", "k:p"),
            log_prefix="t",
            logger=logging.getLogger("test_breaker_core"),
        )
        script, _n, _args = fake.last_eval
        assert script == _breaker_lua.RECORD_SUCCESS_SCRIPT

    async def test_eval_reset_calls_delete(self) -> None:
        fake = _FakeRedisStore(None)

        async def factory():
            return fake

        await _breaker_core.eval_reset(
            client_factory=factory,
            keys=("k:f", "k:s", "k:o", "k:p"),
            log_prefix="t",
            logger=logging.getLogger("test_breaker_core"),
        )
        assert fake.delete_calls == [("k:f", "k:s", "k:o", "k:p")]


@pytest.mark.asyncio
class TestBreakerWrappersDelegateToCore:
    """`bmc_circuit_breaker.check` и `audit_publisher_breaker.check`
    проходят через `_breaker_core` (proof — патчим `eval_check`)."""

    async def test_bmc_check_calls_core(self, monkeypatch) -> None:
        called = {}

        async def fake_eval_check(**kwargs):
            called["keys"] = kwargs["keys"]
            called["log_prefix"] = kwargs["log_prefix"]
            return ("closed", 0.0)

        monkeypatch.setattr(_breaker_core, "eval_check", fake_eval_check)
        await bmc_cb.check("10.0.0.1")
        # Префиксы из core: failures/state/open_until/probe.
        assert called["keys"] == (
            "cb:bmc:10.0.0.1:failures",
            "cb:bmc:10.0.0.1:state",
            "cb:bmc:10.0.0.1:open_until",
            "cb:bmc:10.0.0.1:probe",
        )
        assert "10.0.0.1" in called["log_prefix"]

    async def test_audit_check_calls_core(self, monkeypatch) -> None:
        called = {}

        async def fake_eval_check(**kwargs):
            called["keys"] = kwargs["keys"]
            return ("closed", 0.0)

        monkeypatch.setattr(_breaker_core, "eval_check", fake_eval_check)
        await audit_cb.check()
        assert called["keys"] == (
            "cb:audit_publisher:failures",
            "cb:audit_publisher:state",
            "cb:audit_publisher:open_until",
            "cb:audit_publisher:probe",
        )

    async def test_bmc_check_empty_host_short_circuit(self, monkeypatch) -> None:
        """`check('')` не идёт в core (раннее return)."""
        evict = AsyncMock()
        monkeypatch.setattr(_breaker_core, "eval_check", evict)
        await bmc_cb.check("")
        evict.assert_not_awaited()

    async def test_bmc_check_raises_when_open(self, monkeypatch) -> None:
        async def fake_eval_check(**kwargs):
            return ("open", 17.0)

        monkeypatch.setattr(_breaker_core, "eval_check", fake_eval_check)
        with pytest.raises(bmc_cb.CircuitBreakerOpenError) as ei:
            await bmc_cb.check("host42")
        assert ei.value.details["host"] == "host42"
        assert ei.value.details["retry_after_seconds"] == 17

    async def test_audit_check_raises_when_open(self, monkeypatch) -> None:
        async def fake_eval_check(**kwargs):
            return ("open", 4.0)

        monkeypatch.setattr(_breaker_core, "eval_check", fake_eval_check)
        with pytest.raises(audit_cb.CircuitBreakerOpenError):
            await audit_cb.check()

    async def test_audit_get_state_returns_tuple(self, monkeypatch) -> None:
        async def fake_eval_get_state(**kwargs):
            return ("open", 5.0)

        monkeypatch.setattr(_breaker_core, "eval_get_state", fake_eval_get_state)
        state, retry = await audit_cb.get_state()
        assert state == "open"
        assert retry == 5.0

    async def test_audit_reset_delegates(self, monkeypatch) -> None:
        called = {}

        async def fake_eval_reset(**kwargs):
            called["keys"] = kwargs["keys"]

        monkeypatch.setattr(_breaker_core, "eval_reset", fake_eval_reset)
        await audit_cb.reset()
        assert called["keys"][:3] == (
            "cb:audit_publisher:failures",
            "cb:audit_publisher:state",
            "cb:audit_publisher:open_until",
        )


@pytest.mark.asyncio
class TestBreakerWrappersStillCompatibleWithFakeRedis:
    """Test-helper `install_fake_redis` (`tests/unit/_breaker_test_helpers.py`)
    патчит `_get_client` каждого breaker-модуля — после refactor паттерн
    должен продолжать работать.
    """

    async def test_install_fake_redis_pattern_bmc(self, monkeypatch) -> None:
        from tests.unit._breaker_test_helpers import (
            FakeRedis,
            frozen_clock_fixture,
            install_fake_redis,
        )

        install_fake_redis(monkeypatch, bmc_cb)
        clock = frozen_clock_fixture(monkeypatch, bmc_cb)
        clock["now"] = 1_700_000_000.0

        # closed → check() возвращает молча.
        await bmc_cb.check("10.1.1.1")
        # record_failure × threshold-1 — closed, последний → open.
        for _ in range(4):
            await bmc_cb.record_failure("10.1.1.1")
        await bmc_cb.record_failure("10.1.1.1")
        # Теперь breaker open — check() raises.
        with pytest.raises(bmc_cb.CircuitBreakerOpenError):
            await bmc_cb.check("10.1.1.1")
        FakeRedis.reset_store()

    async def test_install_fake_redis_pattern_audit(self, monkeypatch) -> None:
        from tests.unit._breaker_test_helpers import (
            FakeRedis,
            frozen_clock_fixture,
            install_fake_redis,
        )

        install_fake_redis(monkeypatch, audit_cb)
        clock = frozen_clock_fixture(monkeypatch, audit_cb)
        clock["now"] = 1_700_000_000.0

        await audit_cb.check()  # closed
        for _ in range(5):
            await audit_cb.record_failure()
        with pytest.raises(audit_cb.CircuitBreakerOpenError):
            await audit_cb.check()
        FakeRedis.reset_store()

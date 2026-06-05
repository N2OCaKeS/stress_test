"""Unit-тесты generic-helpers из `src/services/_lockout.py`.

Покрывают `release_principal_if_expired` / `assert_principal_not_locked` /
`register_principal_failure` — те самые duck-typed функции, которыми пользуются
OAuth/Bot lockout-пути и которые до сих пор тестировались только косвенно
через end-to-end. Здесь — прямые проверки на in-memory принципале + fake-репо,
с акцентом на кастомных аргументах (`reset_attr`, `set_locked_method`,
`counter_attr`, `increment_method`).

БД не нужна — helper'ы по контракту не дёргают её, всё через duck-type.
"""

from datetime import timedelta, timezone

import pytest

from src.core.exceptions import AuthorizationError
from src.services import _lockout
from src.utils.time import utcnow


class _FakePrincipal:
    """Имитирует OAuth-client / Bot-row: только нужные поля."""

    def __init__(self, *, counter: int = 0, locked_until=None):
        self.failed_custom_attempts = counter
        self.locked_until = locked_until


class _FakeRepo:
    """Принимает любые имена методов, записывает вызовы для assertion'ов."""

    def __init__(self):
        self.calls: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name):
        async def _method(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return None
        return _method

    async def custom_reset(self, principal):
        self.calls.append(("custom_reset", (principal,), {}))
        principal.failed_custom_attempts = 0
        principal.locked_until = None

    async def custom_increment(self, principal):
        self.calls.append(("custom_increment", (principal,), {}))
        principal.failed_custom_attempts += 1

    async def custom_set_locked(self, principal, locked_until):
        self.calls.append(("custom_set_locked", (principal, locked_until), {}))
        principal.locked_until = locked_until


# ── release_principal_if_expired ──────────────────────────────────────────────


class TestReleasePrincipalIfExpired:
    """`release_principal_if_expired` сбрасывает счётчик через кастомный
    `reset_attr` — это и есть smoke-test для duck-type аргумента."""

    @pytest.mark.asyncio
    async def test_resets_via_custom_attr_when_expired(self):
        repo = _FakeRepo()
        principal = _FakePrincipal(
            counter=5,
            locked_until=utcnow() - timedelta(minutes=10),
        )

        released = await _lockout.release_principal_if_expired(
            repo, principal, reset_attr="custom_reset",
        )

        assert released is True
        # Hit ровно по `custom_reset`, не по дефолтному `reset_failed_attempts`.
        names = [c[0] for c in repo.calls]
        assert "custom_reset" in names
        assert "reset_failed_attempts" not in names
        assert principal.failed_custom_attempts == 0
        assert principal.locked_until is None

    @pytest.mark.asyncio
    async def test_noop_when_not_locked(self):
        repo = _FakeRepo()
        principal = _FakePrincipal(counter=2, locked_until=None)

        released = await _lockout.release_principal_if_expired(
            repo, principal, reset_attr="custom_reset",
        )

        assert released is False
        assert repo.calls == []

    @pytest.mark.asyncio
    async def test_noop_when_lockout_still_active(self):
        repo = _FakeRepo()
        principal = _FakePrincipal(
            counter=5,
            locked_until=utcnow() + timedelta(minutes=5),
        )

        released = await _lockout.release_principal_if_expired(
            repo, principal, reset_attr="custom_reset",
        )

        assert released is False
        assert repo.calls == []


# ── assert_principal_not_locked ───────────────────────────────────────────────


class TestAssertPrincipalNotLocked:
    def test_raises_with_retry_after_seconds(self):
        principal = _FakePrincipal(
            counter=5,
            locked_until=utcnow() + timedelta(seconds=300),
        )

        with pytest.raises(AuthorizationError) as excinfo:
            _lockout.assert_principal_not_locked(principal)

        err = excinfo.value
        assert err.error_code == "ACCOUNT_TEMPORARILY_LOCKED"
        assert err.http_status == 429
        retry = err.details.get("retry_after_seconds")
        # Окно ~300s; допускаем небольшой джиттер.
        assert 280 <= retry <= 305, retry

    def test_does_not_raise_when_locked_until_naive_but_future(self):
        """Naive datetime → нормализуем как UTC и сравниваем — не теряем
        символ tz-info, не падаем с TypeError."""
        naive_future = (utcnow() + timedelta(minutes=2)).replace(tzinfo=None)
        principal = _FakePrincipal(counter=5, locked_until=naive_future)

        with pytest.raises(AuthorizationError):
            _lockout.assert_principal_not_locked(principal)

    def test_noop_when_locked_until_none(self):
        principal = _FakePrincipal(counter=0, locked_until=None)
        _lockout.assert_principal_not_locked(principal)

    def test_noop_when_locked_until_already_expired(self):
        principal = _FakePrincipal(
            counter=5,
            locked_until=utcnow() - timedelta(seconds=1),
        )
        _lockout.assert_principal_not_locked(principal)


# ── register_principal_failure ────────────────────────────────────────────────


class TestRegisterPrincipalFailure:
    """Проверяем кастомные `counter_attr` / `increment_method` /
    `set_locked_method` — основная фишка обобщённого helper'а."""

    @pytest.mark.asyncio
    async def test_increment_below_threshold_no_lockout(self):
        repo = _FakeRepo()
        principal = _FakePrincipal(counter=2)

        await _lockout.register_principal_failure(
            repo,
            principal,
            counter_attr="failed_custom_attempts",
            increment_method="custom_increment",
            set_locked_method="custom_set_locked",
            max_attempts=5,
            lockout_minutes=15,
        )

        names = [c[0] for c in repo.calls]
        assert "custom_increment" in names
        # Порог не перешагнут — set_locked не зовётся.
        assert "custom_set_locked" not in names
        assert principal.locked_until is None

    @pytest.mark.asyncio
    async def test_increment_hits_threshold_sets_lockout_via_custom_setter(self):
        repo = _FakeRepo()
        principal = _FakePrincipal(counter=4)

        before = utcnow()
        await _lockout.register_principal_failure(
            repo,
            principal,
            counter_attr="failed_custom_attempts",
            increment_method="custom_increment",
            set_locked_method="custom_set_locked",
            max_attempts=5,
            lockout_minutes=15,
        )

        names = [c[0] for c in repo.calls]
        # Сначала инкремент, потом set_locked — порядок важен (lockout
        # выставляется после того, как новый счётчик уже >= max_attempts).
        assert names == ["custom_increment", "custom_set_locked"]
        assert principal.failed_custom_attempts == 5
        # locked_until примерно `now + 15m`.
        assert principal.locked_until is not None
        delta = principal.locked_until - before
        assert timedelta(minutes=14) <= delta <= timedelta(minutes=16)

    @pytest.mark.asyncio
    async def test_uses_default_set_locked_method_when_not_overridden(self):
        """Если `set_locked_method` не передан — должен быть дефолтный
        `set_locked_until`. Фиксируем контракт явно."""
        repo = _FakeRepo()
        principal = _FakePrincipal(counter=4)

        await _lockout.register_principal_failure(
            repo,
            principal,
            counter_attr="failed_custom_attempts",
            increment_method="custom_increment",
            max_attempts=5,
            lockout_minutes=10,
        )

        names = [c[0] for c in repo.calls]
        # __getattr__ перехватит дефолтное имя — конкретное значение в
        # principal.locked_until здесь не пишется, мы только проверяем, что
        # вызов прошёл по дефолтному имени метода.
        assert "set_locked_until" in names

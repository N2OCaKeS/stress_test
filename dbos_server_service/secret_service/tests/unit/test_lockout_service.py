"""Тесты in-memory lockout-сервиса secret_service.

Окно/порог/duration берутся из Settings; в тестах подменяем settings через
monkeypatch get_settings, чтобы не ждать 15 минут реального lockout'а.
"""

from __future__ import annotations

import time

import pytest

from src.services import lockout_service


class _FakeSettings:
    def __init__(
        self, *, threshold: int = 3, window: int = 60, duration: int = 30
    ) -> None:
        self.lockout_threshold = threshold
        self.lockout_window_seconds = window
        self.lockout_duration_seconds = duration


@pytest.fixture(autouse=True)
def _reset():
    lockout_service._reset_for_tests()
    yield
    lockout_service._reset_for_tests()


def _patch_settings(monkeypatch, **kw) -> None:
    fake = _FakeSettings(**kw)
    monkeypatch.setattr(lockout_service, "get_settings", lambda: fake)


# ── basics ────────────────────────────────────────────────────────────────


def test_initially_not_locked(monkeypatch) -> None:
    _patch_settings(monkeypatch)
    assert not lockout_service.is_locked("usr_a")


def test_increment_below_threshold(monkeypatch) -> None:
    _patch_settings(monkeypatch, threshold=3)
    assert not lockout_service.record_denied("usr_a")
    assert not lockout_service.record_denied("usr_a")
    assert not lockout_service.is_locked("usr_a")


def test_threshold_triggers_lock(monkeypatch) -> None:
    _patch_settings(monkeypatch, threshold=3)
    lockout_service.record_denied("usr_a")
    lockout_service.record_denied("usr_a")
    locked_now = lockout_service.record_denied("usr_a")
    assert locked_now is True
    assert lockout_service.is_locked("usr_a")


def test_lock_isolated_per_user(monkeypatch) -> None:
    _patch_settings(monkeypatch, threshold=2)
    lockout_service.record_denied("usr_a")
    lockout_service.record_denied("usr_a")
    assert lockout_service.is_locked("usr_a")
    assert not lockout_service.is_locked("usr_b")


def test_clear_resets_state(monkeypatch) -> None:
    _patch_settings(monkeypatch, threshold=2)
    lockout_service.record_denied("usr_a")
    lockout_service.record_denied("usr_a")
    assert lockout_service.is_locked("usr_a")
    lockout_service.clear("usr_a")
    assert not lockout_service.is_locked("usr_a")


def test_clear_unknown_user_noop(monkeypatch) -> None:
    _patch_settings(monkeypatch)
    lockout_service.clear("usr_unknown")  # should not raise


def test_retry_after_zero_when_not_locked(monkeypatch) -> None:
    _patch_settings(monkeypatch)
    assert lockout_service.retry_after_seconds("usr_a") == 0


def test_retry_after_positive_when_locked(monkeypatch) -> None:
    _patch_settings(monkeypatch, threshold=1, duration=120)
    lockout_service.record_denied("usr_a")
    retry = lockout_service.retry_after_seconds("usr_a")
    assert retry > 0
    assert retry <= 120


# ── window expiry ─────────────────────────────────────────────────────────


def test_window_expires_failures(monkeypatch) -> None:
    """Старые failures вне окна не учитываются в счётчике."""
    _patch_settings(monkeypatch, threshold=3, window=60)
    # Подменяем monotonic — добавляем "старые" failures в state напрямую.
    state = lockout_service._state.setdefault(
        "usr_a", lockout_service._UserState()
    )
    now = time.monotonic()
    # Две old failures (старше окна).
    state.failures = [now - 120, now - 90]
    # Новая failure — должна оставить только её, не поднять lock.
    locked = lockout_service.record_denied("usr_a")
    assert not locked
    assert len(state.failures) == 1


def test_lock_expires(monkeypatch) -> None:
    """Истёкший lockout автоматически снимается на is_locked."""
    _patch_settings(monkeypatch, threshold=1, duration=1)
    lockout_service.record_denied("usr_a")
    assert lockout_service.is_locked("usr_a")
    # Перепишем locked_until в прошлое — имитируем истёкший lockout.
    lockout_service._state["usr_a"].locked_until = time.monotonic() - 1
    assert not lockout_service.is_locked("usr_a")


def test_record_after_expired_lock_starts_fresh(monkeypatch) -> None:
    """После истечения lockout'а счётчик failures сбрасывается."""
    _patch_settings(monkeypatch, threshold=2, duration=1)
    lockout_service.record_denied("usr_a")
    lockout_service.record_denied("usr_a")
    assert lockout_service.is_locked("usr_a")
    # Имитируем истёкший lockout.
    lockout_service._state["usr_a"].locked_until = time.monotonic() - 1
    # Новый record_denied должен сбросить и начать с чистого листа.
    locked = lockout_service.record_denied("usr_a")
    assert locked is False
    assert len(lockout_service._state["usr_a"].failures) == 1

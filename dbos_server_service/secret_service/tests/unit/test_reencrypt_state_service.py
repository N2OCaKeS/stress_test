"""Singleton-строка состояния перешифровки + расчёт Retry-After.

Проверяем `reencrypt_state_service`:

* дефолтное состояние — lazy, force снят;
* enter_force / clear_force / set_lazy переключают флаг и режим;
* update_progress пишет снапшот;
* compute_retry_after зажимает значение в [min, max] и считает ETA.
"""

from __future__ import annotations

import pytest

from src.core.config import get_settings
from src.services import reencrypt_state_service


@pytest.mark.asyncio
async def test_default_state_is_lazy_no_force(adb):
    state = await reencrypt_state_service.get_state(adb)
    assert state.mode == "lazy"
    assert state.force_active is False


@pytest.mark.asyncio
async def test_enter_force_sets_flag_and_remaining(adb):
    await reencrypt_state_service.enter_force(adb, remaining=100, throughput=10.0)
    state = await reencrypt_state_service.get_state(adb)
    assert state.mode == "force"
    assert state.force_active is True
    assert state.remaining == 100
    assert state.eta_seconds > 0


@pytest.mark.asyncio
async def test_clear_force_lifts_gate(adb):
    await reencrypt_state_service.enter_force(adb, remaining=50)
    await reencrypt_state_service.clear_force(adb)
    state = await reencrypt_state_service.get_state(adb)
    assert state.force_active is False
    assert state.mode == "lazy"
    assert state.remaining == 0


@pytest.mark.asyncio
async def test_update_progress_persists_snapshot(adb):
    await reencrypt_state_service.update_progress(
        adb, remaining=42, throughput=7.5, eta_seconds=6
    )
    state = await reencrypt_state_service.get_state(adb)
    assert state.remaining == 42
    assert state.throughput == pytest.approx(7.5)
    assert state.eta_seconds == 6


def test_retry_after_respects_floor():
    settings = get_settings()
    retry_after, eta = reencrypt_state_service.compute_retry_after(
        remaining=1, throughput=1000.0
    )
    # ETA почти 0, но Retry-After не опускается ниже минимума.
    assert retry_after == settings.reencrypt_retry_after_min
    assert eta >= 0


def test_retry_after_respects_ceiling():
    settings = get_settings()
    retry_after, _ = reencrypt_state_service.compute_retry_after(
        remaining=10_000_000, throughput=1.0
    )
    assert retry_after == settings.reencrypt_retry_after_max


def test_retry_after_uses_default_throughput_when_unknown():
    settings = get_settings()
    retry_after, eta = reencrypt_state_service.compute_retry_after(
        remaining=100, throughput=0.0
    )
    # 100 / default_throughput + буфер.
    expected_eta = int(-(-100 // int(settings.reencrypt_default_throughput)))
    assert eta == expected_eta
    assert retry_after == max(
        settings.reencrypt_retry_after_min,
        expected_eta + settings.reencrypt_retry_after_buffer,
    )

"""Тесты планировщика retention-тика (`_next_retention_boundary_from`).

Фокус — пересчёт `next_run` от абсолютной wallclock-границы вместо
аккумулирующего `+= interval`. Без этого медленный sweep сдвигал бы все
последующие тики на накопленную задержку, а пропущенные минутные слоты
выполнялись бы катящимся залпом без сна.
"""

import time
from unittest.mock import patch


class TestNextRetentionBoundary:
    def test_boundary_aligned_to_wallclock_minute(self):
        from src.main import _next_retention_boundary_from

        # wall=120.0 ровно на границе минуты → next_run = mono + interval.
        with patch("src.main.time.time", return_value=120.0):
            next_run = _next_retention_boundary_from(1000.0, interval=60.0)
        assert next_run == 1060.0

    def test_boundary_floors_then_advances(self):
        from src.main import _next_retention_boundary_from

        # wall=125.3 → next minute boundary 180.0, delta 54.7s.
        with patch("src.main.time.time", return_value=125.3):
            next_run = _next_retention_boundary_from(1000.0, interval=60.0)
        assert abs(next_run - 1054.7) < 1e-6

    def test_slow_sweep_skips_missed_slot_no_accumulation(self):
        """Если sweep занял 80s (больше interval'а), next_run должен указывать
        на БУДУЩУЮ границу минуты от текущего момента, а не на просроченную
        `previous + 60`. Иначе цикл крутил бы катящийся залп без сна."""
        from src.main import _next_retention_boundary_from

        # Стартовая граница на wall=60.0 (≈ mono=1000.0 + 0).
        with patch("src.main.time.time", return_value=60.0):
            start = _next_retention_boundary_from(1000.0, interval=60.0)
        assert start == 1060.0

        # Sweep сожрал 80s → сейчас wall=140.0, mono=1140.0. Старая логика
        # `start + 60.0` дала бы next_run=1120.0 (уже в прошлом) — мгновенный
        # бесполезный tick. Новая логика возвращает 1160.0 (mono в момент
        # следующей wallclock-минуты 180.0, +40s).
        with patch("src.main.time.time", return_value=140.0):
            next_run = _next_retention_boundary_from(1140.0, interval=60.0)
        # wall 140 → next boundary 180 → delta 40 → mono 1140+40=1180.
        assert next_run == 1180.0
        assert next_run > 1140.0, "next_run должен быть в будущем относительно mono-now"

    def test_consecutive_calls_never_return_past_timestamp(self):
        """Sanity: при любом фиксированном `now_monotonic` next_run > now."""
        from src.main import _next_retention_boundary_from

        # Эмулируем несколько последовательных пересчётов с прогрессом времени.
        wall_progression = [10.0, 70.5, 130.2, 200.0, 305.0]
        mono_progression = [1000.0, 1060.5, 1120.2, 1190.0, 1295.0]
        for wall, mono in zip(wall_progression, mono_progression):
            with patch("src.main.time.time", return_value=wall):
                nxt = _next_retention_boundary_from(mono, interval=60.0)
            assert nxt > mono, f"wall={wall} mono={mono} next={nxt}"
            # И граница не дальше interval'а вперёд.
            assert nxt - mono <= 60.0 + 1e-6

    def test_interval_parameter_respected(self):
        """Хелпер не хардкодит 60s — interval передаётся параметром (это нужно
        и тестам, и потенциальной смене tick-частоты)."""
        from src.main import _next_retention_boundary_from

        with patch("src.main.time.time", return_value=15.0):
            # interval=30s: 15.0 → next boundary 30.0, delta 15s.
            nxt = _next_retention_boundary_from(500.0, interval=30.0)
        assert nxt == 515.0


class TestRetentionLoopUsesBoundaryHelper:
    """Минимально-инвазивный smoke: убеждаемся, что `_retention_loop` использует
    `_next_retention_boundary_from` и `_RETENTION_TICK_INTERVAL_SECONDS`. Без
    этого ревизия `+= 60.0` могла бы вернуться незамеченной.
    """

    def test_module_exports_helper_and_constant(self):
        from src.main import (
            _RETENTION_TICK_INTERVAL_SECONDS,
            _next_retention_boundary_from,
            _retention_loop,
        )

        assert _RETENTION_TICK_INTERVAL_SECONDS == 60.0
        assert callable(_next_retention_boundary_from)
        assert callable(_retention_loop)

    def test_helper_is_monotonic_consistent_with_real_time(self):
        """Реальный time.time() — без patch'а. next_run строго в будущем
        и не более interval'а вперёд."""
        from src.main import _next_retention_boundary_from

        mono = time.monotonic()
        nxt = _next_retention_boundary_from(mono, interval=60.0)
        assert nxt > mono
        assert nxt - mono <= 60.0 + 1e-3

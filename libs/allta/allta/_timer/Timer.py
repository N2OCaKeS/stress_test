from __future__ import annotations

from time import perf_counter


class Timer:
    """Простой таймер с человекочитаемым временем выполнения."""

    def __init__(self) -> None:
        self._started_at: float | None = None

    def start(self) -> "Timer":
        """Запоминает текущее время старта."""

        self._started_at = perf_counter()
        return self

    def stop(self) -> str:
        """Останавливает таймер и возвращает длительность строкой."""

        if self._started_at is None:
            raise RuntimeError("Таймер не был запущен. Сначала вызовите start().")

        elapsed = perf_counter() - self._started_at
        self._started_at = None
        return self.format_duration(elapsed)

    @staticmethod
    def format_duration(duration_seconds: int | float) -> str:
        """Форматирует длительность в короткий вид: 1д 7ч 51м, 17ч 45м, 25м."""

        if duration_seconds < 0:
            raise ValueError("Длительность не может быть отрицательной.")

        if duration_seconds < 1:
            milliseconds = round(duration_seconds * 1000)
            return f"{milliseconds}мс"

        total_seconds = int(duration_seconds)
        days, remainder = divmod(total_seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)

        if days > 0:
            parts = [f"{days}д"]
            if hours > 0:
                parts.append(f"{hours}ч")
            if minutes > 0:
                parts.append(f"{minutes}м")
            return " ".join(parts)

        if hours > 0:
            parts = [f"{hours}ч"]
            if minutes > 0:
                parts.append(f"{minutes}м")
            return " ".join(parts)

        if minutes > 0:
            return f"{minutes}м"

        return f"{seconds}с"

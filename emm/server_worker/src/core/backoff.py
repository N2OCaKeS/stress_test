"""Helper для exponential backoff с двойным cap'ом.

Три call-site'а считают паузу между ретраями по одной формуле:
`min(coefficient * base ** min(attempts, exp_cap), cap_seconds)`.

- `audit_outbox_publisher._apply_backoff` и `dispatch_outbox._compute_next_retry_at`
  — `base=2`, `coefficient=1`: пауза `2^attempts` секунд.
- `_runner._compute_backoff_delay` — `base=2`, `coefficient=10`: пауза
  `10 * 2^(attempt-1)` секунд (in-process task retry стартует с 10с, не с 1с).

Параметры разные (base, coefficient, exp_cap, cap_seconds — у каждого свои
бизнес-cap'ы), поэтому хелпер принимает их явно, не зашивая дефолты.

Внутренний cap'аем показатель степени (CPU/memory guard на случай
accidental overflow attempts — `2 ** 10000` в Python работает, но
жрёт ресурсы). Внешний cap режет результат в секундах (бизнес-cap:
дальше row простаивает слишком долго).
"""

from __future__ import annotations


def compute_retry_delay(
    attempts: int,
    *,
    base: float,
    cap_seconds: float,
    exp_cap: int,
    coefficient: float = 1.0,
) -> float:
    """Вычислить задержку до следующей попытки.

    `attempts` — число уже выполненных неуспешных попыток (≥0).
    `base` — основание степени (обычно `2.0`).
    `cap_seconds` — потолок результата в секундах (бизнес-cap).
    `exp_cap` — потолок показателя степени (CPU/memory guard).
    `coefficient` — множитель перед степенью (стартовая задержка при exponent=0).

    Возвращает `min(coefficient * base ** min(max(attempts, 0), exp_cap), cap_seconds)`.
    """
    exponent = min(max(attempts, 0), exp_cap)
    return min(coefficient * base ** exponent, cap_seconds)

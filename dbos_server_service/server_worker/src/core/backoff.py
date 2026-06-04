"""Helper для exponential backoff с двойным cap'ом.

Два call-site'а (`audit_outbox_publisher._apply_backoff` и
`dispatch_outbox._compute_next_retry_at`) считают паузу между ретраями
по одной формуле: `min(base ** min(attempts, exp_cap), cap_seconds)`.
Параметры разные (base, exp_cap, cap_seconds — у каждого свои бизнес-cap'ы),
поэтому хелпер принимает их явно, не зашивая дефолты.

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
) -> float:
    """Вычислить задержку до следующей попытки.

    `attempts` — число уже выполненных неуспешных попыток (≥0).
    `base` — основание степени (обычно `2.0`).
    `cap_seconds` — потолок результата в секундах (бизнес-cap).
    `exp_cap` — потолок показателя степени (CPU/memory guard).

    Возвращает `min(base ** min(max(attempts, 0), exp_cap), cap_seconds)`.
    """
    exponent = min(max(attempts, 0), exp_cap)
    return min(base ** exponent, cap_seconds)

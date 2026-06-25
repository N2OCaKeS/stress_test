"""Общий движок throttle'а аудита раскрытия секретов.

Раскрытие паролей живёт в двух местах с идентичной семантикой:
`server_account` (account password reveal) и `ipmi_controller` (BMC password
reveal). Оба держат in-memory окно `(actor_id, target_id) → (last_critical_at,
count)` и хотят один CRITICAL на окно, остальное — INFO throttled со счётчиком.

Алгоритм один, отличается только владелец словаря и имя второго ключа.
Поэтому сам словарь и кэп остаются в вызывающем модуле (это удобно для
тестов и для раздельных bucket'ов без коллизий), а повторяющаяся логика
окна + bounded-LRU eviction вынесена сюда.
"""

import time
from collections import OrderedDict

RevealWindow = "OrderedDict[tuple[str, str], tuple[float, int]]"


def record_reveal(
    window: "OrderedDict[tuple[str, str], tuple[float, int]]",
    cap: int,
    actor_id: str | None,
    target_id: str,
    window_seconds: float,
) -> tuple[bool, int]:
    """Зафиксировать reveal и вернуть `(should_emit_critical, total_in_window)`.

    `total_in_window` — накопленный счётчик reveal'ов для пары
    `(actor_id, target_id)` в текущем окне (включая текущий вызов). На первом
    вызове в окне эмитится CRITICAL и счётчик стартует с 1; последующие в том
    же окне — INFO throttled с инкрементом. Окно отсчитывается от первого
    CRITICAL'а; счётчик сбрасывается, когда CRITICAL уезжает за горизонт.

    `actor_id is None` (анонимные / сервисные без identity) — всегда CRITICAL,
    счётчик не накапливается (мерджить разные `None`-bucket'ы опасно).
    `window_seconds <= 0` отключает throttle: всегда CRITICAL, счётчик не ведётся.

    Bounded LRU: при штурме длинного списка уникальных пар словарь рос бы
    безгранично между sweep'ами (stale-cleanup срабатывает только на miss того
    же окна). Кэп `cap` держит размер в пределе — на вытеснении уходит самый
    старый по обращению ключ. Throttle-семантика не страдает: вытесненный actor
    получит ещё один CRITICAL вместо INFO, что в реальном шторме даёт SIEM
    больше сигнала, а не меньше.
    """
    if window_seconds <= 0 or actor_id is None:
        return True, 1
    now = time.monotonic()
    key = (actor_id, target_id)
    entry = window.get(key)
    if entry is None or (now - entry[0]) >= window_seconds:
        window[key] = (now, 1)
        window.move_to_end(key)
        # Best-effort sweep устаревших ключей — линейный пробег только на
        # miss'е (т.е. редко), для O(n) словаря допустимо.
        stale_cutoff = now - window_seconds
        stale_keys = [k for k, (ts, _cnt) in window.items() if ts < stale_cutoff]
        for k in stale_keys:
            window.pop(k, None)
        while len(window) > cap:
            window.popitem(last=False)
        return True, 1
    last_at, count = entry
    new_count = count + 1
    window[key] = (last_at, new_count)
    window.move_to_end(key)
    return False, new_count

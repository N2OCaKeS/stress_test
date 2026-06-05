"""Process-level operational counters.

Без `/metrics`-endpoint'а: значения читаются getter'ами либо логируются
вызывающим кодом (например, в `/ready` диагностике или periodic
heartbeat'е). Mirror'ит подход `audit_service._audit_dropped_429`:
per-process int + getter + test reset.

* `_dispatch_outbox_pending_depth` — последний снимок глубины
  `dispatch_outbox` (rows с `dispatched_at IS NULL`). Заполняется
  caller'ом, который ходит в repo с `dispatch_outbox_pending_count()`.
* `_secrets_decrypt_failures_total` — монотонный счётчик per-row
  decrypt-ошибок: reveal-секретов в `internal_service`, миграция в
  `secrets_migration_service`. Ненулевое значение в проде сигналит
  про rotation key version'а либо мисс-конфиг `hkdf_salt_hex`.

Все getter'ы возвращают локальный (per-process) snapshot. При
`uvicorn --workers N` сумма по pod'у — забота внешнего агрегатора.
"""

from __future__ import annotations

_dispatch_outbox_pending_depth: int = 0
_secrets_decrypt_failures_total: int = 0


def set_dispatch_outbox_pending_depth(value: int) -> None:
    """Записать последний снимок глубины outbox'а."""
    global _dispatch_outbox_pending_depth
    _dispatch_outbox_pending_depth = max(0, int(value))


def get_dispatch_outbox_pending_depth() -> int:
    return _dispatch_outbox_pending_depth


def increment_secrets_decrypt_failures(by: int = 1) -> None:
    """+1 при каждой decrypt-ошибке в hot-path (reveal / migration)."""
    global _secrets_decrypt_failures_total
    _secrets_decrypt_failures_total += max(0, int(by))


def get_secrets_decrypt_failures_total() -> int:
    return _secrets_decrypt_failures_total


def _reset_for_tests() -> None:
    """Сбросить счётчики между прогонами тестов."""
    global _dispatch_outbox_pending_depth, _secrets_decrypt_failures_total
    _dispatch_outbox_pending_depth = 0
    _secrets_decrypt_failures_total = 0

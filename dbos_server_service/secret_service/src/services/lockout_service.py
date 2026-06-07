"""In-memory account-level lockout для secret_service.

Защищает от brute-force/credential-stuffing на reveal-эндпоинтах: после N
denied access'ов в скользящем окне actor блокируется на 15 минут (по
дефолту). Симметрично паттерну `auth_service._lockout`, но без БД —
secret_service хочет дешёвый, in-process throttle поверх уже-аутентифицированной
identity (которую auth_service уже выдал валидной). Защита тут — не от
форджа bearer'а (это auth-domain), а от того, что валидный bearer
устраивает спам по чужим credential-id'ам в надежде попасть в чужой ACL.

Per-process state — это аналог in-memory cache в `auth.py` (TTL 5s). При
multi-pod deploy лимит размывается ×2; Phase 6 переезжает на Redis
(ключ `lockout:user:<id>`, INCR + EXPIRE), тогда лимит станет fleet-wide.

Threshold: `LOCKOUT_THRESHOLD` denied за `LOCKOUT_WINDOW_SECONDS` → lock
на `LOCKOUT_DURATION_SECONDS`. Дефолты: 10 за 5 мин → lock на 15 мин.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from src.core.config import get_settings


@dataclass
class _UserState:
    """Per-user счётчик + lockout-метка.

    `failures` — список monotonic-timestamps последних denied access'ов;
    обрезается до окна на каждой записи (старые «выпадают» — окно
    скользит). `locked_until` — monotonic-timestamp до которого actor
    залочен (или None, если активного lockout'а нет).
    """

    failures: list[float] = field(default_factory=list)
    locked_until: float | None = None


# Module-level state. dict per user_id, lock — для thread-safety
# (uvicorn worker может в принципе обрабатывать concurrent корутины
# на одном loop'е, но lock дешёв и страхует от sync-race'а в фикстурах).
_state: dict[str, _UserState] = {}
_lock = threading.Lock()


def _now() -> float:
    """monotonic — не подвержен NTP-сдвигам, идеально для окон."""
    return time.monotonic()


def _prune(state: _UserState, window: float, now: float) -> None:
    """Выкинуть failures старше окна (in-place)."""
    cutoff = now - window
    state.failures = [t for t in state.failures if t >= cutoff]


def record_denied(user_id: str) -> bool:
    """Учесть очередной denied access. Возвращает True, если actor залочен.

    True означает, что эта попытка ПЕРЕВАЛИЛА порог и lockout
    активирован (или уже был активен). Caller обычно эмитит CRITICAL
    audit-event при возврате True.
    """
    settings = get_settings()
    threshold = settings.lockout_threshold
    window = settings.lockout_window_seconds
    duration = settings.lockout_duration_seconds
    now = _now()

    with _lock:
        state = _state.setdefault(user_id, _UserState())
        # JIT-сброс: если истёкший lockout до сих пор висит — снимаем.
        if state.locked_until is not None and state.locked_until <= now:
            state.locked_until = None
            state.failures.clear()

        state.failures.append(now)
        _prune(state, window, now)

        if len(state.failures) >= threshold:
            state.locked_until = now + duration
            return True
        return state.locked_until is not None


def is_locked(user_id: str) -> bool:
    """Проверка lockout'а без записи failure (для pre-check'а)."""
    now = _now()
    with _lock:
        state = _state.get(user_id)
        if state is None:
            return False
        if state.locked_until is None:
            return False
        if state.locked_until <= now:
            # Окно истекло — лениво снимаем, чтоб не накапливать stale state.
            state.locked_until = None
            state.failures.clear()
            return False
        return True


def clear(user_id: str) -> None:
    """Сбросить failures + lockout — после успешного auth'd action.

    Зеркало `register_success` в auth_service: успешный reveal/read
    означает, что bearer законный и actor не лезет в чужое.
    """
    with _lock:
        _state.pop(user_id, None)


def retry_after_seconds(user_id: str) -> int:
    """Сколько секунд до снятия lockout'а (для 429 Retry-After)."""
    now = _now()
    with _lock:
        state = _state.get(user_id)
        if state is None or state.locked_until is None:
            return 0
        remaining = int(state.locked_until - now)
        return max(0, remaining)


def _reset_for_tests() -> None:
    """Test helper — снести весь in-memory state между прогонами."""
    with _lock:
        _state.clear()

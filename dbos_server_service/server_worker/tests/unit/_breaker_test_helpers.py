"""Общий FakeRedis для unit-тестов breaker'ов.

`bmc_circuit_breaker` и `audit_publisher_breaker` шарят Lua-скрипты
(`src.services._breaker_lua`), и оба теста раньше держали по копии
FakeRedis с py-репликой этих скриптов. Теперь — один общий класс.

FakeRedis матчит скрипты по объекту-строке: `cb._CHECK_SCRIPT is
_breaker_lua.CHECK_SCRIPT`, потому в обоих тестах в `eval()` приходит
один и тот же объект, и `if script == CHECK_SCRIPT` ловит обе ветки.

TTL не симулируем штатно для всех ключей — все тесты двигают `now`
через `frozen_clock` фикстуру, которая monkeypatch'ит `cb.time.time`.
Исключение — probe-ключ: для него отдельно держим expiry в `_expiry`,
потому что `CHECK_SCRIPT` дёргает `TTL` probe-ключа, чтобы вернуть
retry_after для проигравших SETNX. Без модели TTL CHECK возвращал бы
бесконечный или нулевой retry_after — тест на thundering herd
без этой детали смысла не имеет.
"""

from __future__ import annotations

import pytest

from src.services import _breaker_lua


class FakeRedis:
    """In-memory Redis с py-реализацией трёх Lua-скриптов breaker'а.

    `_store` — class-level: все инстансы шарят один dict, что моделирует
    «N реплик worker'а смотрят в один Redis». Сброс — через
    `reset_store()` (фикстура делает это автоматически).

    `_expiry` — отдельный dict с absolute expiry time (тот же frozen
    `now` из тестов). Используется только для probe-ключа: `CHECK_SCRIPT`
    дёргает у него `TTL`, остальным ключам expiry не нужен — тест либо
    двигает `now` за окно, либо явно проверяет наличие ключа.
    """

    _store: dict[str, str] = {}
    _expiry: dict[str, int] = {}
    _now_provider = staticmethod(lambda: 0)

    def __init__(self) -> None:
        pass

    @classmethod
    def reset_store(cls) -> None:
        cls._store.clear()
        cls._expiry.clear()

    async def eval(self, script: str, n: int, *args: str):  # noqa: PLR0911, PLR0912, PLR0915
        keys = list(args[:n])
        argv = list(args[n:])

        if script == _breaker_lua.CHECK_SCRIPT:
            now = int(argv[0])
            cooldown = int(argv[1])
            probe_key = keys[3]
            state = self._store.get(keys[1])
            open_until_raw = self._store.get(keys[2], "0")
            try:
                open_until = int(open_until_raw)
            except ValueError:
                open_until = 0
            if state == "open":
                if open_until > now:
                    return [state, open_until - now]
                # Cooldown истёк — пытаемся захватить probe-слот.
                if probe_key in self._store:
                    ttl = self._expiry.get(probe_key, now + cooldown) - now
                    if ttl < 0:
                        ttl = cooldown
                    return ["open", ttl]
                self._store[probe_key] = "1"
                self._expiry[probe_key] = now + cooldown
                self._store[keys[1]] = "half_open"
                self._store.pop(keys[2], None)
                return ["half_open", 0]
            if state == "half_open":
                ttl = self._expiry.get(probe_key, now + cooldown) - now
                if ttl < 0:
                    ttl = cooldown
                return ["open", ttl]
            if state is not None:
                return [state, 0]
            return ["closed", 0]

        if script == _breaker_lua.RECORD_SUCCESS_SCRIPT:
            for k in keys:
                self._store.pop(k, None)
                self._expiry.pop(k, None)
            return 1

        if script == _breaker_lua.RECORD_FAILURE_SCRIPT:
            now = int(argv[0])
            threshold = int(argv[1])
            cooldown = int(argv[3])
            current_state = self._store.get(keys[1])
            if current_state == "half_open":
                # Пробный запрос провалился — сразу обратно в open,
                # счётчик failures и probe-ключ сносим.
                self._store[keys[1]] = "open"
                self._store[keys[2]] = str(now + cooldown)
                self._store.pop(keys[0], None)
                self._store.pop(keys[3], None)
                self._expiry.pop(keys[3], None)
                return ["open", 1]
            try:
                cur = int(self._store.get(keys[0], "0"))
            except ValueError:
                cur = 0
            cur += 1
            self._store[keys[0]] = str(cur)
            if cur >= threshold:
                self._store[keys[1]] = "open"
                self._store[keys[2]] = str(now + cooldown)
                self._store.pop(keys[0], None)
                return ["open", cur]
            return ["closed", cur]

        raise AssertionError(f"unexpected script: {script[:40]!r}")

    async def delete(self, *keys: str) -> int:
        n = 0
        for k in keys:
            if k in self._store:
                self._store.pop(k, None)
                self._expiry.pop(k, None)
                n += 1
        return n

    async def aclose(self) -> None:
        return None


def install_fake_redis(monkeypatch: pytest.MonkeyPatch, breaker_module) -> type[FakeRedis]:
    """Подменить `_get_client` целевого breaker-модуля на FakeRedis-фабрику.

    Сбрасывает shared `_store` перед каждым вызовом — чистая изоляция
    между тестами. Возвращает класс FakeRedis для прямых assertion'ов
    по `_store` (тесты иногда проверяют конкретные ключи).
    """
    FakeRedis.reset_store()

    async def fake_get_client():
        return FakeRedis()

    monkeypatch.setattr(breaker_module, "_get_client", fake_get_client)
    return FakeRedis


def frozen_clock_fixture(monkeypatch: pytest.MonkeyPatch, breaker_module) -> dict:
    """Замораживает `time.time` внутри указанного breaker-модуля.

    Возвращает dict `{"now": float}` — тесты двигают вперёд для эмуляции
    истечения cooldown'а. Без freeze тесты flaky на медленных runner'ах.
    """
    state = {"now": 1_700_000_000.0}

    def fake_time() -> float:
        return state["now"]

    monkeypatch.setattr(breaker_module.time, "time", fake_time)
    return state

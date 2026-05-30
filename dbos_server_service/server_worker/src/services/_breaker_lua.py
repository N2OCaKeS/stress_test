"""Shared Lua-скрипты для circuit breaker'ов worker'а.

`bmc_circuit_breaker` и `audit_publisher_breaker` оба держат breaker
поверх Redis с одинаковым state-machine: счётчик failures в окне →
порог → open на cooldown → half_open после истечения → success
сбрасывает всё. Логика one-to-one, отличаются только префиксы ключей
и наличие host-измерения.

Раньше каждый модуль держал свою копию трёх Lua-скриптов (~80%
совпадения). При правке состояния (например, добавление нового
поля или смена семантики half_open'а) приходилось править оба места
синхронно — лёгко промазать.

Вынесено в общий модуль с тремя константами:

* `CHECK_SCRIPT` — `{state, retry_after_seconds}`.
* `RECORD_SUCCESS_SCRIPT` — reset всех трёх ключей; всегда `1`.
* `RECORD_FAILURE_SCRIPT` — INCR failures, если >= threshold → open
  на cooldown; возвращает `{state_after, current_count}`.

KEYS-контракт обоих breaker'ов одинаков:
  KEYS[1] = failures, KEYS[2] = state, KEYS[3] = open_until.
ARGV — параметры конкретного вызова (см. docstring каждой константы).

Семантика подробно описана в `bmc_circuit_breaker.__doc__`.
"""

from __future__ import annotations


# check(): вернуть {state, retry_after}.
#   ARGV[1] = now (unix seconds)
#   ARGV[2] = cooldown_seconds
#
# Логика:
#   - читаем state и open_until;
#   - state="open" и open_until > now → возвращаем open + остаток окна;
#   - state="open" и open_until <= now → переход в half_open (SET
#     state=half_open EX cooldown, DEL open_until);
#   - иначе — возвращаем что есть (или "closed" если ключа нет).
CHECK_SCRIPT = """
local now = tonumber(ARGV[1])
local cooldown = tonumber(ARGV[2])
local state = redis.call('GET', KEYS[2])
local open_until = tonumber(redis.call('GET', KEYS[3]) or '0')
if state == 'open' then
  if open_until > now then
    return {state, open_until - now}
  end
  redis.call('SET', KEYS[2], 'half_open', 'EX', cooldown)
  redis.call('DEL', KEYS[3])
  return {'half_open', 0}
end
if state then
  return {state, 0}
end
return {'closed', 0}
"""


# record_success(): полный reset (failures, state, open_until).
# Успех всегда закрывает breaker; race с параллельным fail'ом
# безопасен — следующий же fail откроет breaker заново.
RECORD_SUCCESS_SCRIPT = """
redis.call('DEL', KEYS[1])
redis.call('DEL', KEYS[2])
redis.call('DEL', KEYS[3])
return 1
"""


# record_failure(): INCR failures; >= threshold → open(cooldown).
#   ARGV[1] = now (unix seconds)
#   ARGV[2] = threshold
#   ARGV[3] = window_seconds (EXPIRE счётчика)
#   ARGV[4] = cooldown_seconds
# Возвращает {state_after, current_count}. EXPIRE на счётчик ставим
# только при первом INCR — rolling window.
RECORD_FAILURE_SCRIPT = """
local now = tonumber(ARGV[1])
local threshold = tonumber(ARGV[2])
local window = tonumber(ARGV[3])
local cooldown = tonumber(ARGV[4])
local count = redis.call('INCR', KEYS[1])
if count == 1 then
  redis.call('EXPIRE', KEYS[1], window)
end
if count >= threshold then
  redis.call('SET', KEYS[2], 'open', 'EX', cooldown)
  redis.call('SET', KEYS[3], tostring(now + cooldown), 'EX', cooldown)
  redis.call('DEL', KEYS[1])
  return {'open', count}
end
return {'closed', count}
"""

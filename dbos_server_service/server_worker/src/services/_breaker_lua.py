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
* `RECORD_SUCCESS_SCRIPT` — reset всех четырёх ключей; всегда `1`.
* `RECORD_FAILURE_SCRIPT` — INCR failures, если >= threshold → open
  на cooldown; возвращает `{state_after, current_count}`.

KEYS-контракт обоих breaker'ов одинаков:
  KEYS[1] = failures, KEYS[2] = state, KEYS[3] = open_until,
  KEYS[4] = probe (in-flight half_open marker).
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
#   - state="open" и open_until <= now → пытаемся захватить probe-слот
#     через SET NX EX cooldown. Победитель SETNX переводит state в
#     half_open и пропускает пробный запрос; проигравшие получают
#     {open, cooldown} — другая реплика уже отправила пробный запрос,
#     до его исхода никого больше не пускаем (thundering herd на
#     половине отказавшего downstream'а — основная причина наличия
#     probe-ключа);
#   - state="half_open" → пробный запрос уже в полёте, тоже отбиваем
#     {open, retry_after}; retry_after берём из PTTL probe-ключа,
#     либо cooldown как консервативный фолбэк;
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
  local won = redis.call('SET', KEYS[4], '1', 'NX', 'EX', cooldown)
  if not won then
    local ttl = redis.call('TTL', KEYS[4])
    if ttl < 0 then ttl = cooldown end
    return {'open', ttl}
  end
  redis.call('SET', KEYS[2], 'half_open', 'EX', cooldown)
  redis.call('DEL', KEYS[3])
  return {'half_open', 0}
end
if state == 'half_open' then
  local ttl = redis.call('TTL', KEYS[4])
  if ttl < 0 then ttl = cooldown end
  return {'open', ttl}
end
if state then
  return {state, 0}
end
return {'closed', 0}
"""


# record_success(): полный reset (failures, state, open_until, probe).
# Успех всегда закрывает breaker; race с параллельным fail'ом
# безопасен — следующий же fail откроет breaker заново. Probe-ключ
# тоже сносим: пробный запрос отработал успешно, in-flight маркер
# больше никому не нужен.
RECORD_SUCCESS_SCRIPT = """
redis.call('DEL', KEYS[1])
redis.call('DEL', KEYS[2])
redis.call('DEL', KEYS[3])
redis.call('DEL', KEYS[4])
return 1
"""


# record_failure(): INCR failures; >= threshold → open(cooldown).
#   ARGV[1] = now (unix seconds)
#   ARGV[2] = threshold
#   ARGV[3] = window_seconds (EXPIRE счётчика)
#   ARGV[4] = cooldown_seconds
# Возвращает {state_after, current_count}. EXPIRE на счётчик ставим
# только при первом INCR — rolling window.
#
# Fail в half_open сразу возвращает breaker в open — пробный запрос
# подтвердил, что канал ещё не починен, нет смысла собирать threshold
# с нуля и пропускать в этот раз до threshold-1 пробных запросов через
# тот же cooldown. Это стандартный CB-pattern (half_open — ровно один
# пробный запрос на cycle). Probe-ключ тоже сносим — освобождаем слот
# под следующий цикл (когда новый cooldown истечёт и check снова
# попытается захватить probe).
RECORD_FAILURE_SCRIPT = """
local now = tonumber(ARGV[1])
local threshold = tonumber(ARGV[2])
local window = tonumber(ARGV[3])
local cooldown = tonumber(ARGV[4])
local current_state = redis.call('GET', KEYS[2])
if current_state == 'half_open' then
  redis.call('SET', KEYS[2], 'open', 'EX', cooldown)
  redis.call('SET', KEYS[3], tostring(now + cooldown), 'EX', cooldown)
  redis.call('DEL', KEYS[1])
  redis.call('DEL', KEYS[4])
  return {'open', 1}
end
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

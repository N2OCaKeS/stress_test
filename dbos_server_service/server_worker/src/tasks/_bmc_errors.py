"""Маппинг BMC-исключений в доменные `AppException(BMC_*)`.

Worker говорит с BMC двумя транспортами:

* `RedfishClient` поднимает `RedfishError(status_code, message, redfish_error_code)`.
* `IpmitoolClient` поднимает `IpmitoolError(returncode, stderr, argv_safe, message)`
  и подкласс `IpmitoolTimeout`.

`tasks/power.py` и `tasks/passwords.py` ловят их через единый `wrap_bmc_error`
и поднимают `AppException` с одним из стабильных кодов:

* `BMC_UNREACHABLE` — сеть/таймаут/connect refused, либо ipmitool не нашёл RMCP+
  сессию.
* `BMC_AUTH_FAILED` — 401/403 от Redfish, либо ipmitool stderr с `RAKP`/`Authentication`
  маркерами.
* `BMC_REJECTED` — Redfish 4xx (не auth) либо ipmitool вернул rc!=0 с
  «не таймаут, не auth» причиной (BMC отверг команду — invalid state и т.п.).
* `BMC_TIMEOUT` — `IpmitoolTimeout` (subprocess не уложился в timeout).
* `BMC_ERROR` — fallback: Redfish 5xx, неопознанный rc от ipmitool, и т.д.

`_runner.run_task` видит итоговый `AppException` и кладёт `error_code` в
`task.last_error` + audit details. Это даёт оператору единообразную диагностику
независимо от того, сходил handler в Redfish или в ipmitool fallback.
"""

from __future__ import annotations

from src.clients.ipmitool import IpmitoolError, IpmitoolTimeout
from src.clients.redfish import RedfishError
from src.core.exceptions import AppException

# Маркеры в stderr ipmitool, по которым считаем что BMC ответил, но отверг auth.
# `RAKP` (RMCP+ Authenticated Key-Exchange Protocol) — фигурирует в большинстве
# auth-error'ов lanplus. `Authentication` — generic fallback. Чек регистронезависимый,
# substring — реальные сообщения ipmitool несут разный wording в зависимости от
# билда (1.8.18 vs 1.8.19, Astra vs Ubuntu).
_AUTH_STDERR_MARKERS = ("rakp", "authentication", "username", "password is incorrect")

# Маркеры transport-level ошибок — ipmitool не смог установить RMCP+ сессию
# (BMC недоступен по сети, либо порт 623/udp закрыт). Отличается от auth тем,
# что соединение не дошло до фазы auth.
_UNREACH_STDERR_MARKERS = (
    "unable to establish",
    "no response",
    "connection refused",
    "connection timed out",
    "address already in use",
)


def _wrap_redfish_error(action: str, exc: RedfishError) -> AppException:
    """Маппинг `RedfishError` → `AppException(BMC_*)`.

    Приоритеты кодов: transport (status_code is None) → auth (401/403) →
    rejected (прочие 4xx) → error (5xx/прочее). Этот порядок зеркалит старые
    локальные хелперы в `power.py` и `passwords.py` до выноса в общий модуль.
    """
    if exc.status_code is None:
        code = "BMC_UNREACHABLE"
    elif exc.status_code in (401, 403):
        code = "BMC_AUTH_FAILED"
    elif 400 <= exc.status_code < 500:
        code = "BMC_REJECTED"
    else:
        code = "BMC_ERROR"
    return AppException(
        error_code=code,
        message=f"{action}: {exc.message}",
        details={
            "status_code": exc.status_code,
            "redfish_error_code": exc.redfish_error_code,
            "transport": "redfish",
        },
    )


def _wrap_ipmitool_error(action: str, exc: IpmitoolError) -> AppException:
    """Маппинг `IpmitoolError` → `AppException(BMC_*)`.

    ipmitool возвращает скудный набор сигналов: `returncode` (обычно 1 на любой
    fail) и текст stderr. Логика:

      * `IpmitoolTimeout` → `BMC_TIMEOUT` (отдельный подкласс).
      * `returncode == -1` и `message == "ipmitool binary not found"` →
        `BMC_UNREACHABLE` — окружение не настроено, операционно эквивалентно
        «BMC не отвечает».
      * stderr-substring `unable to establish` / `no response` → `BMC_UNREACHABLE`.
      * stderr-substring `rakp` / `authentication` → `BMC_AUTH_FAILED`.
      * иначе rc != 0 → `BMC_REJECTED` (BMC отверг команду — invalid state,
        out-of-range user_id, и т.п.).
      * fallback → `BMC_ERROR`.

    `argv_safe` уже маскирует пароль (см. `ipmitool._mask_password_in_argv`), его
    кладём в `details` для diagnostics — он попадает в audit без plaintext.
    """
    if isinstance(exc, IpmitoolTimeout):
        code = "BMC_TIMEOUT"
    elif exc.returncode == -1 and "not found" in exc.message:
        code = "BMC_UNREACHABLE"
    else:
        stderr_lc = (exc.stderr or "").lower()
        if any(m in stderr_lc for m in _UNREACH_STDERR_MARKERS):
            code = "BMC_UNREACHABLE"
        elif any(m in stderr_lc for m in _AUTH_STDERR_MARKERS):
            code = "BMC_AUTH_FAILED"
        elif exc.returncode not in (0, -1):
            code = "BMC_REJECTED"
        else:
            code = "BMC_ERROR"

    return AppException(
        error_code=code,
        message=f"{action}: {exc.message or 'ipmitool failed'}",
        details={
            "returncode": exc.returncode,
            "argv_safe": exc.argv_safe,
            "transport": "ipmitool",
        },
    )


def wrap_bmc_error(action: str, exc: BaseException) -> AppException:
    """Единая точка маппинга BMC-ошибок.

    Принимает либо `RedfishError`, либо `IpmitoolError` (включая `IpmitoolTimeout`).
    Любой другой тип → пробрасываем как есть: caller обернёт через `raise ... from`.
    """
    if isinstance(exc, RedfishError):
        return _wrap_redfish_error(action, exc)
    if isinstance(exc, IpmitoolError):
        return _wrap_ipmitool_error(action, exc)
    raise TypeError(f"wrap_bmc_error: unsupported exception type {type(exc).__name__}")


# Маппинг унифицированного power-action'а на конкретный API клиента. Worker-handler
# говорит на «redfish-словаре» (`On`/`ForceOff`/`GracefulShutdown`/`PowerCycle`/
# `GracefulRestart`/`ForceRestart`), а `dispatch_power_action` переводит это в
# `chassis power {on|off|cycle|reset|soft}` для ipmitool.
#
# Различия семантики:
#   * `ForceOff`           ↔ `off`   (hard power-off)
#   * `GracefulShutdown`   ↔ `soft`  (ACPI shutdown через chassis power soft)
#   * `On`                 ↔ `on`
#   * `PowerCycle`         ↔ `cycle`
#   * `GracefulRestart`    ↔ `reset` (на ipmitool 2.0 graceful-reset нет, ставим
#                                     `reset` — caller на legacy BMC платит за
#                                     hard-restart семантикой; это лучше чем
#                                     неработающий `soft+on`).
#   * `ForceRestart`       ↔ `reset`
_IPMITOOL_POWER_MAP: dict[str, str] = {
    "On": "on",
    "ForceOff": "off",
    "GracefulShutdown": "soft",
    "PowerCycle": "cycle",
    "GracefulRestart": "reset",
    "ForceRestart": "reset",
}


async def dispatch_power_action(client, action: str) -> None:
    """Унифицированный power-action на любом BMC-клиенте.

    `action` — redfish-нотация (`On`/`ForceOff`/...). Для Redfish-клиента
    вызывается `power_action(action)` напрямую. Для ipmitool — мэппится через
    `_IPMITOOL_POWER_MAP` и вызывается `chassis_power_action(...)`.

    Если `action` отсутствует в карте — `ValueError`: для legacy BMC некоторые
    действия (`PushPowerButton`, `Nmi`) не поддерживаются ipmitool'ом, и
    handler должен сам решить — fail fast или fallback на ближайшее эквивалентное.
    """
    if hasattr(client, "chassis_power_action"):
        ipmi_action = _IPMITOOL_POWER_MAP.get(action)
        if ipmi_action is None:
            raise ValueError(
                f"BMC_UNSUPPORTED_ACTION: {action!r} has no ipmitool equivalent"
            )
        await client.chassis_power_action(ipmi_action)
    elif hasattr(client, "power_action"):
        await client.power_action(action)
    else:
        raise RuntimeError(
            f"BMC_CLIENT_INCOMPATIBLE: {type(client).__name__} has no "
            "`power_action` nor `chassis_power_action`"
        )


async def dispatch_get_power_state(client) -> str:
    """Унифицированное чтение состояния питания.

    Возвращает Redfish-нотацию (`On`/`Off`/`PoweringOn`/...). ipmitool отдаёт
    только `on|off`, маппим в `On`/`Off`. Дальнейший `_normalize_power_state` в
    `tasks/power.py` уже превращает это в snake_case для API.
    """
    if hasattr(client, "chassis_power_status"):
        state = await client.chassis_power_status()
        # ipmitool: "on" | "off" → "On" | "Off" (Redfish-style).
        return state.capitalize()
    if hasattr(client, "get_power_state"):
        return await client.get_power_state()
    raise RuntimeError(
        f"BMC_CLIENT_INCOMPATIBLE: {type(client).__name__} has no "
        "`get_power_state` nor `chassis_power_status`"
    )


async def dispatch_rotate_user_password(client, user_id: int, new_password: str) -> None:
    """Унифицированная ротация BMC-пароля.

    Redfish: PATCH `/Managers/{id}/Accounts/{user_id}`.
    ipmitool: `user set password <user_id> <newpass>`.
    """
    if hasattr(client, "user_set_password"):
        await client.user_set_password(user_id, new_password)
    elif hasattr(client, "rotate_user_password"):
        await client.rotate_user_password(user_id, new_password)
    else:
        raise RuntimeError(
            f"BMC_CLIENT_INCOMPATIBLE: {type(client).__name__} has no "
            "`rotate_user_password` nor `user_set_password`"
        )

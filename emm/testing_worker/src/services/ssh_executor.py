"""Исполнение резолвленной команды теста на стенде по SSH (§5, §5.5 плана миграции).

`command`, приходящий от `testing_service` (`POST /internal/queue/claim`),
уже полностью резолвлен конструктором команд (`resolve_command` на стороне
`testing_service`) в `list[str]` — обычный argv, без сборки строки наивной
конкатенацией.

SSH exec-канал, в отличие от `subprocess`, не умеет запустить "argv без
интерпретации shell'ом" — `SSHClientConnection.run()` несёт одну
command-строку, которую удалённый sshd передаёт login-шеллу пользователя
(`sh -c '<строка>'`). Эквивалент требования "без `shell=True`" здесь — не
пропустить сборку строки, а собрать её безопасно: `shlex.join(command)`
экранирует каждый аргумент по отдельности, поэтому пробел/`;`/`&&`/`$(...)`
внутри значения одного слота не может развалиться в отдельную shell-команду
или изменить границы аргументов. Это осознанная замена, не пропущенный шаг —
naive `" ".join(command)` был бы инъекцией, `shlex.join` — нет.
"""

from __future__ import annotations

import asyncio
import logging
import shlex

import asyncssh

logger = logging.getLogger("testing_worker.ssh_executor")

# Сколько последних символов stderr класть в error при провале команды.
# Лимит поля `error` на стороне testing_service — 2048 символов (весь JSON,
# не только это поле), оставляем запас под остальные ключи тела.
_ERROR_TAIL_MAX_LEN = 1800


def _tail(text: str, max_len: int) -> str:
    """Хвост строки длиной не больше `max_len` — самая свежая часть stderr обычно
    несёт причину провала (traceback/assertion в конце вывода)."""
    if len(text) <= max_len:
        return text
    return text[-max_len:]


async def execute(
    host: str,
    test_username: str,
    test_ssh_private_key: str,
    command: list[str],
    *,
    connect_timeout: float = 30.0,
    command_timeout: float = 3600.0,
) -> tuple[bool, int | None, str | None]:
    """Подключиться к `host` по ключу и исполнить `command`.

    Возвращает `(succeeded, exit_code, error)`:

    * `succeeded=True` — `exit_code == 0`, `error=None`.
    * `succeeded=False` с заполненным `exit_code` — команда реально
      выполнилась, но вернула ненулевой код; `error` — хвост stderr.
    * `succeeded=False` с `exit_code=None` — SSH-уровня провал (не удалось
      подключиться/аутентифицироваться/уложиться в таймаут); `error` —
      короткое описание причины. Никакого fallback на `test_password` при
      провале ключа — по контракту `testing_service` ключ приходит
      заполненным всегда, а протокольный провал ключа фиксируется как
      обычный провал, не повод менять способ аутентификации на лету.
    """
    try:
        client_key = asyncssh.import_private_key(test_ssh_private_key)
    except (asyncssh.KeyImportError, ValueError, TypeError) as exc:
        logger.warning("ssh_executor: invalid private key for host=%s: %s", host, type(exc).__name__)
        return False, None, f"invalid SSH private key: {type(exc).__name__}"

    cmd_str = shlex.join(command)

    try:
        conn = await asyncssh.connect(
            host=host,
            username=test_username,
            client_keys=[client_key],
            # Стенды часто переустанавливаются, host-key меняется при каждом
            # reimage — TOFU/known_hosts тут непрактичен, тот же принятый в
            # монорепо подход, что и у server_worker'а к тестовым серверам.
            known_hosts=None,
            connect_timeout=connect_timeout,
            login_timeout=connect_timeout,
        )
    except asyncssh.PermissionDenied as exc:
        logger.warning("ssh_executor: auth failed for host=%s: %s", host, type(exc).__name__)
        return False, None, f"SSH authentication failed: {type(exc).__name__}"
    except (asyncio.TimeoutError, TimeoutError) as exc:
        logger.warning("ssh_executor: connect timed out for host=%s: %s", host, type(exc).__name__)
        return False, None, f"SSH connect timed out: {type(exc).__name__}"
    except asyncssh.Error as exc:
        logger.warning("ssh_executor: asyncssh error connecting to host=%s: %s", host, type(exc).__name__)
        return False, None, f"SSH connect error: {type(exc).__name__}: {exc}"[:_ERROR_TAIL_MAX_LEN]
    except OSError as exc:
        logger.warning("ssh_executor: connect failed for host=%s: %s", host, type(exc).__name__)
        return False, None, f"connection failed: {type(exc).__name__}: {exc}"[:_ERROR_TAIL_MAX_LEN]

    try:
        async with conn:
            result = await conn.run(cmd_str, check=False, timeout=command_timeout)
    except (asyncio.TimeoutError, TimeoutError) as exc:
        logger.warning("ssh_executor: command timed out for host=%s: %s", host, type(exc).__name__)
        return False, None, f"SSH command timed out: {type(exc).__name__}"
    except asyncssh.Error as exc:
        logger.warning("ssh_executor: asyncssh error running command on host=%s: %s", host, type(exc).__name__)
        return False, None, f"SSH run error: {type(exc).__name__}: {exc}"[:_ERROR_TAIL_MAX_LEN]

    exit_status = result.exit_status
    if exit_status == 0:
        return True, 0, None

    stderr = result.stderr if isinstance(result.stderr, str) else (result.stderr or b"").decode(
        "utf-8", errors="replace"
    )
    error = _tail(stderr.strip(), _ERROR_TAIL_MAX_LEN) or f"command exited with status {exit_status}"
    return False, exit_status, error

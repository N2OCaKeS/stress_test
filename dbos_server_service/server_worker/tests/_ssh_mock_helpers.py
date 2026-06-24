"""Shared SSH-mock builders для bootstrap-тестов.

`SshClient.bootstrap_management_user` ходит на удалённый хост через
`asyncssh.connect`. В тестах мы мокаем connection и собираем sequence
ожидаемых `conn.run(...)` ответов вручную — порядок команд фиксирован
реализацией:

  0. `getent passwd <login>` — pre-check user_exists;
  1. (если юзер уже есть) `id -nG <login>` — sudo/wheel членство;
  2. (если pre-check pass'ит) `getent passwd <login>` внутри `create_user`;
  3. `useradd` / `usermod`;
  4. sudoers: `bash -c '... tee ... visudo -cf ... mv ...'`;
  5. authorized_keys: `bash -c '... grep -qxF ... >> authorized_keys'`.

Раньше каждый тест-файл (`test_server_prepare_task.py`,
`tests/unit/test_bootstrap_wheel_fallback.py` и др.) держал свои копии
`_run_result` / `_conn` / `_bootstrap_seq*`. При изменении формы
`bootstrap_management_user` (например, добавлении pre-check шага)
приходилось править все копии — и часть оставалась расходиться по
комментариям. Сейчас все builder'ы живут здесь.

Тесты НЕ импортируют SshClient или asyncssh — фабрики возвращают
готовые объекты с правильными `spec=`.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import asyncssh


def run_result(stdout: str = "", stderr: str = "", rc: int = 0):
    """Один элемент `side_effect` для `conn.run`.

    Дублирует форму, в которой `asyncssh.SSHClientConnection.run`
    возвращает результат: атрибуты `.stdout`, `.stderr`,
    `.exit_status`. Реальные тесты комбинируют такие MagicMock'и в
    список и подсовывают как `conn.run = AsyncMock(side_effect=[...])`.
    """
    res = MagicMock()
    res.stdout = stdout
    res.stderr = stderr
    res.exit_status = rc
    return res


def make_conn(run_results):
    """Сборка mock'а `asyncssh.SSHClientConnection`.

    Каждый вызов `conn.run(...)` потребит один элемент из
    `run_results`. Длина списка должна точно совпадать с числом
    ожидаемых SSH-команд — несовпадение даст `StopIteration` посреди
    bootstrap'а и тест упадёт с неинформативным трейсом.
    """
    conn = MagicMock(spec=asyncssh.SSHClientConnection)
    conn.close = MagicMock()
    conn.wait_closed = AsyncMock()
    conn.run = AsyncMock(side_effect=list(run_results))
    return conn


def bootstrap_seq(getent_rc: int = 2):
    """Sequence для happy-path «юзера ещё нет».

    Путь:
      * outer user_exists → `getent passwd` rc=2 (нет) → pre-check id -nG skip'ается;
      * `create_user` → `getent passwd` rc=2 (нет);
      * `useradd` rc=0;
      * sudoers rc=0;
      * authorized_keys rc=0.

    Итого 5 команд. `getent_rc` параметр — для тестов, которые хотят
    смоделировать иной исход pre-check'а (rc=0 даст ветку «юзер есть»,
    но тогда тест должен использовать `bootstrap_seq_existing_sudo`).
    """
    return [
        run_result("", "", getent_rc),
        run_result("", "", getent_rc),
        run_result("", "", 0),
        run_result("", "", 0),
        run_result("", "", 0),
    ]


def bootstrap_seq_existing_sudo(login: str = "dbos"):
    """Sequence для idempotent-пути «юзер уже в sudo-группе».

    Путь:
      * outer user_exists → `getent passwd` rc=0 (есть);
      * pre-check `id -nG` показывает sudo — useradd/usermod skip'аются;
      * sudoers;
      * authorized_keys.

    Итого 4 команды.
    """
    return [
        run_result(f"{login}:x:1001:1001::/home/{login}:/bin/bash", "", 0),
        run_result(f"{login} sudo\n", "", 0),
        run_result("", "", 0),
        run_result("", "", 0),
    ]


def detect_probe_result(astra: str = "", level: str = ""):
    """Один `conn.run` ответ для probe-команды `detect_management_mode`.

    Probe печатает `ASTRA=<...>` и `LEVEL=<...>` на stdout. Пустой `astra`
    означает не-Астру (`other_os`); `level` 0/1/2 → Орёл/Воронеж/Смоленск.
    """
    return run_result(f"ASTRA={astra}\nLEVEL={level}\n", "", 0)


def prepare_seq(astra: str = "", level: str = "", getent_rc: int = 2):
    """Полная sequence facade-prepare: detect-probe + bootstrap.

    Facade (`services.ssh_client.bootstrap_management_user`) сначала зовёт
    `detect_management_mode` (один probe-`conn.run`), затем обычный
    bootstrap. Дефолт — не-Астра + happy-path «юзера ещё нет».
    """
    return [detect_probe_result(astra, level), *bootstrap_seq(getent_rc)]


__all__ = [
    "run_result",
    "make_conn",
    "bootstrap_seq",
    "bootstrap_seq_existing_sudo",
    "detect_probe_result",
    "prepare_seq",
]

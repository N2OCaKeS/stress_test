"""Абстракция цели для команд worker'а: прямой сервер vs гость ВМ через hub.

Рабочая логика задач (пока — листинг пакетов) одинакова для сервера и для ВМ.
Отличается только то, как «доехать» до цели:

* сервер — прямая управляющая SSH-сессия, команда исполняется как есть;
* ВМ — вложенный `ssh`/`sshpass` из hub-сессии: hub заходит в гостя по
  управляющему ключу (managed-ВМ) либо по базовой учётке образа (legacy).

Раннер прячет это за единственным примитивом
`run(cmd, *, sudo=False, stdin=None) -> (rc, stdout, stderr)`, чтобы общий код
(`_packages_common`, `_accounts_common`) не знал, куда именно уходит команда.

`stdin` — опциональный payload для процесса команды (первично под серверный
`chpasswd`, который читает `login:pwd` со stdin). ВМ-путь пароль/ключ инлайнит
в саму строку (`echo l:p | chpasswd`, base64 key), поэтому его callers stdin не
задают и поведение остаётся прежним.
"""

from __future__ import annotations


class DirectRunner:
    """Цель — прямой сервер: обёртка над управляющей `SshClient`-сессией.

    `run` вызывает `SshClient.run` без изменений; `sudo` пробрасывается в него
    как есть (SSH-слой сам оборачивает в `sudo -S`). `stdin` уходит в
    `SshClient.run(..., stdin_payload=...)` — тот подаёт его на stdin процесса
    (после sudo-пароля, если сессия его требует), как контракт серверного
    `chpasswd`.
    """

    def __init__(self, ssh):
        self._ssh = ssh

    @property
    def host(self) -> str:
        return self._ssh.host

    async def run(self, cmd: str, *, sudo: bool = False, stdin: str | None = None):
        return await self._ssh.run(cmd, sudo=sudo, stdin_payload=stdin)


class GuestHopRunner:
    """Цель — гость ВМ: команда идёт вложенным ssh из hub-сессии.

    `connect(remote_cmd, *, sudo=False)` собирает строку входа на гостя (по
    ключу или по паролю) — её и гоняем по hub-сессии. Внешний `ssh.run` идёт без
    sudo: sudo нужен уже внутри гостя и зашивается в строку через `connect`.
    `host` — адрес hub'а, под которым падают ошибки цели.

    `stdin`, если задан, подаётся на stdin внешней hub-команды: у вложенного
    `ssh user@guest '<cmd>'` stdin внешнего процесса проксируется в stdin
    удалённой команды гостя, поэтому payload доезжает до неё. Текущие
    VM-callers stdin не задают (пароль/ключ инлайнятся в строку), для них путь
    байт-в-байт прежний.
    """

    def __init__(self, ssh, connect, *, host: str):
        self._ssh = ssh
        self._connect = connect
        self.host = host

    async def run(self, cmd: str, *, sudo: bool = False, stdin: str | None = None):
        if stdin is None:
            return await self._ssh.run(self._connect(cmd, sudo=sudo))
        return await self._ssh.run(
            self._connect(cmd, sudo=sudo), stdin_payload=stdin,
        )

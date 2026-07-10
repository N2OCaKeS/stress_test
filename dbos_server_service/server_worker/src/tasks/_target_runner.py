"""Абстракция цели для команд worker'а: прямой сервер vs гость ВМ через hub.

Рабочая логика задач (пока — листинг пакетов) одинакова для сервера и для ВМ.
Отличается только то, как «доехать» до цели:

* сервер — прямая управляющая SSH-сессия, команда исполняется как есть;
* ВМ — вложенный `ssh`/`sshpass` из hub-сессии: hub заходит в гостя по
  управляющему ключу (managed-ВМ) либо по базовой учётке образа (legacy).

Раннер прячет это за единственным примитивом
`run(cmd, *, sudo=False) -> (rc, stdout, stderr)`, чтобы общий код
(`_packages_common`) не знал, куда именно уходит команда.
"""

from __future__ import annotations


class DirectRunner:
    """Цель — прямой сервер: обёртка над управляющей `SshClient`-сессией.

    `run` вызывает `SshClient.run` без изменений; `sudo` пробрасывается в него
    как есть (SSH-слой сам оборачивает в `sudo -S`).
    """

    def __init__(self, ssh):
        self._ssh = ssh

    @property
    def host(self) -> str:
        return self._ssh.host

    async def run(self, cmd: str, *, sudo: bool = False):
        return await self._ssh.run(cmd, sudo=sudo)


class GuestHopRunner:
    """Цель — гость ВМ: команда идёт вложенным ssh из hub-сессии.

    `connect(remote_cmd, *, sudo=False)` собирает строку входа на гостя (по
    ключу или по паролю) — её и гоняем по hub-сессии. Внешний `ssh.run` идёт без
    sudo: sudo нужен уже внутри гостя и зашивается в строку через `connect`.
    `host` — адрес hub'а, под которым падают ошибки цели.
    """

    def __init__(self, ssh, connect, *, host: str):
        self._ssh = ssh
        self._connect = connect
        self.host = host

    async def run(self, cmd: str, *, sudo: bool = False):
        return await self._ssh.run(self._connect(cmd, sudo=sudo))

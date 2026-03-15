#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import click

from allta import SystemCommands  # используется для autodetect kernel
from allta_vm.commands.vm import Vm
from allta_vm.commands.snapshot import Snapshot
from allta_vm.commands.server import Server
from allta_vm.libs.exit_code import ExitCodes


def _exit_with_rc(rc: int, ok_msg: str | None = None, err_msg: str | None = None):
    """
    Унифицированный выход из CLI-команды.
    Печатаем сообщение и выходим с нужным кодом.
    """
    if rc == ExitCodes.OK:
        if ok_msg:
            click.echo(ok_msg, err=False)
    else:
        # Если знаем «имя» кода — добавим его для наглядности
        try:
            name = ExitCodes(rc).name
        except Exception:
            name = str(rc)
        msg = err_msg or "ошибка выполнения"
        click.echo(f"[ERROR] {msg} (exit={name})", err=True)
    raise click.exceptions.Exit(rc)


@click.group()
def cli():
    """CLI для управления ВМ, снапшотами и сервером."""
    pass


# === server ===

@cli.group()
def server():
    """Инициализация и установка зависимостей."""
    pass


@server.command("init")
@click.option("--phy-if", "phy_if", required=True, help="Название сетевого интерфейса")
@click.option("--ip", "ip", required=True, help="IP сервера")
def server_init(phy_if: str, ip: str):
    """Установка всех зависимостей и настройка сети."""
    # Предполагаем, что Server.server_init() возвращает int exit-code.
    try:
        rc = Server.server_init(phy_if=phy_if, ip=ip)
    except Exception as e:
        click.echo(f"[ERROR] server.init: {e}", err=True)
        raise click.exceptions.Exit(ExitCodes.UNEXPECTED)
    _exit_with_rc(rc, ok_msg="server.init: OK", err_msg="server.init failed")


# === vm ===

@cli.group()
def vm():
    """Управление виртуальными машинами."""
    pass


@vm.command("create")
@click.option(
    "--info-path", "info_path", required=True,
    type=click.Path(exists=True, dir_okay=False, readable=True),
    help="Путь до JSON-файла с конфигурацией ВМ",
)
@click.option(
    "--box", required=False, default="vm_station",
    help="Имя бокса (по умолчанию vm_station c 1.7.5.9 и 1.8.1.6 как snapshots)",
)
@click.option(
    "--rc", required=False,
    help="Версия РЦ/ОС только если вы указываете параметр box (например, 1.7.5.9 или 1.8.1.6)",
)
@click.option(
    "--kernel", required=False, default=None,
    help="Версия ядра только если вы указываете box (по умолчанию текущая на хосте)",
)
@click.option(
    "--new-password", required=False, default="1",
    help="Пароль, который будет установлен на ВМ после сборки",
)
def vm_create(info_path: str, rc: str | None, box: str | None, kernel: str | None, new_password: str):
    """Создание ВМ по конфигу."""
    if not box:
        box = "vm_station"
    if not kernel:
        try:
            kernel = SystemCommands.check_output_command("uname -r").strip()
        except Exception as e:
            click.echo(f"[ERROR] uname -r: {e}", err=True)
            raise click.exceptions.Exit(ExitCodes.UNEXPECTED)

    try:
        rc_code = Vm.create(info_path=info_path, box=box, rc=rc, kernel=kernel, new_password=new_password)
    except Exception as e:
        click.echo(f"[ERROR] vm.create: {e}", err=True)
        raise click.exceptions.Exit(ExitCodes.UNEXPECTED)

    _exit_with_rc(rc_code, ok_msg="vm.create: OK", err_msg="vm.create failed")


@vm.command("base-create")
@click.option(
    "--new-password", required=False, default="1",
    help="Пароль, который будет установлен на ВМ после сборки",
)
def vm_base_create(new_password: str):
    """Создание базовых ВМ."""
    try:
        rc_code = Vm.create(info_path="/opt/allta_vm/vm/base_vm.json", new_password=new_password)
    except Exception as e:
        click.echo(f"[ERROR] vm.base-create: {e}", err=True)
        raise click.exceptions.Exit(ExitCodes.UNEXPECTED)

    _exit_with_rc(rc_code, ok_msg="vm.base-create: OK", err_msg="vm.base-create failed")


@vm.command("delete")
@click.option(
    "--vms", multiple=True, required=True,
    help="Список ВМ для удаления (можно указать несколько раз)",
)
def vm_delete(vms: tuple[str, ...]):
    """Удаление ВМ и их snapshot'ов."""
    try:
        rc_code = Vm.delete(list(vms))
    except Exception as e:
        click.echo(f"[ERROR] vm.delete: {e}", err=True)
        raise click.exceptions.Exit(ExitCodes.UNEXPECTED)

    _exit_with_rc(rc_code, ok_msg="vm.delete: OK", err_msg="vm.delete failed")


@vm.command("update")
@click.option(
    "--info-path", "info_path", required=True,
    type=click.Path(exists=True, dir_okay=False, readable=True),
    help="Путь до JSON-файла с конфигурацией ВМ",
)
def vm_update(info_path: str):
    """Обновление ресурсов ВМ (CPU/RAM) по конфигу."""
    try:
        rc_code = Vm.update(info_path)
    except Exception as e:
        click.echo(f"[ERROR] vm.update: {e}", err=True)
        raise click.exceptions.Exit(ExitCodes.UNEXPECTED)

    _exit_with_rc(rc_code, ok_msg="vm.update: OK", err_msg="vm.update failed")


@vm.command("stop")
@click.option(
    "--vms", multiple=True, required=True,
    help="Список ВМ для отключения (можно указать несколько раз)",
)
def vm_stop(vms: tuple[str, ...]):
    """Жёсткая остановка ВМ (destroy)."""
    try:
        rc_code = Vm.stop(list(vms))
    except Exception as e:
        click.echo(f"[ERROR] vm.stop: {e}", err=True)
        raise click.exceptions.Exit(ExitCodes.UNEXPECTED)

    _exit_with_rc(rc_code, ok_msg="vm.stop: OK", err_msg="vm.stop failed")


@vm.command("start")
@click.option(
    "--vms", multiple=True, required=True,
    help="Список ВМ для включения (можно указать несколько раз)",
)
def vm_start(vms: tuple[str, ...]):
    """Запуск ВМ."""
    try:
        rc_code = Vm.start(list(vms))
    except Exception as e:
        click.echo(f"[ERROR] vm.start: {e}", err=True)
        raise click.exceptions.Exit(ExitCodes.UNEXPECTED)

    _exit_with_rc(rc_code, ok_msg="vm.start: OK", err_msg="vm.start failed")


@vm.command("astra-update")
@click.option(
    "--info-path", "info_path", required=True,
    type=click.Path(exists=True, dir_okay=False, readable=True),
    help="Путь до JSON-файла с конфигурацией ВМ",
)
@click.option(
    "--rc", required=False,
    help="Целевая версия ОС/лейбл снапшота; если логика использует имя снапшота — передайте его сюда",
)
@click.option(
    "--new-password", required=False, default="1",
    help="Пароль, который будет установлен на ВМ (если логика его использует)",
)

def vm_astra_update(info_path: str, rc: str | None, new_password: str, reboot: bool = True):
    """
    Запуск astra-update на ВМ и создание snapshot (в зависимости от реализации Vm.astra_update).
    Важно: команда возвращает код Vm.astra_update.
    """
    try:
        rc_code = Vm.astra_update(info_path=info_path, snapshot_name=rc, new_password=new_password)


    except Exception as e:
        click.echo(f"[ERROR] vm.astra-update: {e}", err=True)
        raise click.exceptions.Exit(ExitCodes.UNEXPECTED)

    _exit_with_rc(rc_code, ok_msg="vm.astra-update: OK", err_msg="vm.astra-update failed")


@vm.command("allta-update")
@click.option(
    "--payload-path", "payload_path", required=True,
    type=click.Path(exists=True, dir_okay=False, readable=True),
    help="Путь до JSON payload с VM/snapshot/password параметрами",
)
def vm_allta_update(payload_path: str):
    """Обновление allta-cli в гостевых ВМ по payload и пересоздание snapshot'ов."""
    try:
        rc_code = Vm.allta_update(payload_path=payload_path)
    except Exception as e:
        click.echo(f"[ERROR] vm.allta-update: {e}", err=True)
        raise click.exceptions.Exit(ExitCodes.UNEXPECTED)

    _exit_with_rc(rc_code, ok_msg="vm.allta-update: OK", err_msg="vm.allta-update failed")


# === snapshot ===

@cli.group()
def snapshot():
    """Управление снимками ВМ."""
    pass


@snapshot.command("create")
@click.option("--vms", multiple=True, required=True, help="Список ВМ")
@click.option("--name", "name", required=True, help="Имя snapshot'a")
def snapshot_create(vms: tuple[str, ...], name: str):
    """Создание snapshot'ов для указанных ВМ."""
    try:
        rc = Snapshot.create(list(vms), name)
    except Exception as e:
        click.echo(f"[ERROR] snapshot.create: {e}", err=True)
        raise click.exceptions.Exit(ExitCodes.UNEXPECTED)
    _exit_with_rc(rc, ok_msg="snapshot.create: OK", err_msg="snapshot.create failed")


@snapshot.command("delete")
@click.option("--vms", multiple=True, required=True, help="Список ВМ")
@click.option("--name", "name", required=True, help="Имя snapshot'a")
def snapshot_delete(vms: tuple[str, ...], name: str):
    """Удаление snapshot'ов у указанных ВМ."""
    try:
        rc = Snapshot.delete(list(vms), name)
    except Exception as e:
        click.echo(f"[ERROR] snapshot.delete: {e}", err=True)
        raise click.exceptions.Exit(ExitCodes.UNEXPECTED)
    _exit_with_rc(rc, ok_msg="snapshot.delete: OK", err_msg="snapshot.delete failed")


@snapshot.command("revert")
@click.option("--vms", multiple=True, required=True, help="Список ВМ")
@click.option("--name", "name", required=True, help="Имя snapshot'a")
def snapshot_revert(vms: tuple[str, ...], name: str):
    """Откат ВМ к указанному snapshot'у."""
    try:
        rc = Snapshot.revert(list(vms), name)
    except Exception as e:
        click.echo(f"[ERROR] snapshot.revert: {e}", err=True)
        raise click.exceptions.Exit(ExitCodes.UNEXPECTED)
    _exit_with_rc(rc, ok_msg="snapshot.revert: OK", err_msg="snapshot.revert failed")


if __name__ == "__main__":
    cli()

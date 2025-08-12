#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import click
from allta import SystemCommands
from allta_vm.commands.vm import Vm
from allta_vm.commands.snapshot import Snapshot
from allta_vm.commands.server import Server


@click.group()
def cli():
    """CLI для управления ВМ и снимками."""
    pass


# === group commands ===

@cli.group()
def server():
    """Инициализация и установка зависимостей"""
    pass


@cli.group()
def vm():
    """Управление виртуальными машинами"""
    pass


@cli.group()
def snapshot():
    """Управление снимками ВМ"""
    pass

# === server subcommands ===

@server.command("init")
@click.option("--phy-if",
              "phy_if",
              required=True,
              help = "Название сетевого интерфейса")
@click.option("--ip",
              "ip",
              required=True,
              help = "Ip сервера")
def init(phy_if, ip):
    """Установка всех зависимостей и настройка сети."""    
    Server.server_init(phy_if=phy_if, ip=ip)

# === vm subcommands ===

@vm.command("create")
@click.option(
    "--info-path",
    "info_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, readable=True),
    help="Путь до JSON-файла с конфигурацией ВМ",
)
@click.option(
    "--rc",
    required=True,
    help="Версия РЦ/ОС, которая будет установлена (например, 1.7.5.9 или 1.8.1.6)",
)
@click.option(
    "--box",
    required=False,
    default=None,
    help="Имя бокса (по умолчанию выбирается по rc)",
)
@click.option(
    "--kernel",
    required=False,
    default=None,
    help="Версия ядра (по умолчанию текущая на хосте)",
)
def vm_create(info_path: str, rc: str, box: str | None, kernel: str | None):
    """Создание ВМ по конфигу.""" 
    if not box:
            box = "vm_station"    
    if not kernel:
        try:
            kernel = SystemCommands.check_output_command("uname -r").strip()
        except Exception as e:
            click.echo(f"[!] Не удалось получить версию ядра: {e}", err=True)
            sys.exit(1)

    try:
        Vm.create(info_path=info_path, box=box, rc=rc, kernel=kernel)
    except Exception as e:
        click.echo(f"[!] Ошибка создания ВМ: {e}", err=True)
        sys.exit(1)
@vm.command("base-create")
def base_create():
    """Создание базовых ВМ."""
    Vm.create(info_path="/opt/allta_vm/vm/base_vm.json")
    pass

@vm.command("delete")
@click.option(
    "--vms",
    multiple=True,
    required=True,
    help="Список ВМ для удаления (можно указать несколько раз)",
)
def vm_delete(vms: tuple[str, ...]):
    """Удаление ВМ и их snapshot'ов."""
    try:
        Vm.delete(list(vms))
    except Exception as e:
        click.echo(f"[!] Ошибка удаления ВМ: {e}", err=True)
        sys.exit(1)


@vm.command("update")
@click.option(
    "--info-path",
    "info_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, readable=True),
    help="Путь до JSON-файла с конфигурацией ВМ",
)
def vm_update(info_path: str):
    """Обновление ресурсов ВМ (CPU/RAM) по конфигу."""
    try:
        Vm.update(info_path)
    except Exception as e:
        click.echo(f"[!] Ошибка обновления ВМ: {e}", err=True)
        sys.exit(1)


@vm.command("stop")
@click.option(
    "--vms",
    multiple=True,
    required=True,
    help="Список ВМ для отключения (можно указать несколько раз)",
)
def vm_stop(vms: tuple[str, ...]):
    """Жёсткая остановка ВМ (destroy)."""
    try:
        Vm.stop(list(vms))
    except Exception as e:
        click.echo(f"[!] Ошибка остановки ВМ: {e}", err=True)
        sys.exit(1)


@vm.command("start")
@click.option(
    "--vms",
    multiple=True,
    required=True,
    help="Список ВМ для включения (можно указать несколько раз)",
)
def vm_start(vms: tuple[str, ...]):
    """Запуск ВМ."""
    try:
        Vm.start(list(vms))
    except Exception as e:
        click.echo(f"[!] Ошибка запуска ВМ: {e}", err=True)
        sys.exit(1)


@vm.command("astra-update")
@click.option(
    "--info-path",
    "info_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, readable=True),
    help="Путь до JSON-файла с конфигурацией ВМ",
)
@click.option(
    "--rc",
    required=True,
    help="Целевая версия ОС для astra-update (например, 1.7.5.9 или 1.8.1.6)",
)
def astra_update(info_path: str, rc: str):
    """Запуск astra-update на ВМ и создание snapshot по целевой версии."""
    try:
        Vm.astra_update(info_path, rc)
    except Exception as e:
        click.echo(f"[!] Ошибка astra-update: {e}", err=True)
        sys.exit(1)


# === snapshot subcommands ===

@snapshot.command("create")
@click.option(
    "--vms",
    multiple=True,
    required=True,
    help="Список ВМ (можно указать несколько раз)",
)
@click.option(
    "--snapshot-name",
    "snapshot_name",
    required=True,
    help="Имя snapshot'a",
)
def snapshot_create(vms: tuple[str, ...], snapshot_name: str):
    """Создание snapshot'ов для указанных ВМ."""
    # Описание пока не используется в LibvirtManager.Snapshot.create,
    # но оставляем параметр для совместимости и будущего расширения.
    try:
        Snapshot.create(list(vms), snapshot_name)
    except Exception as e:
        click.echo(f"[!] Ошибка создания snapshot: {e}", err=True)
        sys.exit(1)


@snapshot.command("delete")
@click.option(
    "--vms",
    multiple=True,
    required=True,
    help="Список ВМ (можно указать несколько раз)",
)
@click.option(
    "--snapshot-name",
    "snapshot_name",
    required=True,
    help="Имя snapshot'a",
)
def snapshot_delete(vms: tuple[str, ...], snapshot_name: str):
    """Удаление snapshot'ов у указанных ВМ."""
    try:
        Snapshot.delete(list(vms), snapshot_name)
    except Exception as e:
        click.echo(f"[!] Ошибка удаления snapshot: {e}", err=True)
        sys.exit(1)


@snapshot.command("revert")
@click.option(
    "--vms",
    multiple=True,
    required=True,
    help="Список ВМ (можно указать несколько раз)",
)
@click.option(
    "--snapshot-name",
    "snapshot_name",
    required=True,
    help="Имя snapshot'a",
)
def snapshot_revert(vms: tuple[str, ...], snapshot_name: str):
    """Откат ВМ к указанному snapshot'у."""
    try:
        Snapshot.revert(list(vms), snapshot_name)
    except Exception as e:
        click.echo(f"[!] Ошибка отката к snapshot: {e}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    cli()

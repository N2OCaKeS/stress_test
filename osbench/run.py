#!/bin/python3

import argparse

from os import path

from src.osb_logger import log
log.setup(
    name="OSBench",
    log_file=f"{path.dirname(path.abspath(__file__))}/logs/osbench.log",
    log_level="DEBUG",
    console=True,
    colored_console=True
)

from src.lib import system
from config.conf import VENV_PATH






parser = argparse.ArgumentParser()
group = parser.add_mutually_exclusive_group(required=True)
group.add_argument('-tc', '--test-colors', 
                   action='store_true', 
                   help='check colors',
                   dest='COLORS')

group.add_argument('-p', '--prepare',
                    action='store_true',
                    required=False,
                    help='prepare',
                    dest='PREP')

group.add_argument('-r', '--run',
                    action='store_true',
                    required=False,
                    help='run tests',
                    dest='RUN')
args = parser.parse_args()

if args.COLORS:
    log.debug("Отладка - голубой")
    log.info("Информация - зелёный")
    log.warning("Предупреждение - жёлтый")
    log.error("Ошибка - красный")
    log.critical("Критическая ошибка - ярко-красный")

if args.PREP:
    log.info("Настройка окружения\n")
    system.leave_command(f'sudo bash scripts/prepare.sh')
    log.info("\nНастройка бенчмарков\n")
    system.leave_command(f'sudo bash scripts/install_bench.sh')
elif args.RUN:
    log.info("\nНачало тестирования\n")
    system.leave_command(f'sudo {VENV_PATH} src/osbench.py')


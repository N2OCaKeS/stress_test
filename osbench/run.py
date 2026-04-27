#!/bin/python3

import subprocess
import argparse

from os import path

from config.conf import VENV_PATH
from src.logger import log

log.setup(
    name="OSBench",
    log_file=f"{path.dirname(path.abspath(__file__))}/logs/osbench.log",
    log_level="DEBUG",
    console=True
)

parser = argparse.ArgumentParser()
group = parser.add_mutually_exclusive_group(required=True)
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


if args.PREP:
    log.info("Настройка окружения\n")
    subprocess.run(f'sudo bash scripts/prepare.sh', shell=True, check=True)
    log.info("\nНастройка бенчмарков\n")
    subprocess.run(f'sudo bash scripts/install_bench.sh', shell=True)
elif args.RUN:
    log.info("\nНачало тестирования\n")
    subprocess.run(f'sudo {VENV_PATH} src/osbench.py', shell=True)


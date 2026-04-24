#!/bin/python3

import subprocess
import argparse

from config.conf import VENV_PATH

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
    subprocess.run(f'sudo bash scripts/prepare.sh', shell=True, check=True)
    subprocess.run(f'sudo bash scripts/install_bench.sh', shell=True)
elif args.RUN:
    subprocess.run(f'sudo {VENV_PATH} src/osbench.py', shell=True)


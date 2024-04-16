#!/bin/python3

import subprocess
import argparse
from virt_conf import REPORT_PATH, VENV_PATH
from os import path, mkdir


parser = argparse.ArgumentParser()
parser.add_argument('-n',
                    action='store',
                    required=True,
                    help='dates name',
                    dest='NAME')

args = parser.parse_args()

if not path.isdir(REPORT_PATH):
    mkdir(REPORT_PATH)

with open(f'/home/u/{args.NAME}', 'r') as r:
        dates = r.read()


subprocess.run(f'sudo {VENV_PATH} test_run.py {dates}', shell=True)



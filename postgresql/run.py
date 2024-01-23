#!/bin/python3

import subprocess
import argparse
from psb_conf import LOG_FILENAME, REPORT_PATH, RUN_LOG, VENV_PATH
from os import path, mkdir


parser = argparse.ArgumentParser()
parser.add_argument('-n',
                    action='store',
                    required=True,
                    help='dates name',
                    dest='NAME')

parser.add_argument('-kn',
                    action='store',
                    required=False,
                    help='kernel mode',
                    dest='KERNEL')

parser.add_argument('-bl',
                    action='store',
                    required=False,
                    help='balance mode',
                    dest='BALANCE')

args = parser.parse_args()

if not path.isdir(REPORT_PATH):
    mkdir(REPORT_PATH)

with open(f'/home/u/{args.NAME}', 'r') as r:
        dates = r.read()

if args.KERNEL:
    subprocess.run(f'sudo {VENV_PATH} diff_kernel_quantity.py {dates}', shell=True)
elif args.BALANCE:
     subprocess.run(f'sudo {VENV_PATH} bl_run.py {dates}', shell=True)
else:
    #subprocess.run(f'sudo perf record -a -g -F 99 venv/bin/python3 psb_run.py {dates}', shell=True)
    subprocess.run(f'sudo {VENV_PATH} psb_run.py {dates}', shell=True)
    subprocess.run(f'sudo {VENV_PATH} psb_public.py', shell=True)


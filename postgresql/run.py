#!/bin/python3

import subprocess
import argparse
from psb_conf import LOG_FILENAME, REPORT_PATH, RUN_LOG
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

if args.KERNEL:
    subprocess.run(f'sudo python3 diff_kernel_quantity.py {dates}', shell=True)
else:
    #subprocess.run(f'sudo perf record -a -g -F 99 venv/bin/python3 psb_run.py {dates}', shell=True)
    subprocess.run(f'sudo python3 psb_run.py {dates}', shell=True)
    subprocess.run('sudo python3 psb_public.py', shell=True)


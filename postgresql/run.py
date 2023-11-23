#!/bin/python3

import subprocess
import argparse
import logging
from psb_conf import LOG_FILENAME, REPORT_PATH, RUN_LOG
from os import path, mkdir
from libs.libpsb import check_output_command, logging
from time import sleep
from sys import exit

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

args = parser.parse_args()

if not path.isdir(REPORT_PATH):
    mkdir(REPORT_PATH)

if args.KERNEL:
    subprocess.run(f'sudo python3 diff_kernel_quantity.py -q {args.KERNEL} -st 3', shell=True)
else:
    with open(f'/home/u/{args.NAME}', 'r') as r:
        dates = r.read()

    #subprocess.run(f'sudo perf record -a -g -F 99 venv/bin/python3 psb_run.py {dates}', shell=True)
    subprocess.run(f'sudo python3 psb_run.py {dates}', shell=True)
    #holder_transaction('sudo python3 psb_public.py')
    subprocess.run('sudo python3 psb_public.py', shell=True)


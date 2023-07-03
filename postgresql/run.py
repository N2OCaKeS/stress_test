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
args = parser.parse_args()

if not path.isdir(REPORT_PATH):
    mkdir(REPORT_PATH)

# def holder_transaction(command):
#     holder = 0
#     while holder < 1:
#         try:
#             subprocess.run(command, shell=True, check=True)
#             break
#         except Exception as e:
#             holder += 1
#             logging.error('-----' * 30)
#             logging.error('Fail #', holder)
#             logging.error(e)
#             logging.error('-----' * 30)
#             sleep(60)
#             if holder == 1:
#                 logging.error('Скрипт остановлен так как истек период ожидания:', holder, 'попытки')
#                 exit(2)

with open(f'/home/u/{args.NAME}', 'r') as r:
    dates = r.read()

# subprocess.run('sudo bash psb_db_del.sh', shell=True)

#subprocess.run(f'sudo perf record -a -g -F 99 venv/bin/python3 psb_run.py {dates}', shell=True)
subprocess.run(f'sudo python3 psb_run.py {dates}', shell=True)
#holder_transaction('sudo python3 psb_public.py')
subprocess.run('sudo python3 psb_public.py', shell=True)


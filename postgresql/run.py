#!/bin/python3

import subprocess
import argparse
import logging
from psb_conf import LOG_FILENAME, REPORT_PATH, RUN_LOG
from os import path, mkdir
from libs.libpsb import check_output_command
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

logging.basicConfig(
        filename=RUN_LOG, 
        level=logging.INFO,
        filemode='a',
        format='%(asctime)s - %(levelname)s - %(name)s - %(funcName)s: %(lineno)d - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
)

def holder_transaction(command):
    holder = 0
    while holder < 30:
        try:
            subprocess.run(command, shell=True, check=True)
            break
        except Exception as e:
            holder += 1
            logging.error('-----' * 30)
            logging.error('Fail #', holder)
            logging.error(e)
            logging.error('-----' * 30)
            sleep(60)
            if holder == 30:
                logging.error('Скрипт остановлен так как истек период ожидания:', holder, 'минут')
                exit(2)

with open(f'/home/u/{args.NAME}', 'r') as r:
    dates = r.read()

logging.info(check_output_command('sudo bash psb_db_del.sh', out=True))

holder_transaction(f'sudo perf record -a -g -F 99 venv/bin/python3 psb_run.py {dates}')
holder_transaction('sudo venv/bin/python3 psb_public.py')


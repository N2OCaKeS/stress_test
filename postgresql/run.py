#!/bin/python3

import subprocess
import argparse
import logging
from psb_conf import LOG_FILENAME, REPORT_PATH
from os import path, mkdir
from libs.libpsb import check_output_command

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
        filename=LOG_FILENAME, 
        level=logging.INFO,
        filemode='a',
        format='%(asctime)s - %(levelname)s - %(name)s - %(funcName)s: %(lineno)d - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
)

with open(f'/home/u/{args.NAME}', 'r') as r:
    dates = r.read()

logging.info(check_output_command('sudo bash psb_db_del.sh', out=True))
logging.info(check_output_command(f'sudo perf record -a -g -F 99 venv/bin/python3 psb_run.py {dates}'))
logging.info(check_output_command(f'sudo venv/bin/python3 psb_public.py'))

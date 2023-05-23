#!/bin/python3

import subprocess
import argparse

parser = argparse.ArgumentParser()
parser.add_argument('-n',
                    action='store',
                    required=True,
                    help='dates name',
                    dest='NAME')
args = parser.parse_args()

with open(f'/home/u/{args.NAME}', 'r') as r:
    dates = r.read()

subprocess.run('sudo bash psb_db_del.sh', shell=True)
subprocess.run(f'sudo perf record -a -g -F 99 venv/bin/python3 psb_run.py {dates}', shell=True)
subprocess.run(f'sudo venv/bin/python3 psb_public.py', shell=True)

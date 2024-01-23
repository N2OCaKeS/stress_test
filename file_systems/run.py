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
venv_path = '/home/u/python/Python-3.12.1/venv/bin/python3.12'

with open(f'/home/u/{args.NAME}', 'r') as r:
    dates = r.read()

subprocess.run(f'sudo {venv_path} fsb_run.py {dates}', shell=True)

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

subprocess.run(f'sudo venv/bin/python3 run.py {dates}', shell=True)

#!/bin/python3

import subprocess

with open('/home/u/dates.conf', 'r') as r:
    dates = r.read()

subprocess.run(f'sudo perf record -a -g -F 99 venv/bin/python3 psb_run.py {dates}', shell=True)
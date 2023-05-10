#!/bin/python3

import subprocess

with open('/home/u/dates.txt', 'r') as r:
    dates = r.read()

subprocess.run(f'sudo venv/bin/python3 fsb_run.py {dates}', shell=True)
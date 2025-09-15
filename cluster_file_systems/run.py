#!/bin/python3

import subprocess
import argparse
from cfs_conf import VENV_PATH

parser = argparse.ArgumentParser()
parser.add_argument('-n',
                    action='store',
                    required=True,
                    help='dates name',
                    dest='NAME')

parser.add_argument('--cfs',
                    action='store',
                    choices=['ceph','ocfs2'],
                    default='ocfs2',
                    required=False,
                    dest='CFS')

args = parser.parse_args()

with open(f'/home/u/{args.NAME}', 'r') as r:
    dates = r.read()

if args.CFS == "ceph":
    subprocess.run(f'sudo {VENV_PATH} cfs_run.py {dates}', shell=True)
else:
    subprocess.run(f'sudo {VENV_PATH} cfs_run_ceph.py {dates}', shell=True)
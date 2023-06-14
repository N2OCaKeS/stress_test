#!/bin/python3

import subprocess
import argparse
import os

parser = argparse.ArgumentParser()
parser.add_argument('-n',
                    action='store',
                    required=True,
                    help='dates name',
                    dest='NAME')
args = parser.parse_args()

def check_output_command(command):
    result = subprocess.Popen([command], shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True)
    output, errors = result.communicate()
    output = os.linesep.join([s for s in output.splitlines() if s])
    errors = os.linesep.join([s for s in errors.splitlines() if s])
    if errors == "":
        return output
    else:
        return errors

with open(f'/home/u/{args.NAME}', 'r') as r:
    dates = r.read()

ps = check_output_command("sudo ps aux | grep 'sudo /home/u/starter.sh auditd dates_stand1.conf' | \
                          sed -n 1p | awk '{print $2}'", shell=True)
subprocess.run(f'sudo kill -9 {ps}')
subprocess.run(f'sudo venv/bin/python3 aub_run.py {dates}', shell=True)

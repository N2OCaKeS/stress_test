import subprocess
import argparse
from conf import VENV_PATH

parser = argparse.ArgumentParser()
parser.add_argument('-n',
                    action='store',
                    required=True,
                    help='dates name',
                    dest='NAME')
args = parser.parse_args()

with open(f'/home/u/{args.NAME}', 'r') as r:
    dates = r.read()

try:
    subprocess.run(f'sudo {VENV_PATH} ovpn_run.py {dates}', shell=True)
except Exception as e:
    print(e)
import subprocess
import argparse
from ovpn_conf import VENV_PATH

parser = argparse.ArgumentParser()
parser.add_argument('-n',
                    action='store',
                    required=True,
                    help='dates name',
                    dest='NAME')
args = parser.parse_args()

with open(f'/home/askeladd/git/stress_test/{args.NAME}', 'r') as r:
    dates = r.read()

subprocess.run(f'sudo {VENV_PATH} ovpn20k_run.py {dates}', shell=True)
import subprocess
import argparse
from ps_conf import REPORT_PATH, VENV_PATH, DIGSIG_NAME
from os import path, mkdir



subprocess.run(f'sudo bash prepare.sh parsec 1.7.5', shell=True)
subprocess.run(f'sudo {VENV_PATH} digsig/digsig_test.py -ph {DIGSIG_NAME}', shell=True)



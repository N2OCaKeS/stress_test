from allta import SystemCommands
from psb_conf import VENV_PATH

SystemCommands.check_output_command(f'source {VENV_PATH} && cd ./new_balance && python main.py')
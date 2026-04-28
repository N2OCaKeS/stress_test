from os import path


VENV_PATH = '/home/u/python/Python-3.12.1/venv/bin/python3.12'
MAIN_DIR = path.normpath(path.join(path.dirname(path.abspath(__file__)), '..'))


#################################################################################
# UNIXBENCH                                                                     #
#################################################################################
LOW_CONC = 4
HIGH_CONC = 25
STEP = 8



# -*- coding: utf-8 -*-

# ;===========================================================
# ; Author: ivelikanov@astralinux.ru
# ; Date: 2022
# ;===========================================================

import re
import shutil
import os.path
import argparse
import subprocess
import numpy as np
import pandas as pd
import libs.libtable as libtable
import libs.libscanner as libscanner

from time import sleep, ctime
from datetime import datetime
from sklearn import preprocessing
from os import chmod, mkdir, getcwd, path
from find_err_in_logs import collecting_logs
from libs.libsng import (astra_version, 
                         check_service_status, 
                         get_memory_load_by_syslog, 
                         put_system_info_in_file, 
                         upload_results_to_ftp,
                         response)
from libs.zefir import UploaderZC
from libs.libpublic import Public
from sng_conf import SERVICE_COUNT, TIME_EXEC, TIME_EXEC_ST3_ST4, REPORT_PATH, IMAGE_WIDTH, IMAGE_HEIGHT, INFO_FILENAME, REPORT_FILENAME, VENV_PATH


# TIME_START_SCRIPT = datetime.now()

DESCRIPTION = ""
parser = argparse.ArgumentParser(description=DESCRIPTION)
parser.add_argument('-ll', '--log_level',
                    action='store',
                    required=False,
                    choices=['debug',
                             'info',
                             'notice',
                             'warn',
                             'err',
                             'crit',
                             'emerg',
                             'crit..emerg',
                             'err..emerg',
                             'warn..emerg',
                             'notice..emerg',
                             'info..emerg',
                             'debug..emerg'],
                    default='debug',
                    help='log level for syslog-ng',
                    dest='LOG_LEVEL')

parser.add_argument('-u', '--username',
                    action='store',
                    required=True,
                    help='confluence user',
                    dest='USER')

parser.add_argument('-t', '--token',
                    action='store',
                    required=False,
                    default=None,
                    help='confluence access token',
                    dest='TOKEN')

parser.add_argument('-cs', '--confluence-space',
                    action='store',
                    required=True,
                    help='confluence space',
                    dest='SPACE')

parser.add_argument('-cpp', '--confluence-parent-page',
                    action='store',
                    required=True,
                    help='confluence parent page',
                    dest='PPAGE')

parser.add_argument('-cnp', '--confluence-new-page',
                    action='store',
                    required=True,
                    help='confluence new page',
                    dest='NPAGE')

parser.add_argument('-sn', '--stand-num',
                    action='store',
                    choices=['1',
                             '2',
                             '3',
                             '4'],
                    required=True,
                    help='stand num',
                    dest='STAND')

parser.add_argument('-fti', '--folder-tree-id',
                    action='store',
                    required=True,
                    help='folder-tree-id',
                    dest='FTI')

parser.add_argument('-tcyc', '--test-cycle-name',
                    action='store',
                    required=True,
                    help='test-cycle-name',
                    dest='TCYC')

parser.add_argument('-tcas', '--test-case-name',
                    action='store',
                    required=True,
                    help='test-case-name',
                    dest='TCAS')

parser.add_argument('-ba', '--basic-auth',
                    action='store',
                    required=True,
                    help='basic-auth',
                    dest='BA')

parser.add_argument('-tcv', '--test-cycle-version',
                    action='store',
                    required=True,
                    help='test-cycle-version',
                    dest='TCV')
args = parser.parse_args()

def cmd(command):
    subprocess.run(command,
                   shell=True,
                   stderr=subprocess.DEVNULL)


if __name__ == '__main__':

    uzs = UploaderZC(folder_tree_id=args.FTI,
                     test_cycle_name=args.TCYC,
                     test_case_name=args.TCAS,
                     basic_auth=args.BA,
                     test_cycle_version=args.TCV,
                     token=args.TOKEN,
                     username=args.USER,
                     conf_space=args.SPACE,
                     conf_parent_page=args.PPAGE,
                     conf_new_page_name=args.NPAGE,
                     grade_stand=args.STAND)
    
    uzs.upload_test_cycle_status('progress')
            
    """
        TODO Здесь запускаем тесты
    """

    uzs.public = True
    uzs.statistics = True
    uzs.upload_test_cycle_status(zefir_status='pass')
               
if path.isfile('libs/zefir.log'):
    with open('libs/zefir.log', 'r') as r:
        zefir_log = r.read()
        print('\n\n\nZefir-log\n')
        print(zefir_log)
if path.isfile('JIRA_ERROR.log'):
    with open('JIRA_ERROR.log', 'r') as r:
        jira_log = r.read()
        print('\n\n\nJira-log\n')
        print(jira_log)

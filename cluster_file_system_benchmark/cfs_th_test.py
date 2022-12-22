# -*- coding: UTF-8 -*-

# ;===========================================================
# ; Author: rkuznetsov@astralinux.ru
# ; Date: 2022
# ;===========================================================


import argparse
import logging
from time import time
from libs.libtests import Test, TestSet
from libs.libtable import Report
from cfs_conf import START_BORDER_FOR_DATA, STEP_FOR_DATA, \
    END_BORDER_FOR_DATA, TIMEOUT, NUMBER_OF_TEST_FILES, LOG_FILENAME, \
    FILES, FILES_STEP, FILES_LIMIT, \
    SIZE, SIZE_STEP, SIZE_LIMIT

DESCRIPTION = ""
parser = argparse.ArgumentParser(description=DESCRIPTION)
parser.add_argument('--test-set',
                    action='store',
                    choices=['base_load',
                             'timeout',
                             'multithreaded',
                             'big_files',
                             'fs_mark_count',
                             'fs_mark_size'],
                    required=True,
                    dest='TS')

parser.add_argument('--parsec',
                    action='store_true',
                    required=False,
                    help='',
                    dest='PARSEC')

args = parser.parse_args()

logging.basicConfig(filename=LOG_FILENAME,
                    filemode="a+",
                    level=logging.INFO,
                    format='%(levelname)s: t:%(created)f th:%(thread)d ps:%(process)d <%(name)s> | %(message)s')
log = logging.getLogger()

if args.TS == 'fs_mark_count':
    '''    
        Прогон 5.
        fs_mark
    '''
    # Изменение количества файлов
    run_test = TestSet(start_burder=FILES,
                       end_burder=FILES_LIMIT,
                       step=FILES_STEP)
    try:
        run_test.test_7_fs_mark33_count()
    except Exception as exeption:
        log.info(exeption)

if args.TS == 'fs_mark_size':
    '''    
        Прогон 6.
        fs_mark
    '''
    # Изменение размера файлов
    run_test = TestSet(start_burder=SIZE,
                       end_burder=SIZE_LIMIT,
                       step=SIZE_STEP)
    try:
        run_test.test_8_fs_mark33_size()
    except Exception as exeption:
        log.info(exeption)

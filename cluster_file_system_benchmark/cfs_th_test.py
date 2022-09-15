# -*- coding: UTF-8 -*-

# ;===========================================================
# ; Author: rkuznetsov@astralinux.ru
# ; Date: 2022
# ;===========================================================

import time
import argparse
from libs.libtests import Test, TestSet
from cfs_conf import START_BORDER_FOR_DATA, STEP_FOR_BORDER, \
    END_BORDER_FOR_DATA, TIMEOUT, NUMBER_OF_TEST_FILES

DESCRIPTION = ""
parser = argparse.ArgumentParser(description=DESCRIPTION)
parser.add_argument('-v',
                    action='store',
                    choices=['files',
                             'symlinks',
                             'hardlinks',
                             'archs',
                             'isos'],
                    required=True,
                    help='variant',
                    dest='VARIANT')

args = parser.parse_args()

excute_time = 0

if args.VARIANT == 'files':
    while excute_time < TIMEOUT:
        start_time = time.time()
        assert Test.file_filling(count=NUMBER_OF_TEST_FILES,
                                 start=START_BORDER_FOR_DATA,
                                 end=END_BORDER_FOR_DATA,
                                 step=STEP_FOR_BORDER) is True
        end_time = time.time()
        excute_time += end_time - start_time

if args.VARIANT == 'symlinks':
    while excute_time < TIMEOUT:
        start_time = time.time()
        assert Test.symlink_filling(count=NUMBER_OF_TEST_FILES,
                                    start=START_BORDER_FOR_DATA,
                                    end=END_BORDER_FOR_DATA,
                                    step=STEP_FOR_BORDER) is True
        end_time = time.time()
        excute_time += end_time - start_time

if args.VARIANT == 'hardlinks':
    while excute_time < TIMEOUT:
        start_time = time.time()
        assert Test.hardlink_filling(count=NUMBER_OF_TEST_FILES,
                                     start=START_BORDER_FOR_DATA,
                                     end=END_BORDER_FOR_DATA,
                                     step=STEP_FOR_BORDER) is True
        end_time = time.time()
        excute_time += end_time - start_time

if args.VARIANT == 'archs':
    while excute_time < TIMEOUT:
        start_time = time.time()
        assert Test.arch_filling(count=NUMBER_OF_TEST_FILES,
                                 start=START_BORDER_FOR_DATA,
                                 end=END_BORDER_FOR_DATA,
                                 step=STEP_FOR_BORDER) is True
        end_time = time.time()
        excute_time += end_time - start_time

if args.VARIANT == 'isos':
    while excute_time < TIMEOUT:
        start_time = time.time()
        assert Test.iso_filling(count=NUMBER_OF_TEST_FILES,
                                start=START_BORDER_FOR_DATA,
                                end=END_BORDER_FOR_DATA,
                                step=STEP_FOR_BORDER) is True
        end_time = time.time()
        excute_time += end_time - start_time

# -*- coding: UTF-8 -*-

# ;===========================================================
# ; Author: vgusev@astralinux.ru
# ; Date: 2022
# ;===========================================================

import argparse
from libs.libtest import TestSet



parser = argparse.ArgumentParser(description="DESCRIPTION")
parser.add_argument('-m', '--mode',
                    action='store',
                    required=False,
                    choices=['default',
                             'extended',],
                    default='default',
                    help='help me',
                    dest='MODE')
args = parser.parse_args()
if args.MODE == 'default':
    TestSet.test_set1()
elif args.MODE == 'extended':
    print("In developing")

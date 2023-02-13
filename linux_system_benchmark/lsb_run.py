# -*- coding: UTF-8 -*-

# ;===========================================================
# ; Author: vgusev@astralinux.ru
# ; Date: 2022
# ;===========================================================

import argparse
import subprocess

from os import path, chdir, listdir, remove
from lsb_conf import STAND1_LOWER_LIMIT, STAND1_UPPER_LIMIT, STAND1_STEP, \
    STAND2_LOWER_LIMIT, STAND2_UPPER_LIMIT, STAND2_STEP, \
    STAND3_LOWER_LIMIT, STAND3_UPPER_LIMIT, STAND3_STEP, \
    STAND4_LOWER_LIMIT, STAND4_UPPER_LIMIT, STAND4_STEP


parser = argparse.ArgumentParser(description="DESCRIPTION")
parser.add_argument('-m', '--mode',
                    action='store',
                    required=False,
                    choices=['default',
                             'extended',],
                    default='default',
                    help='help me',
                    dest='MODE')

parser.add_argument('-sn', '--stand-name',
                    action='store',
                    required=False,
                    choices=['stand1',
                             'stand2',
                             'stand3',
                             'stand4',],
                    help='',
                    dest='STAND')

args = parser.parse_args()


def cmd(command):
    subprocess.run(command,
                   shell=True,
                   stderr=subprocess.DEVNULL)


if args.MODE == 'default':
    # определить текущую директрию
    current_dir = path.dirname(path.realpath(__file__))

    # собираем проект
    # chdir(current_dir + '/byte-unixbench-master/UnixBench/')
    # cmd('make')

    # очистить файлы /results
    result_dir = current_dir + '/byte-unixbench-master/UnixBench/results/'
    for file in listdir(result_dir):
        remove(result_dir + file)

    if args.STAND == 'stand1':  # итеративный проход stand1
        parallel_processes = ['-c ' + str(proc) for proc in range(STAND1_LOWER_LIMIT, STAND1_UPPER_LIMIT, STAND1_STEP)]
        cmd_parallel_processes = ' '.join(parallel_processes)
        # print(cmd_parallel_processes)

        chdir(current_dir + '/byte-unixbench-master/UnixBench/')
        cmd('./Run ' + cmd_parallel_processes)

    if args.STAND == 'stand2':  # итеративный проход stand2
        parallel_processes = ['-c ' + str(proc) for proc in range(STAND2_LOWER_LIMIT, STAND2_UPPER_LIMIT, STAND2_STEP)]
        cmd_parallel_processes = ' '.join(parallel_processes)
        # print(cmd_parallel_processes)

        chdir(current_dir + '/byte-unixbench-master/UnixBench/')
        cmd('./Run ' + cmd_parallel_processes)

    if args.STAND == 'stand3':  # итеративный проход stand3
        parallel_processes = ['-c ' + str(proc) for proc in range(STAND3_LOWER_LIMIT, STAND3_UPPER_LIMIT, STAND3_STEP)]
        cmd_parallel_processes = ' '.join(parallel_processes)
        # print(cmd_parallel_processes)

        chdir(current_dir + '/byte-unixbench-master/UnixBench/')
        cmd('./Run ' + cmd_parallel_processes)

    if args.STAND == 'stand4':  # итеративный проход stand4
        parallel_processes = ['-c ' + str(proc) for proc in range(STAND4_LOWER_LIMIT, STAND4_UPPER_LIMIT, STAND4_STEP)]
        cmd_parallel_processes = ' '.join(parallel_processes)
        # print(cmd_parallel_processes)

        chdir(current_dir + '/byte-unixbench-master/UnixBench/')
        cmd('./Run ' + cmd_parallel_processes)


elif args.MODE == 'extended':
    print("In developing")

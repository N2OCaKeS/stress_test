# -*- coding: UTF-8 -*-

# ;===========================================================
# ; Author: vgusev@astralinux.ru
# ; Date: 2022
# ;===========================================================

import argparse
import subprocess
import pysnooper
from time import time
from shutil import copy
from os import path, chdir, listdir, remove, mkdir
from libs.liblsb import cmd, put_system_info_in_file, upload_result
from libs.lsbtable import Report
from lsb_conf import INFO_FILENAME, \
    LOG_DIR, REPORT_DIR, \
    STAND1_LOWER_LIMIT, STAND1_UPPER_LIMIT, STAND1_STEP, \
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
                    required=True,
                    choices=['stand1',
                             'stand2',
                             'stand3',
                             'stand4',],
                    help='stand name',
                    dest='STAND')

args = parser.parse_args()

@pysnooper.snoop()
def main():
    if args.MODE == 'default':
        # определить текущую директрию
        current_dir = path.dirname(path.realpath(__file__))

        # Создать /report
        if not path.exists(REPORT_DIR):
            mkdir(REPORT_DIR, mode=0o755)

        # Создать /log
        if not path.exists(LOG_DIR):
            mkdir(LOG_DIR, mode=0o755)

        # собираем проект
        chdir(current_dir + '/byte-unixbench-master/UnixBench/')
        cmd('make')

        # очистить файлы /results
        try:
            result_dir = current_dir + '/byte-unixbench-master/UnixBench/results/'
            for file in listdir(result_dir):
                remove(result_dir + file)
        except FileNotFoundError:
            pass

        # Засечь время выполнения скрипта
        start_time = time()

        '''
            stand1
        '''
        if args.STAND == 'stand1':  # итеративный проход stand1
            parallel_processes = ['-c ' + str(proc) for proc in range(STAND1_LOWER_LIMIT, STAND1_UPPER_LIMIT, STAND1_STEP)]
            cmd_parallel_processes = ' '.join(parallel_processes)

            # запустить тест
            chdir(current_dir + '/byte-unixbench-master/UnixBench/')
            cmd('./Run ' + cmd_parallel_processes)

            # выгрузить результаты
            upload_result(current_dir)

            # Собираем результаты
            r = Report(STAND1_LOWER_LIMIT,
                    STAND1_UPPER_LIMIT,
                    STAND1_STEP)
            r.create_all_graphs()
            r.get_all_ratings()
            r.create_tar()

        '''
            stand2
        '''
        if args.STAND == 'stand2':  # итеративный проход stand2
            parallel_processes = ['-c ' + str(proc) for proc in range(STAND2_LOWER_LIMIT, STAND2_UPPER_LIMIT, STAND2_STEP)]
            cmd_parallel_processes = ' '.join(parallel_processes)

            # запустить тест
            chdir(current_dir + '/byte-unixbench-master/UnixBench/')
            cmd('./Run ' + cmd_parallel_processes)

            # выгрузить результаты
            upload_result(current_dir)

            # Собираем результаты
            r = Report(STAND2_LOWER_LIMIT,
                    STAND2_UPPER_LIMIT,
                    STAND2_STEP)
            r.create_all_graphs()
            r.get_all_ratings()
            r.create_tar()

        '''
            stand3
        '''
        if args.STAND == 'stand3':  # итеративный проход stand3
            parallel_processes = ['-c ' + str(proc) for proc in range(STAND3_LOWER_LIMIT, STAND3_UPPER_LIMIT, STAND3_STEP)]
            cmd_parallel_processes = ' '.join(parallel_processes)

            # запустить тест
            chdir(current_dir + '/byte-unixbench-master/UnixBench/')
            cmd('./Run ' + cmd_parallel_processes)

            # выгрузить результаты
            upload_result(current_dir)

            # Собираем результаты
            r = Report(STAND3_LOWER_LIMIT,
                    STAND3_UPPER_LIMIT,
                    STAND3_STEP)
            r.create_all_graphs()
            r.get_all_ratings()
            r.create_tar()

        '''
            stand4
        '''
        if args.STAND == 'stand4':  # итеративный проход stand4
            parallel_processes = ['-c ' + str(proc) for proc in range(STAND4_LOWER_LIMIT, STAND4_UPPER_LIMIT, STAND4_STEP)]
            cmd_parallel_processes = ' '.join(parallel_processes)

            # запустить тест
            chdir(current_dir + '/byte-unixbench-master/UnixBench/')
            cmd('./Run ' + cmd_parallel_processes)

            # выгрузить результаты
            upload_result(current_dir)

            # Собираем результаты
            r = Report(STAND4_LOWER_LIMIT,
                    STAND4_UPPER_LIMIT,
                    STAND4_STEP)
            r.create_all_graphs()
            r.get_all_ratings()
            r.create_tar()

        put_system_info_in_file(start_time, current_dir+'/report/'+INFO_FILENAME)

    elif args.MODE == 'extended':
        print("In developing")

main()
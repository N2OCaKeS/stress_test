# -*- coding: UTF-8 -*-

# ;===========================================================
# ; Author: vgusev@astralinux.ru
# ; Date: 2022
# ;===========================================================

import argparse
import subprocess
import pysnooper
from time import time, ctime, sleep
from shutil import copy
from os import path, chdir, listdir, remove, mkdir
from libs.liblsb import cmd, put_system_info_in_file, upload_result, response, upload_results_to_ftp
from libs.zefir import ZefirStatusAPI, ZefirResultTable
from libs.lsbtable import Report
from libs.libpublic import Public
from lsb_conf import INFO_FILENAME, \
    LOG_DIR, REPORT_DIR, REPORT_FILENAME, \
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

@pysnooper.snoop()
def main():

    def test_cycle_status_start():
        zefir = ZefirStatusAPI(folder_tree_id=args.FTI,
                                test_cycle_name=args.TCYC,
                                test_case_name=args.TCAS,
                                basic_auth=args.BA)
        zefir.upload_status(90)
        zefir_table = ZefirResultTable(test_cycle_version=args.TCV,
                                        token=args.TOKEN,
                                        basic_auth=args.BA,
                                        username=args.USER)
        zefir_table

    start_status = 0
    while start_status == 0:
        jira_start, life_start = response()
        try:
            if jira_start == 200 and life_start == 200:
                test_cycle_status_start()
                start_status += 1
            else: 
                with open('JIRA_ERROR.log', 'a') as err:
                    err.write('start:\n')
                    err.write(ctime())
                    err.write(f'jira_status = {jira_start}\nlife_status = {life_start}')
                    err.write('---------' * 25)
                    err.write('\n\n')
                sleep(60)
        except Exception as e:
            with open('JIRA_ERROR.log', 'a') as err:
                err.write('start:\n')
                err.write(ctime())
                err.write(str(e))
                err.write('---------' * 25)
                err.write('\n\n')
                start_status += 1


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
        if args.STAND == '1':  # итеративный проход stand1
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
        if args.STAND == '2':  # итеративный проход stand2
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
        if args.STAND == '3':  # итеративный проход stand3
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
        if args.STAND == '4':  # итеративный проход stand4
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

    upload_results_to_ftp(args.TCV, f'{REPORT_DIR}/{REPORT_FILENAME}', f'syslog-ng_{args.TCYC}_{REPORT_FILENAME}')

    def upload_result_status():
        public = Public(username=args.USER,
                        token=args.TOKEN,
                        conf_space=args.SPACE,
                        conf_parent_page=args.PPAGE,
                        conf_new_page_name=args.NPAGE,
                        grade_stand=args.STAND)

        public.run_publish()

        zefir = ZefirStatusAPI(folder_tree_id=args.FTI,
                                test_cycle_name=args.TCYC,
                                test_case_name=args.TCAS,
                                basic_auth=args.BA)
        zefir.upload_status(91)

        zefir_table = ZefirResultTable(test_cycle_version=args.TCV,
                                        token=args.TOKEN,
                                        basic_auth=args.BA,
                                        username=args.USER)
        zefir_table

        #statisctics = FileSystemStatistics(username=args.USER, 
        #                                token=args.TOKEN)
        #statisctics.update_statistics()

    end_status = 0
    while end_status == 0:
        jira_end, life_end = response()
        try:
            if jira_end == 200 and life_end == 200:
                upload_result_status()
                end_status += 1
            else: 
                with open('JIRA_ERROR.log', 'a') as err:
                    err.write('end:\n')
                    err.write(ctime())
                    err.write(f'jira_status = {jira_end}\nlife_status = {life_end}')
                    err.write('---------' * 25)
                    err.write('\n\n')
                sleep(60)
        except Exception as e:
            with open('JIRA_ERROR.log', 'a') as err:
                err.write('end:\n')
                err.write(ctime())
                err.write(str(e))
                err.write('---------' * 25)
                err.write('\n\n')
                end_status += 1

main()

if path.isfile('libs/zefir.log'):
    with open('libs/zefir.log', 'r') as r:
        zefir_log = r.read()
if path.isfile('JIRA_ERROR.log'):
    with open('JIRA_ERROR.log', 'r') as r:
        jira_log = r.read()
print('\n\n\nZefir-log\n')
print(zefir_log)
print('\n\n\nJira-log\n')
print(jira_log)

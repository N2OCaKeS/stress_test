# -*- coding: UTF-8 -*-

# ;===========================================================
# ; Author: rkuznetsov@astralinux.ru
# ; Date: 2022
# ;===========================================================

import argparse
import os
import subprocess
import runpy
import time

from sys import exit
from os import getuid, path
from psb_conf import SCRIPT_DIR, LOG_FILENAME, REPORT_FILENAME, REPORT_PATH, \
    MAC_SQL_UPGRADE, MAC_SQL_TRANSACTION, \
    MIC_SQL_UPGRADE, MIC_SQL_TRANSACTION, \
    ACL_SQL_UPGRADE, ACL_SQL_TRANSACTION, \
    DEFAULT_SCALE_FACTOR, DEFAULT_TRANSACTIONS, DEFAULT_THREADS, \
    SCALE_FACTOR, SCALE_FACTOR_STEP, LIMITE_SCALE_FACTOR, \
    TRANSACTIONS, TRANSACTIONS_STEP, LIMITE_TRANSACTIONS, \
    THREADS, THREADS_STEP, LIMITE_THREADS, \
    CLIENTS, CLIENTS_STEP, LIMITE_CLIENTS, STEP_RATIO_BY_CLIENTS, PG_VERSION
from libs.libpsqltests import Test
from libs.libpsb import astra_version
from libs.libtable import Report

DESCRIPTION = ""
parser = argparse.ArgumentParser(description=DESCRIPTION)
parser.add_argument('-t', '--testlist',
                    action='store',
                    choices=['base'],  # 'mac', 'mic', 'acl']
                    required=False,
                    default='base',
                    help='testlist',
                    dest='TEST_LIST')

parser.add_argument('-m', '--mode',
                    action='store',
                    choices=['default',
                             'extended'],
                    required=False,
                    default='default',
                    help='Get default parameters (default) or parameters from config (extended)',
                    dest='MODE')

parser.add_argument('-db', '--dbprep',
                    action='store_true',
                    required=False,
                    help='prepare host, create cluster, init database ...',
                    dest='DB_PREPARE')

parser.add_argument('-c', '--cleaner',
                    action='store_true',
                    required=False,
                    help='delete cluster, delete database ...',
                    dest='CLEANER')

args = parser.parse_args()

# is root?
if getuid() != 0:
    exit(2)

# clean log
if path.exists(LOG_FILENAME):
    report = open(LOG_FILENAME, 'w')
    report.close()

# create dir
if not path.exists(REPORT_PATH):
    os.mkdir(REPORT_PATH, mode=0o755)

version = astra_version()
if args.DB_PREPARE:
    '''
        Настроить машину, инициализировать тестовую БД
    '''
    subprocess.run('sudo bash {dir}/psb_db_prep.sh {init_file}'.format(dir=SCRIPT_DIR,
                                                                       init_file='psb_init.sql'),
                   shell=True,
                   stderr=subprocess.DEVNULL)
    if PG_VERSION == 14:
        runpy.run_module(mod_name='psb14_config-trust')
        os.system("sudo service postgresql restart")
        time.sleep(3)

if args.TEST_LIST == 'base':
    if args.MODE == 'default':

        '''
            Запуск на оптимальных настройках
        '''
        scale_factor = DEFAULT_SCALE_FACTOR
        transactions = DEFAULT_TRANSACTIONS
        threads = DEFAULT_THREADS
        clients = CLIENTS
        clients_step = CLIENTS_STEP
        step_ratio_by_clients = STEP_RATIO_BY_CLIENTS  # (client_step)*(step_ratio_by_clients) every iteration
        limite_clients = LIMITE_CLIENTS

        # clean conf
        report = open(REPORT_FILENAME, 'w')
        report.close()

        print('# INFO # --- scale factor {}'.format(str(scale_factor)))
        print('# INFO # --- transactions count {}'.format(str(transactions)))
        print('# INFO # --- threads count {}'.format(str(threads)))
        print('# INFO # --- max clients count {}'.format(str(limite_clients)))

        while clients <= limite_clients:
            print('# INFO # --- clients count {}'.format(str(clients)))
            with open(REPORT_FILENAME, 'a+') as report_file:
                report_file.write(str(clients))
            test = Test(scale=scale_factor,
                        trs=transactions,
                        ths=threads,
                        cls=clients)
            print(test.run_test())
            clients += clients_step
            clients_step *= step_ratio_by_clients

        # create report
        report = Report(param_name='clients')
        report.create_beauty_table()
        report.create_psb_cl_la_graph()
        report.create_psb_cl_tps1_graph()
        report.create_psb_cl_tps2_graph()
        report.create_psb_cl_tpsall_graph()
        report.merge(table_lst=['psb_report_table.html'],
                     graph_lst=['psb_clients_la_graph.png',
                                'psb_clients_tps1_graph.png',
                                'psb_clients_tps2_graph.png',
                                'psb_clients_tpsall_graph.png'])
        report.create_tar()

    if args.MODE == 'extended':
        '''
            Проверка на втроенных сценариях.
            Нахождение предельного коэффициента масштаба.
        '''
        report = open(REPORT_FILENAME, 'w')
        report.close()

        scale_factor = SCALE_FACTOR
        scale_factor_step = SCALE_FACTOR_STEP
        limite_scale_factor = LIMITE_SCALE_FACTOR
        max_scale_factor = 0

        while scale_factor < limite_scale_factor:
            with open(REPORT_FILENAME, 'a+') as report_file:
                report_file.write(str(scale_factor))
            test = Test(scale=scale_factor)
            result = test.run_test()
            if result is False:
                max_scale_factor = scale_factor
                break
            else:
                max_scale_factor = scale_factor
                scale_factor += scale_factor_step
                print(result)

        # create report
        report = Report(param_name='scale')
        report.create_beauty_table('psb_scale_table.html')
        report.create_psb_sc_la_graph()
        report.create_psb_sc_tpsall_graph()

        '''
            Проверка на втроенных сценариях.
            Нахождение предельного числа транзакций.
        '''
        report = open(REPORT_FILENAME, 'w')
        report.close()

        transactions = TRANSACTIONS
        transactions_step = TRANSACTIONS_STEP
        limite_transactions = LIMITE_TRANSACTIONS
        max_transactions_count = 0

        while transactions < limite_transactions:
            with open(REPORT_FILENAME, 'a+') as report_file:
                report_file.write(str(transactions))
            test = Test(trs=transactions)
            result = test.run_test()
            if result is False:
                max_transactions_count = transactions
                break
            else:
                max_transactions_count = transactions
                transactions += transactions_step
                print(result)

        # create report
        report = Report(param_name='transactions')
        report.create_beauty_table('psb_transactions_table.html')
        report.create_psb_tr_la_graph()
        report.create_psb_tr_tpsall_graph()

        '''
            Проверка на втроенных сценариях.
            Нахождение предельного числа потоков. 
        '''
        report = open(REPORT_FILENAME, 'w')
        report.close()

        threads = THREADS
        threads_step = THREADS_STEP
        limite_threads = LIMITE_THREADS
        max_threads_count = 0

        while threads < limite_threads:
            with open(REPORT_FILENAME, 'a+') as report_file:
                report_file.write(str(threads))
            test = Test(ths=threads)
            result = test.run_test()
            if result is False:
                max_threads_count = threads
                break
            else:
                max_threads_count = threads
                threads += threads_step
                print(result)

        # create report
        report = Report(param_name='threads')
        report.create_beauty_table('psb_threads_table.html')
        report.create_psb_th_la_graph()
        report.create_psb_th_tpsall_graph()

        '''
            Проверка на втроенных сценариях.
            Нахождение предельного числа клиентов. 
        '''
        report = open(REPORT_FILENAME, 'w')
        report.close()

        clients = CLIENTS
        clients_step = CLIENTS_STEP
        limite_clients = LIMITE_CLIENTS
        max_clients_count = 0

        while clients < limite_clients:
            with open(REPORT_FILENAME, 'a+') as report_file:
                report_file.write(str(clients))
            test = Test(cls=clients)
            result = test.run_test()
            if result is False:
                max_clients_count = clients
                break
            else:
                max_clients_count = clients
                clients += clients_step
                print(result)

        # create report
        report = Report(param_name='clients')
        report.create_beauty_table('psb_clients_table.html')
        report.create_psb_cl_la_graph()
        report.create_psb_cl_tpsall_graph()

        print('# INFO # --- max scale factor {}'.format(str(max_scale_factor)))
        print('# INFO # --- max transactions count {}'.format(str(max_transactions_count)))
        print('# INFO # --- max threads count {}'.format(str(max_threads_count)))
        print('# INFO # --- max clients count {}'.format(str(max_clients_count)))

        '''
            Запуск на максимально допустимых настройках
        '''
        report = open(REPORT_FILENAME, 'w')
        report.close()

        scale_factor = SCALE_FACTOR
        scale_factor_step = SCALE_FACTOR_STEP
        transactions = TRANSACTIONS
        transactions_step = TRANSACTIONS_STEP
        threads = THREADS
        threads_step = THREADS_STEP
        clients = CLIENTS
        clients_step = CLIENTS_STEP

        while (scale_factor <= max_scale_factor) and \
                (transactions <= max_transactions_count) and \
                (threads <= max_threads_count) and \
                (clients <= max_clients_count):
            with open(REPORT_FILENAME, 'a+') as report_file:
                report_file.write(str(scale_factor))
                report_file.write(str(transactions))
                report_file.write(str(threads))
                report_file.write(str(clients))
            test = Test(scale=scale_factor,
                        trs=transactions,
                        ths=threads,
                        cls=clients)
            print(test.run_test())
            scale_factor += scale_factor_step
            transactions += transactions_step
            threads += threads_step
            clients += clients_step

        report = Report(all_params=True)
        report.create_beauty_table('psb_max_table.html')
        report.merge(table_lst=['psb_scale_table.html',
                                'psb_transactions_table.html',
                                'psb_threads_table.html',
                                'psb_clients_table.html',
                                'psb_max_table.html'],
                     graph_lst=['psb_scale_la_graph.png',
                                'psb_scale_tpsall_graph.png',
                                'psb_transactions_la_graph.png',
                                'psb_transactions_tpsall_graph.png',
                                'psb_threads_la_graph.png',
                                'psb_threads_tpsall_graph.png',
                                'psb_clients_la_graph.png',
                                'psb_clients_tpsall_graph.png'])


if args.CLEANER:
    '''
        Удалить тестовую базу и настройки PostgreSQL
    '''
    subprocess.run('bash {dir}/psb_db_del.sh {v}'.format(dir=SCRIPT_DIR, v=version[0]),
                   shell=True,
                   stderr=subprocess.DEVNULL)
#!venv/bin/python
# -*- coding: UTF-8 -*-

# ;===========================================================
# ; Author: rkuznetsov@astralinux.ru
# ; Date: 2022
# ;===========================================================

import argparse
import subprocess

from psb_conf import SCRIPT_DIR, LOG_FILENAME, \
    MAC_SQL_UPGRADE, MAC_SQL_TRANSACTION, \
    MIC_SQL_UPGRADE, MIC_SQL_TRANSACTION, \
    ACL_SQL_UPGRADE, ACL_SQL_TRANSACTION, \
    SCALE_FACTOR, SCALE_FACTOR_STEP, LIMITE_SCALE_FACTOR, \
    TRANSACTIONS, TRANSACTIONS_STEP, LIMITE_TRANSACTIONS, \
    THREADS, THREADS_STEP, LIMITE_THREADS, \
    CLIENTS, CLIENTS_STEP, LIMITE_CLIENTS
from libs.libpsqbtests import Test
from libs.libpsb import astra_version

DESCRIPTION = ""
parser = argparse.ArgumentParser(description=DESCRIPTION)
parser.add_argument('--testlist',
                    action='store',
                    choices=['base'],  # 'mac', 'mic', 'acl']
                    required=False,
                    default='base',
                    help='testlist',
                    dest='TEST_LIST')
parser.add_argument('--dbprep',
                    action='store_true',
                    required=False,
                    help='prepare host, create cluster, init database ...',
                    dest='DB_PREPARE')
parser.add_argument('--cleaner',
                    action='store_true',
                    required=False,
                    help='delete cluster, delete database ...',
                    dest='CLEANER')

args = parser.parse_args()

subprocess.run('rm -rf {file}'.format(file=LOG_FILENAME),
               shell=True,
               stderr=subprocess.DEVNULL)
version = astra_version()
if args.DB_PREPARE:
    '''
        Настроить машину, инициализировать тестовую БД
    '''
    if version[0] == '1.7' or version[0] == '4.7':
        subprocess.run('sudo bash {dir}/psb_db_prep_11.sh'.format(dir=SCRIPT_DIR),
                       shell=True,
                       stderr=subprocess.DEVNULL)
    elif version[0] == '1.6' or version[0] == '8.1' or version[0] == '2.12':
        subprocess.run('sudo bash {dir}/psb_db_prep_96.sh'.format(dir=SCRIPT_DIR),
                       shell=True,
                       stderr=subprocess.DEVNULL)


scale_factor = SCALE_FACTOR
scale_factor_step = SCALE_FACTOR_STEP
limite_scale_factor = LIMITE_SCALE_FACTOR

transactions = TRANSACTIONS
transactions_step = TRANSACTIONS_STEP
limite_transactions = LIMITE_TRANSACTIONS

threads = THREADS
threads_step = THREADS_STEP
limite_threads = LIMITE_THREADS

clients = CLIENTS
clients_step = CLIENTS_STEP
limite_clients = LIMITE_CLIENTS

max_scale_factor = 0
max_transactions_count = 0
max_threads_count = 0
max_clients_count = 0

if args.TEST_LIST == 'base':
    '''
        Проверка на втроенных сценариях.
        Нахождение предельного коэффициента масштаба.
    '''
    while scale_factor < limite_scale_factor:
        test = Test(scale=scale_factor)
        result = test.run_test()
        if result is False:
            max_scale_factor = scale_factor
            break
        else:
            max_scale_factor = scale_factor
            scale_factor += scale_factor_step
            print(result)
    '''
        Проверка на втроенных сценариях.
        Нахождение предельного числа транзакций.
    '''
    while transactions < limite_transactions:
        test = Test(trs=transactions)
        result = test.run_test()
        if result is False:
            max_transactions_count = transactions
            break
        else:
            max_transactions_count = transactions
            transactions += transactions_step
            print(result)
    '''
        Проверка на втроенных сценариях.
        Нахождение предельного числа потоков. 
    '''
    while threads < limite_threads:
        test = Test(ths=threads)
        result = test.run_test()
        if result is False:
            max_threads_count = threads
            break
        else:
            max_threads_count = threads
            threads += threads_step
            print(result)
    '''
        Проверка на втроенных сценариях.
        Нахождение предельного числа клиентов. 
    '''
    while clients < limite_clients:
        test = Test(cls=clients)
        result = test.run_test()
        if result is False:
            max_clients_count = clients
            break
        else:
            max_clients_count = clients
            clients += clients_step
            print(result)

    print('# INFO # --- max scale factor {}'.format(str(max_scale_factor)))
    print('# INFO # --- max transactions count {}'.format(str(max_transactions_count)))
    print('# INFO # --- max threads count {}'.format(str(max_threads_count)))
    print('# INFO # --- max clients count {}'.format(str(max_clients_count)))

    '''
        Запуск на максимально допустимых настройках
    '''
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
        test = Test(scale=scale_factor,
                    trs=transactions,
                    ths=threads,
                    cls=clients)
        print(test.run_test())
        scale_factor += scale_factor_step
        transactions += transactions_step
        threads += threads_step
        clients += clients_step

if args.TEST_LIST == 'mac':
    '''
        Проверка на внешних сценариях.
        Нахождение предельного коэффициента масштаба.
    '''
    while scale_factor < limite_scale_factor:
        test = Test(scale=scale_factor)
        result = test.run_test_custom(upgrade_script=MAC_SQL_UPGRADE,
                                      test_script=MAC_SQL_TRANSACTION)
        if result is False:
            max_scale_factor = scale_factor
            break
        else:
            scale_factor += scale_factor_step
            print(result)
    '''
        Проверка на внешних сценариях.
        Нахождение предельного числа транзакций.
    '''
    while transactions < limite_transactions:
        test = Test(trs=transactions)
        result = test.run_test_custom(upgrade_script=MAC_SQL_UPGRADE,
                                      test_script=MAC_SQL_TRANSACTION)
        if result is False:
            max_transactions_count = transactions
            break
        else:
            transactions += transactions_step
            print(result)
    '''
        Проверка на внешних сценариях.
        Нахождение предельного числа потоков. 
    '''
    while threads < limite_threads:
        test = Test(ths=threads)
        result = test.run_test_custom(upgrade_script=MAC_SQL_UPGRADE,
                                      test_script=MAC_SQL_TRANSACTION)
        if result is False:
            max_threads_count = threads
            break
        else:
            threads += threads_step
            print(result)
    '''
        Проверка на внешних сценариях.
        Нахождение предельного числа клиентов. 
    '''
    while clients < limite_clients:
        test = Test(cls=clients)
        result = test.run_test_custom(upgrade_script=MAC_SQL_UPGRADE,
                                      test_script=MAC_SQL_TRANSACTION)
        if result is False:
            max_clients_count = clients
            break
        else:
            clients += clients_step
            print(result)

    print('# INFO # --- max scale factor {}'.format(str(max_scale_factor)))
    print('# INFO # --- max transactions count {}'.format(str(max_transactions_count)))
    print('# INFO # --- max threads count {}'.format(str(max_threads_count)))
    print('# INFO # --- max clients count {}'.format(str(max_clients_count)))

    '''
        Запуск на максимально допустимых настройках
    '''
    test = Test(scale=max_scale_factor,
                trs=max_transactions_count,
                ths=max_threads_count,
                cls=max_clients_count)
    print(test.run_test_custom(upgrade_script=MAC_SQL_UPGRADE,
                               test_script=MAC_SQL_TRANSACTION))

if args.TEST_LIST == 'mic':
    '''
        Проверка на внешних сценариях.
        Нахождение предельного коэффициента масштаба.
    '''
    while scale_factor < limite_scale_factor:
        test = Test(scale=scale_factor)
        result = test.run_test_custom(upgrade_script=MIC_SQL_UPGRADE,
                                      test_script=MIC_SQL_TRANSACTION)
        if result is False:
            max_scale_factor = scale_factor
            break
        else:
            scale_factor += scale_factor_step
            print(result)
    '''
        Проверка на внешних сценариях.
        Нахождение предельного числа транзакций.
    '''
    while transactions < limite_transactions:
        test = Test(trs=transactions)
        result = test.run_test_custom(upgrade_script=MIC_SQL_UPGRADE,
                                      test_script=MIC_SQL_TRANSACTION)
        if result is False:
            max_transactions_count = transactions
            break
        else:
            transactions += transactions_step
            print(result)
    '''
        Проверка на внешних сценариях.
        Нахождение предельного числа потоков. 
    '''
    while threads < limite_threads:
        test = Test(ths=threads)
        result = test.run_test_custom(upgrade_script=MIC_SQL_UPGRADE,
                                      test_script=MIC_SQL_TRANSACTION)
        if result is False:
            max_threads_count = threads
            break
        else:
            threads += threads_step
            print(result)
    '''
        Проверка на внешних сценариях.
        Нахождение предельного числа клиентов. 
    '''
    while clients < limite_clients:
        test = Test(cls=clients)
        result = test.run_test_custom(upgrade_script=MIC_SQL_UPGRADE,
                                      test_script=MIC_SQL_TRANSACTION)
        if result is False:
            max_clients_count = clients
            break
        else:
            clients += clients_step
            print(result)

    print('# INFO # --- max scale factor {}'.format(str(max_scale_factor)))
    print('# INFO # --- max transactions count {}'.format(str(max_transactions_count)))
    print('# INFO # --- max threads count {}'.format(str(max_threads_count)))
    print('# INFO # --- max clients count {}'.format(str(max_clients_count)))

    '''
        Запуск на максимально допустимых настройках
    '''
    test = Test(scale=max_scale_factor,
                trs=max_transactions_count,
                ths=max_threads_count,
                cls=max_clients_count)
    print(test.run_test_custom(upgrade_script=MIC_SQL_UPGRADE,
                               test_script=MIC_SQL_TRANSACTION))

if args.TEST_LIST == 'acl':
    '''
        Проверка на внешних сценариях.
        Нахождение предельного коэффициента масштаба.
    '''
    while scale_factor < limite_scale_factor:
        test = Test(scale=scale_factor)
        result = test.run_test_custom(upgrade_script=ACL_SQL_UPGRADE,
                                      test_script=ACL_SQL_TRANSACTION)
        if result is False:
            max_scale_factor = scale_factor
            break
        else:
            scale_factor += scale_factor_step
            print(result)
    '''
        Проверка на внешних сценариях.
        Нахождение предельного числа транзакций.
    '''
    while transactions < limite_transactions:
        test = Test(trs=transactions)
        result = test.run_test_custom(upgrade_script=ACL_SQL_UPGRADE,
                                      test_script=ACL_SQL_TRANSACTION)
        if result is False:
            max_transactions_count = transactions
            break
        else:
            transactions += transactions_step
            print(result)
    '''
        Проверка на внешних сценариях.
        Нахождение предельного числа потоков. 
    '''
    while threads < limite_threads:
        test = Test(ths=threads)
        result = test.run_test_custom(upgrade_script=ACL_SQL_UPGRADE,
                                      test_script=ACL_SQL_TRANSACTION)
        if result is False:
            max_threads_count = threads
            break
        else:
            threads += threads_step
            print(result)
    '''
        Проверка на внешних сценариях.
        Нахождение предельного числа клиентов. 
    '''
    while clients < limite_clients:
        test = Test(cls=clients)
        result = test.run_test_custom(upgrade_script=ACL_SQL_UPGRADE,
                                      test_script=ACL_SQL_TRANSACTION)
        if result is False:
            max_clients_count = clients
            break
        else:
            clients += clients_step
            print(result)

    print('# INFO # --- max scale factor {}'.format(str(max_scale_factor)))
    print('# INFO # --- max transactions count {}'.format(str(max_transactions_count)))
    print('# INFO # --- max threads count {}'.format(str(max_threads_count)))
    print('# INFO # --- max clients count {}'.format(str(max_clients_count)))

    '''
        Запуск на максимально допустимых настройках
    '''
    test = Test(scale=max_scale_factor,
                trs=max_transactions_count,
                ths=max_threads_count,
                cls=max_clients_count)
    print(test.run_test_custom(upgrade_script=ACL_SQL_UPGRADE,
                               test_script=ACL_SQL_TRANSACTION))

if args.CLEANER:
    '''
        Удалить тестовую базу и настройки PostgreSQL
    '''
    subprocess.run('bash {dir}/psb_db_del.sh {v}'.format(dir=SCRIPT_DIR, v=version[0]),
                   shell=True,
                   stderr=subprocess.DEVNULL)
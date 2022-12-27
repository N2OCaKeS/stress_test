# -*- coding: UTF-8 -*-

# ;===========================================================
# ; Author: rkuznetsov@astralinux.ru
# ; Date: 2022
# ;===========================================================

import logging
import argparse

from time import time
from libs.libtests import TestSet
from libs.libtable import Report
from libs.libcfs import put_system_info_in_file
from cfs_conf import LOG_FILENAME, INFO_PATH, \
    START_BORDER_FOR_DATA, STEP_FOR_DATA, END_BORDER_FOR_DATA, TIMEOUT, \
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


if args.TS == 'base_load':
    '''
        Прогон 1.
        Средняя загрузка разными файлами на r/w.
        Потоков:                                       1
        Количество тестовых структур (разных файлов):  count
        Нижняя граница загрузки:                       40%
        Верхняя граница загрузки:                      70%
        Шаг загрузки:                                  5%
    '''
    start_time = time()

    # 1000 - 10000 c шагов в 1000 файлов
    for count in range(1000, 10000, 1000):
        run_test = TestSet(file_count=count) # количество файлов
        try:
            run_test.test_1_base_load()
        except Exception as exeption:
            log.info(exeption)
        finally:
            log.info("--- {} sec ---".format(round(time() - start_time)))
            print("# INFO # --- {} files {} sec ---".format(count, round(time() - start_time)))
    log.info("--- 1000-10000 {} sec ---".format(round(time() - start_time)))
    print("# INFO # --- 1000-10000 {} sec ---".format(round(time() - start_time)))
    exit()
    # 10000 - 100000 c шагов в 10000 файлов
    for count in range(10000, 100000, 10000):
        run_test = TestSet(file_count=count) # количество файлов
        try:
            run_test.test_1_base_load()
        except Exception as exeption:
            log.info(exeption)
        finally:
            log.info("--- {} sec ---".format(round(time() - start_time)))
            print("# INFO # --- {} files {} sec ---".format(count, round(time() - start_time)))
    log.info("--- 10000-100000 {} sec ---".format(round(time() - start_time)))
    print("# INFO # --- 10000-100000 {} sec ---".format(round(time() - start_time)))

    # 100000 - 1000000 c шагов в 100000 файлов
    for count in range(100000, 1000000, 100000):
        run_test = TestSet(file_count=count) # количество файлов
        try:
            run_test.test_1_base_load()
        except Exception as exeption:
            log.info(exeption)
        finally:
            log.info("--- {} sec ---".format(round(time() - start_time)))
            print("# INFO # --- {} files {} sec ---".format(count, round(time() - start_time)))
    log.info("--- 100000-1000000 {} sec ---".format(round(time() - start_time)))
    print("# INFO # --- 100000-1000000 {} sec ---".format(round(time() - start_time)))

if args.TS == 'timeout':
    '''
        Прогон 2.
        Средняя загрузка разными файлами на r/w на протяжении времени.
        Потоков:                                       1
        Количество тестовых структур (разных файлов):  5000
        Нижняя граница загрузки:                       40%
        Верхняя граница загрузки:                      70%
        Шаг загрузки:                                  5%
        Время загрузки                                 7200 (2 часа)
    '''
    start_time = time()
    run_test = TestSet(test_timeout=TIMEOUT)

    try:
        run_test.test_2_timeout()
    except Exception as exeption:
        log.info(exeption)
    finally:
        log.info("--- {}/7200 sec ---".format(round(time() - start_time)))
        print("--- {}/7200 sec ---".format(round(time() - start_time)))

if args.TS == 'multithreaded':
    '''    
        Прогон 3.
        Средняя загрузка разными файлами на r/w на протяжении времени.
        Потоков:                                       3,4,5
        Количество тестовых структур (разных файлов):  5000
        Нижняя граница загрузки:                       10%
        Верхняя граница загрузки:                      20%
        Шаг загрузки:                                  2%
    '''
    run_test = TestSet(start_burder=START_BORDER_FOR_DATA,
                       end_burder=END_BORDER_FOR_DATA,
                       step=STEP_FOR_DATA)

    start_time = time()
    # 3 потока. 3 типа файлов
    try:
        run_test.test_3_threads()
    except Exception as exeption:
        log.info(exeption)
    finally:
        log.info("--- {} sec ---".format(round(time() - start_time)))
        print("# INFO # --- {} sec ---".format(round(time() - start_time)))

    # 4 потока. 4 типа файлов
    try:
        run_test.test_4_threads()
    except Exception as exeption:
        log.info(exeption)
    finally:
        log.info("--- {} sec ---".format(round(time() - start_time)))
        print("# INFO # --- {} sec ---".format(round(time() - start_time)))

    # 5 потоков. 5 типов файлов
    try:
        run_test.test_5_threads()
    except Exception as exeption:
        log.info(exeption)
    finally:
        log.info("--- {} sec ---".format(round(time() - start_time)))
        print("# INFO # --- {} sec ---".format(round(time() - start_time)))

if args.TS == 'big_files':
    '''    
        Прогон 4.
        Средняя загрузка разными файлами на r/w на протяжении времени.
        Потоков:                                       1
        Количество тестовых структур (разных файлов):  2
        Нижняя граница загрузки:                       10%
        Верхняя граница загрузки:                      40%
        Шаг загрузки:                                  5%
    '''
    start_time = time()
    run_test = TestSet(start_burder=START_BORDER_FOR_DATA,
                       end_burder=END_BORDER_FOR_DATA,
                       step=STEP_FOR_DATA)

    try:
        run_test.test_6_big_files()
    except Exception as exeption:
        log.info(exeption)
    finally:
        log.info("--- {} sec ---".format(round(time() - start_time)))
        print("# INFO # --- {} sec ---".format(round(time() - start_time)))

if args.TS == 'fs_mark_count':
    '''    
        Прогон 5.
        fs_mark
    '''
    # Изменение количества файлов
    start_time = time()
    run_test = TestSet(start_burder=FILES,
                       end_burder=FILES_LIMIT,
                       step=FILES_STEP)

    try:
        run_test.test_7_fs_mark33_count()
    except Exception as exeption:
        log.info(exeption)
    finally:
        log.info("--- {} sec ---".format(round(time() - start_time)))
        print("# INFO # --- {} sec ---".format(round(time() - start_time)))

    put_system_info_in_file(start_time, INFO_PATH)

    report = Report(ox_lo_lim=FILES, ox_step=FILES_STEP, ox_up_lim=FILES_LIMIT)
    report.create_beauty_table()
    report.create_cfs_fc_sp_graph()
    report.create_cfs_fc_app_overhead_graph()
    report.create_cfs_fc_create_graph()
    report.create_cfs_fc_write_graph()
    report.create_cfs_fc_fsync_graph()
    report.create_cfs_fc_sync_graph()
    report.create_cfs_fc_close_graph()
    report.create_cfs_fc_unlink_graph()
    report.merge(ox_lst=report.file_count_lst,
                 table_lst=['cfs_report_table.html'],
                 graph_lst=['cfs_file_count_speed_graph.png',
                            'cfs_file_count_app_overhead_graph.png',
                            'cfs_file_count_create_graph.png',
                            'cfs_file_count_write_graph.png',
                            'cfs_file_count_fsync_graph.png',
                            'cfs_file_count_sync_graph.png',
                            'cfs_file_count_close_graph.png',
                            'cfs_file_count_unlink_graph.png'])
    report.create_tar()


if args.TS == 'fs_mark_size':
    '''    
        Прогон 6.
        fs_mark
    '''
    # Изменение размера файлов
    start_time = time()
    run_test = TestSet(start_burder=SIZE,
                       end_burder=SIZE_LIMIT,
                       step=SIZE_STEP)

    try:
        run_test.test_8_fs_mark33_size()
    except Exception as exeption:
        log.info(exeption)
    finally:
        log.info("--- {} sec ---".format(round(time() - start_time)))
        print("# INFO # --- {} sec ---".format(round(time() - start_time)))

    put_system_info_in_file(start_time, INFO_PATH)

    report = Report(ox_lo_lim=SIZE, ox_step=SIZE_STEP, ox_up_lim=SIZE_LIMIT)
    report.create_beauty_table()
    report.create_cfs_fc_sp_graph()
    report.create_cfs_fc_app_overhead_graph()
    report.create_cfs_fc_create_graph()
    report.create_cfs_fc_write_graph()
    report.create_cfs_fc_fsync_graph()
    report.create_cfs_fc_sync_graph()
    report.create_cfs_fc_close_graph()
    report.create_cfs_fc_unlink_graph()
    report.merge(ox_lst=report.file_count_lst,
                 table_lst=['cfs_report_table.html'],
                 graph_lst=['cfs_file_count_speed_graph.png',
                            'cfs_file_count_app_overhead_graph.png',
                            'cfs_file_count_create_graph.png',
                            'cfs_file_count_write_graph.png',
                            'cfs_file_count_fsync_graph.png',
                            'cfs_file_count_sync_graph.png',
                            'cfs_file_count_close_graph.png',
                            'cfs_file_count_unlink_graph.png'])
    report.create_tar()


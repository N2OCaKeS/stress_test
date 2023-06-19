# -*- coding: UTF-8 -*-

# ;===========================================================
# ; Author: rkuznetsov@astralinux.ru
# ; Date: 2022
# ;===========================================================

import argparse
import subprocess

from time import time, strftime, gmtime, sleep, ctime
from os import path, mkdir, listdir, remove
from libs.libaub import put_system_info_in_file, upload_results_to_ftp
from libs.libtest import AuditdTestSet
from libs.libtable import Report
from libs.zefir import ZefirStatusAPI, ZefirResultTable
from libs.libstatistics import FileSystemStatistics
from libs.libpublic import Public
from aub_conf import \
    REPORT, REPORT_DIR, REPORT_FILENAME, \
    LOG, LOG_DIR, \
    INFO_FILENAME, \
    LATENCY_REPORT_PSAUD, LATENCY_REPORT_USAUD, LATENCY_REPORT_FLAUD, \
    LOSSES_REPORT_PSAUD, LOSSES_REPORT_USAUD, LOSSES_REPORT_FLAUD, \
    PSAUD_PROC_BODYS, USERAUD_PROC_BODYS, FILEAUD_PROC_BODYS, \
    PS_LOWER_LIMIT, PS_UPPER_LIMIT, PS_STEP, \
    TEST_USER, \
    DEFAULT_PS_LIFETIME, DEFAULT_PS_EVENT_RE_INITIALIZATION_DELAY

DESCRIPTION = ""
parser = argparse.ArgumentParser(description=DESCRIPTION)
parser.add_argument('--testlist',
                    action='store',
                    choices=['psaud',
                             'useraud',
                             'fileaud'],
                    required=True,
                    help='testlist',
                    dest='TEST_LIST')

parser.add_argument('-m', '--mode',
                    action='store',
                    choices=['default',
                             'extended',
                             'test'],
                    required=False,
                    default='default',
                    help='type of auditd tests',
                    dest='MODE')

parser.add_argument('-e', '--event',
                    action='store',
                    choices=['open',
                             'create',
                             'exec',
                             'delete',
                             'chmod',
                             'chown',
                             'mount',
                             'module',
                             'uid',
                             'gid',
                             'audit',
                             'acl',
                             'mac',
                             'cap',
                             'chroot',
                             'rename',
                             'net'],
                    required=False,
                    default='open',
                    help='audit event',
                    dest='EVENT')

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
    return 'completed'

start_status = 0
while start_status == 0:
    try:
        if test_cycle_status_start() is 'completed':
            start_status += 1
        else: sleep(60)
    except Exception as e:
        with open('JIRA_ERROR.log', 'a') as err:
            err.write('start:\n')
            err.write(ctime())
            err.write(e)
            err.write('---------' * 25)
            err.write('\n\n')

# Засечь время выполнения скрипта
start_time = time()

# Создать /log
if not path.exists(LOG_DIR):
    mkdir(LOG_DIR, mode=0o755)

# Очистить лог
log_file = open(LOG, 'w')
log_file.close()

log_file = open(INFO_FILENAME, 'w')
log_file.close()

# Создать /report
if not path.exists(REPORT_DIR):
    mkdir(REPORT_DIR, mode=0o755)
else:
    for file in listdir(REPORT_DIR):
        remove(path.join(REPORT_DIR, file))

# Очистить отчет
report_file = open(REPORT, 'w')
report_file.close()

if args.TEST_LIST == 'psaud':
    if args.MODE == 'default':
        # Очистить отчет
        report_file = open(LATENCY_REPORT_PSAUD, 'w')
        report_file.close()

        for event in PSAUD_PROC_BODYS.keys():
            for quantity in range(PS_LOWER_LIMIT, PS_UPPER_LIMIT, PS_STEP):
                AuditdTestSet.get_latency_stat_psaud(event,
                                                     quantity,
                                                     DEFAULT_PS_LIFETIME,
                                                     DEFAULT_PS_EVENT_RE_INITIALIZATION_DELAY,
                                                     LATENCY_REPORT_PSAUD)
        # Очистить отчет
        report_file = open(LOSSES_REPORT_PSAUD, 'w')
        report_file.close()

        for event in PSAUD_PROC_BODYS.keys():
            for quantity in range(PS_LOWER_LIMIT, PS_UPPER_LIMIT, PS_STEP):
                AuditdTestSet.get_losses_stat_psaud(event,
                                                    quantity,
                                                    DEFAULT_PS_LIFETIME,
                                                    DEFAULT_PS_EVENT_RE_INITIALIZATION_DELAY,
                                                    LOSSES_REPORT_PSAUD)

        put_system_info_in_file(start_time, INFO_FILENAME)

        # Cоздать отчет
        r = Report(PSAUD_PROC_BODYS.keys(), LATENCY_REPORT_PSAUD, LOSSES_REPORT_PSAUD)
        r.create_beauty_table(type='ps')
        r.create_total_latency_eps_graph(type='ps')
        r.create_total_losses_eps_graph(type='ps')
        r.get_total_auditd_rating()
        r.create_tar()

    elif args.MODE == 'extended':

        if args.EVENT in USERAUD_PROC_BODYS.keys():
            # Очистить отчет
            report_file = open(LATENCY_REPORT_USAUD, 'w')
            report_file.close()

            for quantity in range(PS_LOWER_LIMIT, PS_UPPER_LIMIT, PS_STEP):
                AuditdTestSet.get_latency_stat_psaud(args.EVENT,
                                                     quantity,
                                                     DEFAULT_PS_LIFETIME,
                                                     DEFAULT_PS_EVENT_RE_INITIALIZATION_DELAY,
                                                     LATENCY_REPORT_PSAUD)
            # Очистить отчет
            report_file = open(LOSSES_REPORT_USAUD, 'w')
            report_file.close()

            for quantity in range(PS_LOWER_LIMIT, PS_UPPER_LIMIT, PS_STEP):
                AuditdTestSet.get_losses_stat_psaud(args.EVENT,
                                                    quantity,
                                                    DEFAULT_PS_LIFETIME,
                                                    DEFAULT_PS_EVENT_RE_INITIALIZATION_DELAY,
                                                    LOSSES_REPORT_PSAUD)
    elif args.MODE == 'test':
        AuditdTestSet.get_latency_psaud_total(PSAUD_PROC_BODYS.keys(), REPORT)

        for event in PSAUD_PROC_BODYS.keys():
            for quantity in range(PS_LOWER_LIMIT, PS_UPPER_LIMIT, PS_STEP):
                AuditdTestSet.get_latency_stat_psaud(event,
                                                     quantity,
                                                     DEFAULT_PS_LIFETIME,
                                                     DEFAULT_PS_EVENT_RE_INITIALIZATION_DELAY,
                                                     LATENCY_REPORT_PSAUD)
        for event in PSAUD_PROC_BODYS.keys():
            for quantity in range(PS_LOWER_LIMIT, PS_UPPER_LIMIT, PS_STEP):
                AuditdTestSet.get_losses_stat_psaud(event,
                                                    quantity,
                                                    DEFAULT_PS_LIFETIME,
                                                    DEFAULT_PS_EVENT_RE_INITIALIZATION_DELAY,
                                                    LOSSES_REPORT_PSAUD)
    else:
        exit(2)

elif args.TEST_LIST == 'useraud':
    if args.MODE == 'default':
        # Очистить отчет
        report_file = open(LATENCY_REPORT_USAUD, 'w')
        report_file.close()

        for event in USERAUD_PROC_BODYS.keys():
            for quantity in range(PS_LOWER_LIMIT, PS_UPPER_LIMIT, PS_STEP):
                AuditdTestSet.get_latency_stat_useraud(event,
                                                       quantity,
                                                       DEFAULT_PS_LIFETIME,
                                                       DEFAULT_PS_EVENT_RE_INITIALIZATION_DELAY,
                                                       LATENCY_REPORT_USAUD,
                                                       TEST_USER)
        # Очистить отчет
        report_file = open(LOSSES_REPORT_USAUD, 'w')
        report_file.close()

        for event in USERAUD_PROC_BODYS.keys():
            for quantity in range(PS_LOWER_LIMIT, PS_UPPER_LIMIT, PS_STEP):
                AuditdTestSet.get_losses_stat_useraud(event,
                                                      quantity,
                                                      DEFAULT_PS_LIFETIME,
                                                      DEFAULT_PS_EVENT_RE_INITIALIZATION_DELAY,
                                                      LOSSES_REPORT_USAUD,
                                                      TEST_USER)

        put_system_info_in_file(start_time, INFO_FILENAME)

        # Cоздать отчет
        r = Report(USERAUD_PROC_BODYS.keys(), LATENCY_REPORT_USAUD, LOSSES_REPORT_USAUD)
        r.create_beauty_table(type='us')
        r.create_total_latency_eps_graph(type='us')
        r.create_total_losses_eps_graph(type='us')
        r.get_total_auditd_rating()
        r.create_tar()

    elif args.MODE == 'extended':

        if args.EVENT in USERAUD_PROC_BODYS.keys():
            # Очистить отчет
            report_file = open(LATENCY_REPORT_USAUD, 'w')
            report_file.close()

            for quantity in range(PS_LOWER_LIMIT, PS_UPPER_LIMIT, PS_STEP):
                AuditdTestSet.get_latency_stat_useraud(args.EVENT,
                                                       quantity,
                                                       DEFAULT_PS_LIFETIME,
                                                       DEFAULT_PS_EVENT_RE_INITIALIZATION_DELAY,
                                                       LATENCY_REPORT_USAUD,
                                                       TEST_USER)
            # Очистить отчет
            report_file = open(LOSSES_REPORT_USAUD, 'w')
            report_file.close()

            for quantity in range(PS_LOWER_LIMIT, PS_UPPER_LIMIT, PS_STEP):
                AuditdTestSet.get_losses_stat_useraud(args.EVENT,
                                                      quantity,
                                                      DEFAULT_PS_LIFETIME,
                                                      DEFAULT_PS_EVENT_RE_INITIALIZATION_DELAY,
                                                      LOSSES_REPORT_USAUD,
                                                      TEST_USER)
    elif args.MODE == 'test':
        AuditdTestSet.get_latency_useraud_total(USERAUD_PROC_BODYS.keys(), REPORT)

        for event in USERAUD_PROC_BODYS.keys():
            for quantity in range(PS_LOWER_LIMIT, PS_UPPER_LIMIT, PS_STEP):
                AuditdTestSet.get_latency_stat_useraud(event,
                                                       quantity,
                                                       DEFAULT_PS_LIFETIME,
                                                       DEFAULT_PS_EVENT_RE_INITIALIZATION_DELAY,
                                                       LATENCY_REPORT_USAUD,
                                                       TEST_USER)
        for event in USERAUD_PROC_BODYS.keys():
            for quantity in range(PS_LOWER_LIMIT, PS_UPPER_LIMIT, PS_STEP):
                AuditdTestSet.get_losses_stat_useraud(event,
                                                      quantity,
                                                      DEFAULT_PS_LIFETIME,
                                                      DEFAULT_PS_EVENT_RE_INITIALIZATION_DELAY,
                                                      LOSSES_REPORT_USAUD,
                                                      TEST_USER)
    else:
        exit(2)

elif args.TEST_LIST == 'fileaud':
    if args.MODE == 'default':
        # Очистить отчет
        report_file = open(LATENCY_REPORT_FLAUD, 'w')
        report_file.close()

        for event in FILEAUD_PROC_BODYS.keys():
            for quantity in range(PS_LOWER_LIMIT, PS_UPPER_LIMIT, PS_STEP):
                AuditdTestSet.get_latency_stat_fileaud(event,
                                                       quantity,
                                                       DEFAULT_PS_LIFETIME,
                                                       DEFAULT_PS_EVENT_RE_INITIALIZATION_DELAY,
                                                       LATENCY_REPORT_FLAUD)
        # Очистить отчет
        report_file = open(LOSSES_REPORT_FLAUD, 'w')
        report_file.close()

        for event in FILEAUD_PROC_BODYS.keys():
            for quantity in range(PS_LOWER_LIMIT, PS_UPPER_LIMIT, PS_STEP):
                AuditdTestSet.get_losses_stat_fileaud(event,
                                                      quantity,
                                                      DEFAULT_PS_LIFETIME,
                                                      DEFAULT_PS_EVENT_RE_INITIALIZATION_DELAY,
                                                      LOSSES_REPORT_FLAUD)

        put_system_info_in_file(start_time, INFO_FILENAME)

        # Cоздать отчет
        r = Report(FILEAUD_PROC_BODYS.keys(), LATENCY_REPORT_FLAUD, LOSSES_REPORT_FLAUD)
        r.create_beauty_table(type='fl')
        r.create_total_latency_eps_graph(type='fl')
        r.create_total_losses_eps_graph(type='fl')
        r.get_total_auditd_rating()
        r.create_tar()

    elif args.MODE == 'extended':

        if args.EVENT in USERAUD_PROC_BODYS.keys():
            # Очистить отчет
            report_file = open(LATENCY_REPORT_FLAUD, 'w')
            report_file.close()

            for quantity in range(PS_LOWER_LIMIT, PS_UPPER_LIMIT, PS_STEP):
                AuditdTestSet.get_latency_stat_fileaud(args.EVENT,
                                                       quantity,
                                                       DEFAULT_PS_LIFETIME,
                                                       DEFAULT_PS_EVENT_RE_INITIALIZATION_DELAY,
                                                       LATENCY_REPORT_FLAUD)
            # Очистить отчет
            report_file = open(LOSSES_REPORT_FLAUD, 'w')
            report_file.close()

            for quantity in range(PS_LOWER_LIMIT, PS_UPPER_LIMIT, PS_STEP):
                AuditdTestSet.get_losses_stat_fileaud(args.EVENT,
                                                      quantity,
                                                      DEFAULT_PS_LIFETIME,
                                                      DEFAULT_PS_EVENT_RE_INITIALIZATION_DELAY,
                                                      LOSSES_REPORT_FLAUD)

    elif args.MODE == 'test':
        AuditdTestSet.get_latency_fileaud_total(FILEAUD_PROC_BODYS.keys(), REPORT)

        for event in FILEAUD_PROC_BODYS.keys():
            for quantity in range(PS_LOWER_LIMIT, PS_UPPER_LIMIT, PS_STEP):
                AuditdTestSet.get_latency_stat_fileaud(event,
                                                       quantity,
                                                       DEFAULT_PS_LIFETIME,
                                                       DEFAULT_PS_EVENT_RE_INITIALIZATION_DELAY,
                                                       LATENCY_REPORT_FLAUD)
        for event in FILEAUD_PROC_BODYS.keys():
            for quantity in range(PS_LOWER_LIMIT, PS_UPPER_LIMIT, PS_STEP):
                AuditdTestSet.get_losses_stat_fileaud(event,
                                                      quantity,
                                                      DEFAULT_PS_LIFETIME,
                                                      DEFAULT_PS_EVENT_RE_INITIALIZATION_DELAY,
                                                      LOSSES_REPORT_FLAUD)
    else:
        exit(2)

upload_results_to_ftp(args.TCV, f'{REPORT_DIR}/{REPORT_FILENAME}', f'{args.TCYC}_{REPORT_FILENAME}')

def upload_result_status():
    public = Public(username=args.USER,
                    token=args.TOKEN,
                    conf_space=args.SPACE,
                    conf_parent_page=args.PPAGE,
                    conf_new_page_name=args.NPAGE,
                    grade_stand=args.STAND,
                    test_set=args.TEST_LIST)

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
    return 'completed'

end_status = 0
while end_status == 0:
    try:
        if upload_result_status() is 'completed':
            end_status += 1
        else: sleep(60)
    except Exception as e:
        with open('JIRA_ERROR.log', 'a') as err:
            err.write('end:\n')
            err.write(ctime())
            err.write(e)
            err.write('---------' * 25)
            err.write('\n\n')
import argparse
import os

from os import path, mkdir
from libs.libtests import AuditdTestSet
from libs.libtable import Report
from aub_conf import REPORT, REPORT_DIR, LATENCY_REPORT, LOSSES_REPORT, \
    LOG, LOG_DIR, PSAUD_PROC_BODYS, FILEAUD_PROC_BODYS, \
    PS_LOWER_LIMIT, PS_UPPER_LIMIT, PS_STEP, \
    TEST_USER, \
    DEFAULT_PS_LIFETIME, DEFAULT_PS_EVENT_RE_INITIALIZATION_DELAY

DESCRIPTION = ""
parser = argparse.ArgumentParser(description=DESCRIPTION)
parser.add_argument('-t', '--testlist',
                    action='store',
                    choices=['psaud',
                             'fileaud'],
                    required=True,
                    help='testlist',
                    dest='TEST_LIST')

parser.add_argument('-m', '--mode',
                    action='store',
                    choices=['default',
                             'extended'],
                    required=False,
                    default='default',
                    help='type of auditd tests',
                    dest='MODE')

args = parser.parse_args()

# Создать /log
if not path.exists(LOG_DIR):
    mkdir(LOG_DIR, mode=0o755)

# Очистить лог
log_file = open(LOG, 'w')
log_file.close()

# Создать /report
if not path.exists(REPORT_DIR):
    mkdir(REPORT_DIR, mode=0o755)

# Очистить отчет
report_file = open(REPORT, 'w')
report_file.close()

if args.TEST_LIST == 'psaud':
    if args.MODE == 'default':
        AuditdTestSet.get_latency_psaud_single('chmod',
                                               REPORT)

        AuditdTestSet.get_latency_psaud_total(PSAUD_PROC_BODYS.keys(),
                                              REPORT)

        # # Очистить отчет
        # report_file = open(LATENCY_REPORT, 'w')
        # report_file.close()
        #
        # for event in PSAUD_PROC_BODYS.keys():
        #     for quantity in range(PS_LOWER_LIMIT, PS_UPPER_LIMIT, PS_STEP):
        #         AuditdTestSet.get_latency_stat_psaud(event,
        #                                              quantity,
        #                                              DEFAULT_PS_LIFETIME,
        #                                              DEFAULT_PS_EVENT_RE_INITIALIZATION_DELAY,
        #                                              LATENCY_REPORT)
        # # Очистить отчет
        # report_file = open(LOSSES_REPORT, 'w')
        # report_file.close()
        #
        # for event in PSAUD_PROC_BODYS.keys():
        #     for quantity in range(PS_LOWER_LIMIT, PS_UPPER_LIMIT, PS_STEP):
        #         AuditdTestSet.get_losses_stat_psaud(event,
        #                                             quantity,
        #                                             DEFAULT_PS_LIFETIME,
        #                                             DEFAULT_PS_EVENT_RE_INITIALIZATION_DELAY,
        #                                             LOSSES_REPORT)

        # # Cоздать отчет
        # r = Report()
        # r.create_beauty_table()
        # r.create_aub_latency_eps_graph()
        # r.create_aub_losses_eps_graph()
        # r.get_total_auditd_rating()
        # r.create_tar()

    elif args.MODE == 'extended':
        pass
    else:
        pass
elif args.TEST_LIST == 'useraud':
    if args.MODE == 'default':
        AuditdTestSet.get_latency_useraud_single('open',
                                                 REPORT)

        # AuditdTestSet.get_latency_useraud_total(PSAUD_PROC_BODYS.keys(),
        #                                         REPORT)
        #
        # # Очистить отчет
        # report_file = open(LATENCY_REPORT, 'w')
        # report_file.close()
        #
        # for event in PSAUD_PROC_BODYS.keys():
        #     for quantity in range(PS_LOWER_LIMIT, PS_UPPER_LIMIT, PS_STEP):
        #         AuditdTestSet.get_latency_stat_useraud(event,
        #                                                quantity,
        #                                                DEFAULT_PS_LIFETIME,
        #                                                DEFAULT_PS_EVENT_RE_INITIALIZATION_DELAY,
        #                                                LATENCY_REPORT,
        #                                                TEST_USER)
        # # Очистить отчет
        # report_file = open(LOSSES_REPORT, 'w')
        # report_file.close()
        #
        # for event in PSAUD_PROC_BODYS.keys():
        #     for quantity in range(PS_LOWER_LIMIT, PS_UPPER_LIMIT, PS_STEP):
        #         AuditdTestSet.get_losses_stat_useraud(event,
        #                                               quantity,
        #                                               DEFAULT_PS_LIFETIME,
        #                                               DEFAULT_PS_EVENT_RE_INITIALIZATION_DELAY,
        #                                               LOSSES_REPORT,
        #                                               TEST_USER)

    elif args.MODE == 'extended':
        pass
    else:
        pass
elif args.TEST_LIST == 'fileaud':
    if args.MODE == 'default':

        AuditdTestSet.get_latency_fileaud_total(FILEAUD_PROC_BODYS.keys(),
                                                REPORT)

        # # Очистить отчет
        # report_file = open(LATENCY_REPORT, 'w')
        # report_file.close()
        #
        # for event in PSAUD_PROC_BODYS.keys():
        #     for quantity in range(PS_LOWER_LIMIT, PS_UPPER_LIMIT, PS_STEP):
        #         AuditdTestSet.get_latency_stat_fileaud(event,
        #                                                quantity,
        #                                                DEFAULT_PS_LIFETIME,
        #                                                DEFAULT_PS_EVENT_RE_INITIALIZATION_DELAY,
        #                                                LATENCY_REPORT)
        # # Очистить отчет
        # report_file = open(LOSSES_REPORT, 'w')
        # report_file.close()
        #
        # for event in PSAUD_PROC_BODYS.keys():
        #     for quantity in range(PS_LOWER_LIMIT, PS_UPPER_LIMIT, PS_STEP):
        #         AuditdTestSet.get_losses_stat_fileaud(event,
        #                                               quantity,
        #                                               DEFAULT_PS_LIFETIME,
        #                                               DEFAULT_PS_EVENT_RE_INITIALIZATION_DELAY,
        #                                               LOSSES_REPORT)
    elif args.MODE == 'extended':
        pass
    else:
        pass


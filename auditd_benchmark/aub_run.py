import argparse
import os

from os import path, mkdir
from libs.libtests import AuditdTestSet
from libs.libtable import Report
from aub_conf import \
    REPORT, REPORT_DIR, \
    LATENCY_REPORT_PSAUD, LATENCY_REPORT_USAUD, LATENCY_REPORT_FLAUD, \
    LOSSES_REPORT_PSAUD, LOSSES_REPORT_USAUD, LOSSES_REPORT_FLAUD, \
    LOG, LOG_DIR, \
    PSAUD_PROC_BODYS, USERAUD_PROC_BODYS, FILEAUD_PROC_BODYS, \
    PS_LOWER_LIMIT, PS_UPPER_LIMIT, PS_STEP, \
    TEST_USER, \
    DEFAULT_PS_LIFETIME, DEFAULT_PS_EVENT_RE_INITIALIZATION_DELAY

DESCRIPTION = ""
parser = argparse.ArgumentParser(description=DESCRIPTION)
parser.add_argument('-t', '--testlist',
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

        # Cоздать отчет
        r = Report(PSAUD_PROC_BODYS.keys(), LATENCY_REPORT_PSAUD, LOSSES_REPORT_PSAUD)
        r.create_beauty_table(type='ps')
        r.create_total_latency_eps_graph(type='ps')
        r.create_total_losses_eps_graph(type='ps')
        r.get_total_auditd_rating()
        r.create_tar()

    elif args.MODE == 'extended':
        # Очистить отчет
        report_file = open(LATENCY_REPORT_USAUD, 'w')
        report_file.close()

        if args.EVENT in USERAUD_PROC_BODYS.keys():
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

        # Cоздать отчет
        r = Report(USERAUD_PROC_BODYS.keys(), LATENCY_REPORT_USAUD, LOSSES_REPORT_USAUD)
        r.create_beauty_table(type='us')
        r.create_total_latency_eps_graph(type='us')
        r.create_total_losses_eps_graph(type='us')
        r.get_total_auditd_rating()
        r.create_tar()

    elif args.MODE == 'extended':
        # Очистить отчет
        report_file = open(LATENCY_REPORT_USAUD, 'w')
        report_file.close()

        if args.EVENT in USERAUD_PROC_BODYS.keys():
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

        # Cоздать отчет
        r = Report(FILEAUD_PROC_BODYS.keys(), LATENCY_REPORT_FLAUD, LOSSES_REPORT_FLAUD)
        r.create_beauty_table(type='fl')
        r.create_total_latency_eps_graph(type='fl')
        r.create_total_losses_eps_graph(type='fl')
        r.get_total_auditd_rating()
        r.create_tar()

    elif args.MODE == 'extended':
        pass
    elif args.MODE == 'test':
        # AuditdTestSet.get_latency_fileaud_total(FILEAUD_PROC_BODYS.keys(), REPORT)
        #
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


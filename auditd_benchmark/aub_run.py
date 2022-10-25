import argparse
import os

from os import path, mkdir
from libs.libtests import AuditdTestSet
from aub_conf import REPORT, REPORT_DIR, \
    LOG, LOG_DIR, PROC_BODYS, \
    PS_LOWER_LIMIT, PS_UPPER_LIMIT, PS_STEP, \
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
log_file = open(REPORT, 'w')
log_file.close()

if args.TEST_LIST == 'psaud':
    if args.MODE == 'default':
        for event in PROC_BODYS.keys():
            for quantity in range(PS_LOWER_LIMIT, PS_UPPER_LIMIT, PS_STEP):
                AuditdTestSet.get_latency_stat_psaud(event,
                                                     quantity,
                                                     DEFAULT_PS_LIFETIME,
                                                     DEFAULT_PS_EVENT_RE_INITIALIZATION_DELAY,
                                                    '{}/aub_report_latency.txt'.format(REPORT_DIR))
        for event in PROC_BODYS.keys():
            for quantity in range(PS_LOWER_LIMIT, PS_UPPER_LIMIT, PS_STEP):
                AuditdTestSet.get_losses_stat_psaud(event,
                                                    quantity,
                                                    DEFAULT_PS_LIFETIME,
                                                    DEFAULT_PS_EVENT_RE_INITIALIZATION_DELAY,
                                                    '{}/aub_report_losses.txt'.format(REPORT_DIR))
    elif args.MODE == 'extended':
        pass
    else:
        pass
elif args.TEST_LIST == 'useraud':
    if args.MODE == 'default':
        pass
    elif args.MODE == 'extended':
        pass
    else:
        pass
elif args.TEST_LIST == 'fileaud':
    if args.MODE == 'default':
        pass
    elif args.MODE == 'extended':
        pass
    else:
        pass


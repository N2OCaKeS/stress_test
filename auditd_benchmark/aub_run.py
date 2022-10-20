import argparse

from os import path, mkdir
from libs.libtests import AuditdTestSet
from aub_conf import REPORT, REPORT_DIR, \
    LOG, LOG_DIR

DESCRIPTION = ""
parser = argparse.ArgumentParser(description=DESCRIPTION)
parser.add_argument('-m', '--mode',
                    action='store',
                    choices=['psaud',
                             'useraud',
                             'fileaud'],
                    required=True,
                    help='',
                    dest='MODE')

parser.add_argument('-af', '--audit-flag',
                    action='store',
                    choices=['open',
                             'create',
                             'exec',
                             'remove',
                             'chmod',
                             'chown',
                             'mount',
                             'module',
                             'uid',
                             'gid',
                             'acl',
                             'mac',
                             'cap',
                             'chroot',
                             'rename',
                             'net'],
                    required=True,
                    help='',
                    dest='AUDIT_FLAG')
args = parser.parse_args()

# Создать /report
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

if args.MODE == 'psaud':
    AuditdTestSet.get_latency_auditd_single('open', REPORT)
    AuditdTestSet.get_latency_auditd_total(['open', 'create', 'exec', 'remove', 'chmod'], REPORT)

elif args.MODE == 'useraud':
    pass
elif args.MODE == 'fileaud':
    pass


import argparse
import time

from time import ctime, sleep, monotonic_ns
from libs.libaub import create_ps, cmd, \
    test_get_latency_auditd, \
    Prepare

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

if args.MODE == 'psaud':
    #
    Prepare.file()
    print(test_get_latency_auditd('open'))
    Prepare.clean()
    #

    print(test_get_latency_auditd('create'))
    Prepare.clean()
    #

    print(test_get_latency_auditd('exec'))

    #
    Prepare.file()
    print(test_get_latency_auditd('remove'))

    #
    Prepare.file()
    print(test_get_latency_auditd('chmod'))

    #
    Prepare.file()
    print(test_get_latency_auditd('chown'))
    Prepare.clean()
    #
    Prepare.file()
    print(test_get_latency_auditd('mount'))
    Prepare.clean()
    #
    Prepare.file()
    print(test_get_latency_auditd('module'))
    Prepare.clean()
    #
    Prepare.file()
    print(test_get_latency_auditd('uid'))
    Prepare.clean()
    #
    Prepare.file()
    print(test_get_latency_auditd('gid'))
    Prepare.clean()
    #

elif args.MODE == 'useraud':
    pass
elif args.MODE == 'fileaud':
    pass


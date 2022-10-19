import argparse

from time import ctime, sleep, monotonic_ns
from libs.libaub import create_ps, cmd, CheckAusearch

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
    test_ps = create_ps(args.AUDIT_FLAG)
    cmd('psaud {pid} +{flag}:-{flag}'.format(pid=test_ps.pid, flag=args.AUDIT_FLAG))
    start = monotonic_ns()
    while test_ps.is_alive() and CheckAusearch.psaud(args.AUDIT_FLAG, test_ps.pid) is False:
        end = monotonic_ns()
    latency=(end-start)//(10**6)
    print(latency)


elif args.MODE == 'useraud':
    pass
elif args.MODE == 'fileaud':
    pass

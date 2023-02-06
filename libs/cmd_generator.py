import argparse
DESCRIPTION = ""
parser = argparse.ArgumentParser(description=DESCRIPTION)
parser.add_argument('-u', '--username',
                    action='store',
                    required=True,
                    help='confluence user',
                    dest='USER')

parser.add_argument('-t', '--token',
                    action='store',
                    required=True,
                    default=None,
                    help='confluence access token',
                    dest='TOKEN')

parser.add_argument('-av',
                    action='store',
                    required=True,
                    help='astra version',
                    dest='ASTRA_VERSION')

parser.add_argument('-ts',
                    action='store',
                    choices=['auditd', 'postgresql'],
                    required=True,
                    help='astra version',
                    dest='TEST_SET')
args = parser.parse_args()


if args.TEST_SET == 'auditd':
    ### low ###
    for kernel in ('5.10.142-1-generic', '5.15.0-33-generic', '5.15.0-33-lowlatency'):
        cmd = 'sudo venv/bin/python aub_run.py -t fileaud -m default && ' \
              'sudo venv/bin/python aub_publish.py ' \
              '--username {user} ' \
              '--token {token} ' \
              '--confluence-space "~rkuznetsov" ' \
              '--confluence-parent-page "{av} ⬝ Auditd" ' \
              '--confluence-new-page FILEAUD_{av}_smolensk_{kern}_{grid}_{num} ' \
              '--test-set fileaud ' \
              '--arm-name "{g}" ' \
              '--arm-proccessor "{cpu}" ' \
              '--arm-memory "{ram}" ' \
              '--arm-storage "{disk}" ' \
              '--package auditd && ' \
              'sudo venv/bin/python aub_run.py -t psaud -m default && ' \
              'sudo venv/bin/python aub_publish.py ' \
              '--username {user} ' \
              '--token {token} ' \
              '--confluence-space "~rkuznetsov" ' \
              '--confluence-parent-page "{av} ⬝ Auditd" ' \
              '--confluence-new-page PSAUD_{av}_smolensk_{kern}_{grid}_{num} ' \
              '--test-set fileaud ' \
              '--arm-name "{g}" ' \
              '--arm-proccessor "{cpu}" ' \
              '--arm-memory "{ram}" ' \
              '--arm-storage "{disk}" ' \
              '--package auditd && ' \
              'sudo venv/bin/python aub_run.py -t useraud -m default && ' \
              'sudo venv/bin/python aub_publish.py ' \
              '--username {user} ' \
              '--token {token} ' \
              '--confluence-space "~rkuznetsov" ' \
              '--confluence-parent-page "{av} ⬝ Auditd" ' \
              '--confluence-new-page USERAUD_{av}_smolensk_{kern}_{grid}_{num} ' \
              '--test-set fileaud ' \
              '--arm-name "{g}" ' \
              '--arm-proccessor "{cpu}" ' \
              '--arm-memory "{ram}" ' \
              '--arm-storage "{disk}" ' \
              '--package auditd'.format(user=args.USER,
                                        token=args.TOKEN,
                                        av=args.ASTRA_VERSION,
                                        kern=kernel,
                                        cpu='Intel(R) Core(TM) i7-11700 CPU @ 2.50GHz',
                                        disk='Samsung NVME 970 EVO 2Тб',
                                        ram='32GB',
                                        g='low(141)',
                                        grid='low',
                                        num='141')

        print('\033[92m++++++++++++++++++++++++++++++++++ {} ++++++++++++++++++++++++++++++++++++\033[0m'.format(kernel))
        print(cmd)

    ### middle ###

    for kernel in ('5.10.142-1-generic', '5.15.0-33-generic', '5.15.0-33-lowlatency'):
        cmd = 'sudo venv/bin/python aub_run.py -t fileaud -m default && ' \
              'sudo venv/bin/python aub_publish.py ' \
              '--username {user} ' \
              '--token {token} ' \
              '--confluence-space "~rkuznetsov" ' \
              '--confluence-parent-page "{av} ⬝ Auditd" ' \
              '--confluence-new-page FILEAUD_{av}_smolensk_{kern}_{grid}_{num} ' \
              '--test-set fileaud ' \
              '--arm-name "{g}" ' \
              '--arm-proccessor "{cpu}" ' \
              '--arm-memory "{ram}" ' \
              '--arm-storage "{disk}" ' \
              '--package auditd && ' \
              'sudo venv/bin/python aub_run.py -t psaud -m default && ' \
              'sudo venv/bin/python aub_publish.py ' \
              '--username {user} ' \
              '--token {token} ' \
              '--confluence-space "~rkuznetsov" ' \
              '--confluence-parent-page "{av} ⬝ Auditd" ' \
              '--confluence-new-page PSAUD_{av}_smolensk_{kern}_{grid}_{num} ' \
              '--test-set fileaud ' \
              '--arm-name "{g}" ' \
              '--arm-proccessor "{cpu}" ' \
              '--arm-memory "{ram}" ' \
              '--arm-storage "{disk}" ' \
              '--package auditd && ' \
              'sudo venv/bin/python aub_run.py -t useraud -m default && ' \
              'sudo venv/bin/python aub_publish.py ' \
              '--username {user} ' \
              '--token {token} ' \
              '--confluence-space "~rkuznetsov" ' \
              '--confluence-parent-page "{av} ⬝ Auditd" ' \
              '--confluence-new-page USERAUD_{av}_smolensk_{kern}_{grid}_{num} ' \
              '--test-set fileaud ' \
              '--arm-name "{g}" ' \
              '--arm-proccessor "{cpu}" ' \
              '--arm-memory "{ram}" ' \
              '--arm-storage "{disk}" ' \
              '--package auditd'.format(user=args.USER,
                                        token=args.TOKEN,
                                        av=args.ASTRA_VERSION,
                                        kern=kernel,
                                        cpu='Intel(R) Xeon(R) CPU E5-2620 v3 @ 2.40GHz',
                                        disk='Patriot Burst El 960GB',
                                        ram='32GB',
                                        g='middle(151)',
                                        grid='middle',
                                        num='151')

        print('\033[92m++++++++++++++++++++++++++++++++++ {} ++++++++++++++++++++++++++++++++++++\033[0m'.format(kernel))
        print(cmd)

    ### high ###

    for kernel in ('5.10.142-1-generic', '5.15.0-33-generic', '5.15.0-33-lowlatency'):
        cmd = 'sudo venv/bin/python aub_run.py -t fileaud -m default && ' \
              'sudo venv/bin/python aub_publish.py ' \
              '--username {user} ' \
              '--token {token} ' \
              '--confluence-space "~rkuznetsov" ' \
              '--confluence-parent-page "{av} ⬝ Auditd" ' \
              '--confluence-new-page FILEAUD_{av}_smolensk_{kern}_{grid}_{num} ' \
              '--test-set fileaud ' \
              '--arm-name "{g}" ' \
              '--arm-proccessor "{cpu}" ' \
              '--arm-memory "{ram}" ' \
              '--arm-storage "{disk}" ' \
              '--package auditd && ' \
              'sudo venv/bin/python aub_run.py -t psaud -m default && ' \
              'sudo venv/bin/python aub_publish.py ' \
              '--username {user} ' \
              '--token {token} ' \
              '--confluence-space "~rkuznetsov" ' \
              '--confluence-parent-page "{av} ⬝ Auditd" ' \
              '--confluence-new-page PSAUD_{av}_smolensk_{kern}_{grid}_{num} ' \
              '--test-set fileaud ' \
              '--arm-name "{g}" ' \
              '--arm-proccessor "{cpu}" ' \
              '--arm-memory "{ram}" ' \
              '--arm-storage "{disk}" ' \
              '--package auditd && ' \
              'sudo venv/bin/python aub_run.py -t useraud -m default && ' \
              'sudo venv/bin/python aub_publish.py ' \
              '--username {user} ' \
              '--token {token} ' \
              '--confluence-space "~rkuznetsov" ' \
              '--confluence-parent-page "{av} ⬝ Auditd" ' \
              '--confluence-new-page USERAUD_{av}_smolensk_{kern}_{grid}_{num} ' \
              '--test-set fileaud ' \
              '--arm-name "{g}" ' \
              '--arm-proccessor "{cpu}" ' \
              '--arm-memory "{ram}" ' \
              '--arm-storage "{disk}" ' \
              '--package auditd'.format(user=args.USER,
                                        token=args.TOKEN,
                                        av=args.ASTRA_VERSION,
                                        kern=kernel,
                                        cpu='Intel(R) Core(TM) i5-8600K CPU @ 3.60GHz',
                                        disk='Patriot Burst El 960GB',
                                        ram='125GB',
                                        g='high(150)',
                                        grid='high',
                                        num='150')

        print('\033[92m++++++++++++++++++++++++++++++++++ {} ++++++++++++++++++++++++++++++++++++\033[0m'.format(kernel))
        print(cmd)

elif args.TEST_SET == 'postgresql':
    ### low ###
    for kernel in ('5.10.142-1-generic', '5.15.0-33-generic', '5.15.0-33-lowlatency'):
        cmd = 'sudo venv/bin/python psb_run.py -t base -m default -db -c -sn 1 && ' \
              'sudo venv/bin/python psb_publish.py ' \
              '--username {user} ' \
              '--token {token} ' \
              '--confluence-space "~rkuznetsov" ' \
              '--confluence-parent-page "{av} ⬝ PostgreSQL" ' \
              '--confluence-new-page PostgreSQL_{av}_orel_{kern}_{grid}_{num} ' \
              '--arm-name "{g}" ' \
              '--arm-proccessor "{cpu}" ' \
              '--arm-memory "{ram}" ' \
              '--arm-storage "{disk}" ' \
              '--package postgresql'.format(user=args.USER,
                                        token=args.TOKEN,
                                        av=args.ASTRA_VERSION,
                                        kern=kernel,
                                        cpu='Intel(R) Core(TM) i7-11700 CPU @ 2.50GHz',
                                        disk='Samsung NVME 970 EVO 2Тб',
                                        ram='32GB',
                                        g='low(141)',
                                        grid='low',
                                        num='141')

        print('\033[92m++++++++++++++++++++++++++++++++++ {} ++++++++++++++++++++++++++++++++++++\033[0m'.format(kernel))
        print(cmd)

    ### middle ###

    for kernel in ('5.10.142-1-generic', '5.15.0-33-generic', '5.15.0-33-lowlatency'):
        cmd = 'sudo venv/bin/python psb_run.py -t base -m default -db -c -sn 3 && ' \
              'sudo venv/bin/python psb_publish.py ' \
              '--username {user} ' \
              '--token {token} ' \
              '--confluence-space "~rkuznetsov" ' \
              '--confluence-parent-page "{av} ⬝ PostgreSQL" ' \
              '--confluence-new-page PostgreSQL_{av}_orel_{kern}_{grid}_{num} ' \
              '--arm-name "{g}" ' \
              '--arm-proccessor "{cpu}" ' \
              '--arm-memory "{ram}" ' \
              '--arm-storage "{disk}" ' \
              '--package postgresql'.format(user=args.USER,
                                        token=args.TOKEN,
                                        av=args.ASTRA_VERSION,
                                        kern=kernel,
                                        cpu='Intel(R) Xeon(R) CPU E5-2620 v3 @ 2.40GHz',
                                        disk='Patriot Burst El 960GB',
                                        ram='32GB',
                                        g='middle(151)',
                                        grid='middle',
                                        num='151')

        print('\033[92m++++++++++++++++++++++++++++++++++ {} ++++++++++++++++++++++++++++++++++++\033[0m'.format(kernel))
        print(cmd)

    ### high ###

    for kernel in ('5.10.142-1-generic', '5.15.0-33-generic', '5.15.0-33-lowlatency'):
        cmd = 'sudo venv/bin/python psb_run.py -t base -m default -db -c -sn 4 && ' \
              'sudo venv/bin/python psb_publish.py ' \
              '--username {user} ' \
              '--token {token} ' \
              '--confluence-space "~rkuznetsov" ' \
              '--confluence-parent-page "{av} ⬝ PostgreSQL" ' \
              '--confluence-new-page PostgreSQL_{av}_orel_{kern}_{grid}_{num} ' \
              '--arm-name "{g}" ' \
              '--arm-proccessor "{cpu}" ' \
              '--arm-memory "{ram}" ' \
              '--arm-storage "{disk}" ' \
              '--package postgresql'.format(user=args.USER,
                                        token=args.TOKEN,
                                        av=args.ASTRA_VERSION,
                                        kern=kernel,
                                        cpu='Intel(R) Core(TM) i5-8600K CPU @ 3.60GHz',
                                        disk='Patriot Burst El 960GB',
                                        ram='125GB',
                                        g='high(150)',
                                        grid='high',
                                        num='150')

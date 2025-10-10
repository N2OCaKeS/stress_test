import argparse
import subprocess
import os
import threading

from time import sleep
from statistics import median
from os.path import isfile, isdir, dirname, abspath
from os import mkdir
from datetime import datetime


PSQL_VERSION = 15


parser = argparse.ArgumentParser()
parser.add_argument('-cleare',
                    action='store_true',
                    required=False,
                    help='cleare logs',
                    dest='CLEARE')

parser.add_argument('-count',
                    action='store_true',
                    required=False,
                    help='count logs',
                    dest='COUNT')

parser.add_argument('-test',
                    action='store_true',
                    required=False,
                    help='start test',
                    dest='TEST')

parser.add_argument('-prepare',
                    action='store_true',
                    required=False,
                    help='prepare db',
                    dest='PREPARE')

parser.add_argument('-flame',
                    action='store',
                    required=False,
                    choices=['0', '1', '2', '3', '4', '5', '6'],
                    help='create flamegraph',
                    dest='FLAME')

parser.add_argument('-set_hdd',
                    action='store',
                    required=False,
                    choices=['ext4', 'xfs'],
                    help='set test storage on HDD',
                    dest='HDD')

parser.add_argument('-parsec_off',
                    action='store_true',
                    required=False,
                    help='parsec disable',
                    dest='PARSECOFF')

parser.add_argument('-i',
                    action='store',
                    required=False,
                    choices=['psqlpro',
                             'python'],
                    help='install',
                    dest='INST')

parser.add_argument('-pg_vm',
                    action='store_true',
                    required=False,
                    help='test pgpro on vm',
                    dest='PGVM')

args = parser.parse_args()


def check_output_command(command, out=None):
    result = subprocess.Popen([command], shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    output, errors = result.communicate()
    output = os.linesep.join([s for s in output.splitlines() if s])
    errors = os.linesep.join([s for s in errors.splitlines() if s])
    if errors == "":
        return output
    elif out != None:
        return errors + output
    else:
        return errors


def cmd(command):
    subprocess.run(command, shell=True)


def prepare():
    cmd(f'sudo bash psb_db_prep_manual_test.sh {PSQL_VERSION}')


def cpu_load(function):
    results = []
    command = """
            top -bn1 | grep '%Cpu' | tail -1 | awk '{gsub(",",".",$8); 
            printf "%s::%s::%s::", 100-$8, $2, $4}'; 
            free -m | awk 'NR==2{printf "%s\\n", $2-$7}'
            """
    
    def __median():
        rare_dates = [list(map(float, str(i).replace(',', '.').split('::'))) for i in results]
        return [round(median([j[i] for j in rare_dates]), 1) for i in range(len(rare_dates[0]))]
        
    while True:
        results.append(check_output_command(command))
        sleep(1)
        if not function.is_alive():
            return print(f'\nMedian CPU loads:\n[CPU::user::system::RAM]:\n{__median()}\n')
         

def start_test():
    cmd('pgbench -h localhost -p 6000 -U postgres -t 1000 -j 200 -c 200 test')
    

def test():
    start_test_thread = threading.Thread(target=start_test)
    cpu_load_thread = threading.Thread(target=cpu_load, args=(start_test_thread,))

    cmd(f'pg_ctlcluster {PSQL_VERSION} TEST start')
    cpu_load_thread.start()
    start_test_thread.start()
    start_test_thread.join()
    cmd(f'pg_ctlcluster {PSQL_VERSION} TEST stop')


def cleare():
    #Cluster
    cmd('sudo systemctl restart postgresql.service')
    cmd(f'pg_ctlcluster {PSQL_VERSION} TEST restart')
    cmd(f'pg_ctlcluster {PSQL_VERSION} TEST stop')
    #Journald
    cmd(f'journalctl --rotate --vacuum-time=1s --unit=postgresql@{PSQL_VERSION}-TEST')
    #Syslog-NG
    cmd('logrotate --force /etc/logrotate.d/syslog-ng-mod-astra')
    if os.path.isfile('perf.data'):
        cmd('sudo rm -r perf.data')
    cmd(f'sudo rm -r /var/lib/postgresql/{PSQL_VERSION}/TEST/pg_log/*')
    print('Cleared logs done\n')


def count():
    def __sum_audit_count():
        try:
            with open("/tmp/pg_count_audit.txt") as f:
                lines = f.readlines()
            return sum([int(x.split()[0]) for x in lines])
        except Exception as e:
            print(f'Error: {type(e).__name__}\nMessage: {str(e)}')

    psql_event_count = __sum_audit_count()
    journald = check_output_command('journalctl -t postgres | wc -l')
    syslog_ng = check_output_command('grep -a "postgres" /parsec/log/astra/events | wc -l')

    if os.listdir(f'/var/lib/postgresql/{PSQL_VERSION}/TEST/pg_log/'):
        comm = 'sudo grep -o "type=\'AUDIT\'" /var/lib/postgresql/{}/TEST/pg_log/{} | wc -l'
        files = os.listdir(f'/var/lib/postgresql/{PSQL_VERSION}/TEST/pg_log')
        bd_logs = sum([int(check_output_command(comm.format(PSQL_VERSION, logs))) for logs in files])
    else: bd_logs = 0

    if os.path.isfile('/tmp/pg_test_audit.log'):
        mini_server = check_output_command('grep -o "type=\'AUDIT\'" /tmp/pg_test_audit.log | wc -l')
    else: mini_server = 0

    print(f'Event count:\npsql_event_count: {psql_event_count}\njournald: {journald}')
    print(f'syslog_ng: {syslog_ng}\nbd_logs: {bd_logs}\nmini_server: {mini_server}\n')

    try:
        print(f'Summ events: {int(journald) + int(syslog_ng) + int(bd_logs) + int(mini_server)}')
    except Exception as e:
            print(f'Error: {type(e).__name__}\nMessage: {str(e)}')


def flame():
    cmd(f'pg_ctlcluster {PSQL_VERSION} TEST start')
    cmd('sudo perf record -g -a pgbench -h localhost -p 6000 -U postgres -t 1000 -j 200 -c 200 test')
    cmd(f'sudo perf script | perl libs/libstackcollapse-perf.pl | perl libs/libflamegraph.pl > result_{args.FLAME}.svg')
    cmd(f'pg_ctlcluster {PSQL_VERSION} TEST stop')
    print('Flamegraph done')


def set_hdd(part='sdb', fs=args.HDD): 
    if fs == 'xfs':
        option = 'f'
    else: option = 'F'

    if not os.path.isdir('/var/lib/postgresql'):
        os.mkdir('/var/lib/postgresql')
    else: 
        cmd('rm -r /var/lib/postgresql')
        os.mkdir('/var/lib/postgresql')

    cmd(f'parted -s /dev/{part} mklabel gpt mkpart primary {fs} 0% 100%')
    cmd(f'mkfs -t {fs} -{option} /dev/{part}1')
    cmd(f'mount /dev/{part}1 /var/lib/postgresql/')


def parsec_disable():
    """
    Отключение модуля parsec, поможет исключить его влияние на системные процессы:
    """

    cmd('lsmod | grep parseс')
    if not isdir('/etc/modprobe.d'):
        mkdir('/etc/modprobe.d/')
    with open('/etc/modprobe.d/parsec.conf', 'w') as w:
        w.write('install parsec /bin/false')

    cmd('sudo update-initramfs -u -k all')
    print('Перезагрузите стенд и проверьте корректность отключения модуля: "lsmod | grep parseс"')


def install_bd(bd=args.INST, key=None):
    """
    Для скачивания скрипта потребуется ключ
    """
    if bd == 'psqlpro':
        cmd(f'wget --user {key} --password='' https://repo.postgrespro.ru/ent/ent-17/keys/pgpro-repo-add.sh')
        cmd('sudo bash pgpro-repo-add.sh')
        cmd('sudo apt-get update -y')
        cmd('sudo apt-get install postgrespro-ent-17 -y')
        cmd('sudo apt-get install postgrespro-ent-17-contrib -y')
        cmd('/opt/pgpro/ent-17/bin/pg-setup initdb')
        cmd('/opt/pgpro/ent-17/bin/pg-setup service enable')
        cmd('/opt/pgpro/ent-17/bin/pg-setup service start')


def create_vms_test_env(mode='s',
                        key=None):
    """
    Reqiered python >= 3.12
    """
    
    VMS = ['testvm1']
    VMS_DATES = {'testvm1': {'host-port': '22', 
                            'cpu': '8', 
                            'ram': '32768',
                            'ip_bridge': '10.177.103.77'}}
    tasks = {
        'g_VMS':{
            'get_key':{
                'command': f'wget --user {key} --password='' https://repo.postgrespro.ru/ent/ent-17/keys/pgpro-repo-add.sh',
                'signal set': 'get_key', 
                'signal get': ''
            },
            'add_pgpro_repo':{
                'command': 'sudo bash pgpro-repo-add.sh',
                'signal set': 'add_pgpro_repo', 
                'signal get': ['get_key']
            },
            'apt_update':{
                'command': 'sudo apt-get update -y',
                'signal set': 'apt_update', 
                'signal get': ['add_pgpro_repo']
            },
            'install_pgpro':{
                'command': 'sudo apt-get install postgrespro-ent-17 -y && sudo apt-get install postgrespro-ent-17-contrib -y',
                'signal set': 'install_pgpro', 
                'signal get': ['apt_update']
            },
            'initdb':{
                'command': 'sudo /opt/pgpro/ent-17/bin/pg-setup initdb',
                'signal set': 'initdb', 
                'signal get': ['install_pgpro']
            },
            'start_service':{
                'command': 'sudo /opt/pgpro/ent-17/bin/pg-setup service enable && sudo /opt/pgpro/ent-17/bin/pg-setup service start',
                'signal set': 'start_service', 
                'signal get': ['initdb']
            },
            'install_perf':{
                'command': 'sudo apt-get install linux-tools-`uname -r` -y || sudo apt-get install perf -y',
                'signal set': 'install_perf', 
                'signal get': ['start_service']
            },
            'psbpro_prep':{
                'command': 'sudo bash /home/psbpro_db_prep_manual_test.sh vm',
                'signal set': 'psbpro_prep', 
                'signal get': ['install_perf']
            },
        }
    }

    perf_task = {
        'g_VMS':{
            'start_perf':{
                'command': '(sudo perf record -g -a &); PERF_PID=$!; echo "$PERF_PID" > /home/pid',
                'signal set': 'start_perf', 
                'signal get': ''
            },
        }
    }

    kill_perf_task = {
        'g_VMS':{
            'kill_perf':{
                'command': 'sudo kill -SIGINT $(cat /home/pid)',
                'signal set': 'kill_perf', 
                'signal get': ''
            },
        }
    }

    cp_prep_file = {
        'g_VMS': [
            {
                'mode': 'push', 
                'path_host': f'{dirname(abspath(__file__))}/psbpro_db_prep_manual_test.sh', 
                'path_vm': '/home/psbpro_db_prep_manual_test.sh'
            }
        ]
    }

    cp_perf_data = {
        'g_VMS': [
            {
                'mode': 'pull', 
                'path_host': f'{dirname(abspath(__file__))}/perf.data', 
                'path_vm': '/home/perf.data'
            }
        ]
    }


    install_bd(bd='psqlpro', key=key)
    from allta import Libvirt, LibvirtManager
    provider = Libvirt()

    provider.prepare()
    vm_date = provider.build(f'1.8.1.{mode}', '1.8.3.7', VMS, VMS_DATES)
    LibvirtManager.Vm.bridge(vms_date=vm_date, new_vms_date=VMS_DATES, username="u", password="1")
    sleep(90)
    if provider.check(VMS, VMS_DATES) == 0:
        provider.scp(scp_settings=cp_prep_file, vms_dates=VMS_DATES, vms_groups={'VMS':VMS})
        provider.execute(commands=tasks, vms_dates=VMS_DATES, vms_groups={'VMS':VMS})
        provider.execute(commands=perf_task, vms_dates=VMS_DATES, vms_groups={'VMS':VMS})
        cmd('sudo /opt/pgpro/ent-17/bin/pgbench -h %s -p 6000 -U postgres -t 1000 -j 30 -c 30 test' % VMS_DATES['testvm1']['ip_bridge'])
        provider.execute(commands=kill_perf_task, vms_dates=VMS_DATES, vms_groups={'VMS':VMS})
        provider.scp(scp_settings=cp_perf_data, vms_dates=VMS_DATES, vms_groups={'VMS':VMS})
        cmd(f'sudo perf script | perl libs/libstackcollapse-perf.pl | perl libs/libflamegraph.pl > result_{datetime.now().strftime("%H:%M:%S")}.svg')
        print('Flamegraph done')
    else: print('Настройка ВМ прошла неудачно')


def install_python():
    cmd('sudo bash install_python.sh')


if args.CLEARE:
    cleare()
elif args.COUNT:
    count()
elif args.PREPARE:
    prepare()
elif args.TEST:
    test()
elif args.FLAME:
    flame()
elif args.HDD:
    set_hdd()
elif args.PARSECOFF:
    parsec_disable()
elif args.INST == 'psqlpro':
    install_bd()
elif args.INST == 'python':
    install_python()
elif args.PGVM:
    create_vms_test_env()


    
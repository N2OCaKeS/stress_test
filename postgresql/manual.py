import argparse
import subprocess
import os
import threading
from time import sleep
from statistics import median


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
    cmd('sudo bash psb_db_prep_manual_test.sh 15')

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

    cmd('pg_ctlcluster 15 TEST start')
    cpu_load_thread.start()
    start_test_thread.start()
    start_test_thread.join()
    cmd('pg_ctlcluster 15 TEST stop')

def cleare():
    #Cluster
    cmd('sudo systemctl restart postgresql.service')
    cmd('pg_ctlcluster 15 TEST restart')
    cmd('pg_ctlcluster 15 TEST stop')
    #Journald
    cmd('journalctl --rotate --vacuum-time=1s --unit=postgresql@15-TEST')
    #Syslog-NG
    cmd('logrotate --force /etc/logrotate.d/syslog-ng-mod-astra')
    if os.path.isfile('perf.data'):
        cmd('sudo rm -r perf.data')
    cmd('sudo rm -r /var/lib/postgresql/15/TEST/pg_log/*')
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

    if os.listdir('/var/lib/postgresql/15/TEST/pg_log/'):
        comm = 'sudo grep -o "type=\'AUDIT\'" /var/lib/postgresql/15/TEST/pg_log/{} | wc -l'
        files = os.listdir('/var/lib/postgresql/15/TEST/pg_log')
        bd_logs = sum([int(check_output_command(comm.format(logs))) for logs in files])
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
    cmd('pg_ctlcluster 15 TEST start')
    cmd('sudo perf record -g -a pgbench -h localhost -p 6000 -U postgres -t 1000 -j 200 -c 200 test')
    cmd(f'sudo perf script | perl libs/stackcollapse-perf.pl | perl libs/flamegraph.pl > result_{args.FLAME}.svg')
    cmd('pg_ctlcluster 15 TEST stop')
    print('Flamegraph done')


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


    
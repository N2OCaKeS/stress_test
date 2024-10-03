#!/home/u/python/Python-3.12.1/venv/bin/python3.12

import subprocess
import os
import json
from time import sleep, ctime
import logging
import socket
import paramiko
from paramiko import ssh_exception
import argparse
from allta_image_conf import *
import pysnooper
from ansible.plugins.callback import CallbackBase
from ansible.executor.task_queue_manager import TaskQueueManager
from ansible.playbook.play import Play
from ansible.inventory.host import Host
from ansible.parsing.dataloader import DataLoader
from ansible.inventory.manager import InventoryManager
from ansible.vars.manager import VariableManager
from libs.zefir import ZefirResultTable, ZefirStatusAPI, response_status
import psycopg2
from psycopg2 import sql
import threading
from libs.liballta import BootOrder, comm_and_log



parser = argparse.ArgumentParser()
parser.add_argument('-sn', '--stand-num',
                    action='store',
                    choices=['1',
                             '2',
                             '3',
                             '4'],
                    required=True,
                    help='stand num',
                    dest='ST')

parser.add_argument('-rs', '--release',
                    action='store',
                    required=True,
                    help='release num',
                    dest='RELEASE')

parser.add_argument('-test', 
                    action='store',
                    required=True,
                    help='test name',
                    dest='TEST')

parser.add_argument('-mode', 
                    action='store',
                    choices=['orel',
                             'smolensk'],
                    required=True,
                    help='astra-modeswitch',
                    dest='MODE')

parser.add_argument('-kn', '--kernel',
                    action='store',
                    required=True,
                    help='kernel name',
                    dest='KERNEL')

parser.add_argument('-stand',
                    action='store',
                    required=True,
                    help='stand',
                    dest='STAND')

parser.add_argument('-tcyc',
                    action='store',
                    required=True,
                    help='test cycle name',
                    dest='TCYCLE')

parser.add_argument('-tcas',
                    action='store',
                    required=True,
                    help='test case name',
                    dest='TCASE')

parser.add_argument('-branch',
                    action='store',
                    required=True,
                    help='branch name',
                    dest='BRANCH')

parser.add_argument('-cti',
                    action='store',
                    required=True,
                    help='cycle tree index',
                    dest='CTI')

parser.add_argument('-pp',
                    action='store',
                    required=True,
                    help='parent page in conf',
                    dest='PARP')

parser.add_argument('-ps',
                    action='store',
                    required=False,
                    help='test psql',
                    dest='PSQL')

parser.add_argument('-aud',
                    action='store',
                    required=False,
                    help='testlist',
                    dest='AUDIT')

parser.add_argument('-testnum',
                    action='store',
                    required=True,
                    help='testlist',
                    dest='TESTNUM')

parser.add_argument('-psql_aud',
                    action='store',
                    required=False,
                    help='testlist',
                    dest='AUDIT_OFF')

parser.add_argument('-psql-parsec',
                    action='store',
                    required=False,
                    help='testlist',
                    dest='PSQL_PARSEC')

parser.add_argument('-psql-vanilla',
                    action='store',
                    required=False,
                    help='testlist',
                    dest='PSQL_VANILLA')

parser.add_argument('-psql-bl',
                    action='store',
                    required=False,
                    help='testlist',
                    dest='PSQL_BALANCE')

parser.add_argument('-db-kernels',
                    action='store',
                    choices=['psql',
                             'tantor'],
                    required=False,
                    help='testlist',
                    dest='DB_KERNELS')

parser.add_argument('-ovf',
                    action='store',
                    required=False,
                    help='overflow',
                    dest='OVF')

parser.add_argument('-tantor-vanilla',
                    action='store',
                    required=False,
                    help='testlist',
                    dest='TANTOR_VANILLA')

parser.add_argument('-ipa-auth ipa',
                    action='store',
                    required=False,
                    help='testlist',
                    dest='FREEIPA_AUTH')

parser.add_argument('-parsec-impact',
                    action='store',
                    required=False,
                    help='testlist',
                    dest='PARSEC_IMPACT')

parser.add_argument('-parsec-impact-ao',
                    action='store',
                    required=False,
                    help='testlist',
                    dest='PARSEC_IMPACT_AO')

parser.add_argument('-apache',
                    action='store',
                    required=False,
                    help='testlist',
                    dest='APACHE')

parser.add_argument('-lvirt',
                    action='store',
                    required=False,
                    help='testlist',
                    dest='LVIRT')


args = parser.parse_args()

with open('/home/u/tokens.json', 'r') as r:
    tokens = json.load(r)
__conf_token = tokens['conf_token']
__username = tokens['username']
__jira_token = tokens['jira_token']
success = f'Success {args.STAND} {args.TEST}'
in_prog = f'In progress {args.STAND} {args.TEST}'
fail = f'Fail {args.STAND} {args.TEST}'
done = f'Done {args.STAND} {args.TEST}'
stand_ip = stands_ip[args.STAND]
user = 'u'
password = '1'
port = 22
ipmi = BootOrder(stand=args.STAND)
clonezilla_command = cz_comm()[args.STAND][args.RELEASE]
if args.PSQL_BALANCE:
    clonezilla_command_balance = cz_comm()['stand4']['1.8.1.6']
    #clonezilla_command_balance = cz_comm()['stand4'][args.RELEASE]
branch = args.BRANCH
parent_page = args.PARP
systems = ['debian10', 'debian10-5.15', 'altlinux-5.10']
dates_name = f'dates_{args.STAND}.conf'
username = f'--username {__username}'
token = f'--token {__conf_token}'
confluence_space = "--confluence-space 'DEVQA'"
confluence_parent_page = f'--confluence-parent-page "{parent_page}"'
confluence_new_page = f'--confluence-new-page "{args.TEST}_{args.RELEASE}_{args.MODE}_{args.KERNEL}_{args.STAND}"'
if args.TEST == 'EXT4 parsec' or args.TEST == 'XFS parsec':
    fs = f'-fs {args.TEST.split()[0].lower()}'
else:
    fs = f'-fs {args.TEST.lower()}'
#ts = '-ts fs_mark_count'
sn = f'-sn {args.ST}'
fti = f'-fti {args.CTI}'
tcyc = f'-tcyc {args.TCYCLE}'
tcas = f'-tcas "{args.TCASE}"'
ba = f'-ba "{__jira_token}"'
tcv = f'-tcv {args.RELEASE}'
balance_vbox = f"-vbox {args.TCYCLE.split('_')[0]}"
pack_sql = '--package postgresql-11'
psql_version = '--package postgresql-'
tantor_pkg = '--package tantor-se-server-15'
testlist = f'--testlist {args.AUDIT}'
psql_aud_off = '-psql_aud off'
psql_parsec = '-parsec parsec'
psql_vanilla = '-psql_van pv'
tantor_vanilla = '-tantor_van tv'
lvirt_test = f'-testname {args.LVIRT}'
ovf = f'-ovf {args.OVF}'
ovf_ram_dates = f'{username} {token} {fti} {tcyc} {tcas} {ba} {tcv} -check drop'
ovf_sd_dates = f'{username} {token} {fti} {tcyc} {tcas} {ba} {tcv} -check reboot'
if args.PSQL:
    dates = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
              -db {sn} {fti} {tcyc} {tcas} {ba} {tcv} -c {pack_sql}'
elif args.PSQL_VANILLA:
    dates = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
              -db {sn} {fti} {tcyc} {tcas} {ba} {tcv} -c {pack_sql} {psql_vanilla}'
elif args.TANTOR_VANILLA:
    dates = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
              -db {sn} {fti} {tcyc} {tcas} {ba} {tcv} -c {tantor_pkg} {tantor_vanilla}'
elif args.PSQL_BALANCE:
    dates = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
              {sn} {fti} {tcyc} {tcas} {ba} {tcv} {balance_vbox}'
elif args.PSQL_PARSEC:
    dates = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
              -db {sn} {fti} {tcyc} {tcas} {ba} {tcv} -c {pack_sql} {psql_parsec}'
elif args.AUDIT_OFF:
    dates = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
              -db {sn} {fti} {tcyc} {tcas} {ba} {tcv} -c {pack_sql} {psql_aud_off}'
elif args.OVF:
    dates = f'{username} {token} {fti} {tcyc} {tcas} {ba} {tcv} {ovf}'
elif args.AUDIT:
    dates = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
              {sn} {testlist} {fti} {tcyc} {tcas} {ba} {tcv}'
elif args.TEST == 'EXT4 parsec' or args.TEST == 'XFS parsec':
    dates = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
              {fs} {sn} {fti} {tcyc} {tcas} {ba} {tcv} --parsec'
elif args.TEST == 'OCFS2':
    dates = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
              {fs} {sn} {fti} {tcyc} {tcas} {ba} {tcv} -vbox {args.RELEASE} -kernel {args.KERNEL} --libvirt'
elif args.TEST == 'syslog-ng' or args.TEST == 'unix':
    dates = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
              {sn} {fti} {tcyc} {tcas} {ba} {tcv}'
elif args.TEST == 'unix parsec':
    dates = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
              {sn} {fti} {tcyc} {tcas} {ba} {tcv} -p parsec'
elif args.FREEIPA_AUTH:
    dates = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
              {sn} {fti} {tcyc} {tcas} {ba} {tcv}'
elif args.PARSEC_IMPACT or args.PARSEC_IMPACT_AO:
    dates = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
              {sn} {fti} {tcyc} {tcas} {ba} {tcv}'
elif args.APACHE == 'rp':
    dates = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
              {sn} {fti} {tcyc} {tcas} {ba} {tcv}'
elif args.LVIRT:
    dates = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
              {sn} {fti} {tcyc} {tcas} {ba} {tcv} {balance_vbox} {lvirt_test}'
else: 
    dates = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
              {fs} {sn} {fti} {tcyc} {tcas} {ba} {tcv}'

with open(f'/home/u/git/stress_test/allta_app/{dates_name}', 'w') as w:
    w.write(dates)

home_dir = os.path.expanduser('~')
if not os.path.isdir(f'/home/u/git/stress_test/allta_app/status_{args.STAND}'):
    os.mkdir(f'/home/u/git/stress_test/allta_app/status_{args.STAND}')
status_dir = f'/home/u/git/stress_test/allta_app/status_{args.STAND}'
if not os.path.isdir(f'/home/u/git/stress_test/allta_app/logs'):
    os.mkdir(f'/home/u/git/stress_test/allta_app/logs')
except_num = 1
if os.path.isfile(f'/home/u/git/stress_test/allta_app/logs/backup_image_{args.STAND}_testnum{args.TESTNUM}.log'):
    os.remove(f'/home/u/git/stress_test/allta_app/logs/backup_image_{args.STAND}_testnum{args.TESTNUM}.log')

logging.basicConfig(
        filename=f'/home/u/git/stress_test/allta_app/logs/backup_image_{args.STAND}_testnum{args.TESTNUM}.log', 
        level=logging.DEBUG, 
        filemode='a',
        format='%(asctime)s - %(levelname)s - %(funcName)s: %(lineno)d - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
)
logging.error('\n\n\nStart logging\n')
logging.error(f'{args.TEST}_{args.RELEASE}_{args.MODE}_{args.KERNEL}_{args.STAND}\n\n\n')


class GrubCommand:
    def __init__(self,
                 hostname=stand_ip,
                 username=user,
                 password=password,
                 port=port
                 ):
        
        self.hostname = hostname
        self.username = username
        self.password = password
        self.port = port


    def ex_command(self, grubcommand):
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(hostname=self.hostname, username=self.username, password=self.password, port=self.port)
        stdin, stdout, stderr = client.exec_command(grubcommand)
        data_out = stdout.read().decode('utf-8') 
        data_err = stderr.read().decode('utf-8')
        logging.error(data_err)
        client.close()
        return data_out


#@pysnooper.snoop()
#def main():
with open(f'conf/actual_log_path_{args.STAND}.conf', 'w') as w:
    w.write(f'/home/u/git/stress_test/allta_app/logs/backup_image_{args.STAND}_testnum{args.TESTNUM}.log')

def write_status(status):
    with open(status_dir + '/status.txt', 'w') as wr:
        wr.write(status)

def read_status():
    with open(status_dir + '/status.txt', 'r') as r:
        status = r.read()
        return status
    

def jira_send_status(FTI, TCYC, TCAS, TCV, status):
    def test_cycle_status_start():
        zefir = ZefirStatusAPI(folder_tree_id=FTI,
                                test_cycle_name=TCYC,
                                test_case_name=TCAS,
                                basic_auth=__jira_token)
        if status == 'pass':
            status_code = 91
        elif status == 'fail':
            status_code = 92
        zefir.upload_status(status_code)
        zefir_table = ZefirResultTable(test_cycle_version=TCV,
                                        token=__conf_token,
                                        basic_auth=__jira_token,
                                        username=__username)
        zefir_table

    with open('JIRA_ERROR.log', 'w') as w:
        w.write('')

    start_status = 0
    while start_status == 0:
        jira_start, life_start = response_status()
        try:
            if jira_start == 200 and life_start == 200:
                test_cycle_status_start()
                start_status += 1
            else: 
                with open('JIRA_ERROR.log', 'a') as err:
                    err.write('start:\n')
                    err.write(ctime())
                    err.write(f'jira_status = {jira_start}\nlife_status = {life_start}\n')
                    err.write('---------' * 25)
                    err.write('\n\n')
                sleep(60)
        except Exception as e:
            with open('JIRA_ERROR.log', 'a') as err:
                err.write('start:\n')
                err.write(ctime())
                err.write(str(e))
                err.write('---------' * 25)
                err.write('\n\n')
                start_status += 1

#@pysnooper.snoop()
def ssh_command(command, stand_ip=stand_ip):
    client = paramiko.SSHClient()
    
    client.set_missing_host_key_policy(paramiko.WarningPolicy())
    client.connect(stand_ip, port=port, username=user, password='1')
    stdin, stdout, stderr = client.exec_command(command)
    response = stdout.read().decode().strip()
    client.close()
    return response

#@pysnooper.snoop()
def socket_available(reboot_counter=0, max_reboot_attempts=3):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex((stand_ip, 22))
    if result == 0:
        logging.debug('port is open')
    else: 
        logging.error('port is closed')

    while True:
        try:
            system_status = ssh_command('systemctl is-system-running')
            if system_status == 'running':
                logging.debug('System is running')
                sock.close()
                return True
            elif system_status == 'degraded':
                not_load_module = ssh_command("systemctl --state=failed --no-legend | awk '{print $2}'")
                logging.error(f'Some modules is not loaded: {system_status}: {not_load_module}')
                if not_load_module == 'astra-mount-lock.service':
                    return True
                else:
                    if reboot_counter >= max_reboot_attempts:
                        logging.error("Maximum reboot attempts reached. Check the system.")
                        return False
                    ssh_command('sudo reboot')
                    sleep(60)
                    sock.close()
                    return socket_available(reboot_counter = reboot_counter + 1)
            else:
                logging.error(f'System is not fully loaded yet: {system_status}')
                sleep(30)
        except paramiko.AuthenticationException:
            sleep(30)
            continue
        except ssh_exception.NoValidConnectionsError:
            sleep(30)
            continue
        except ssh_exception.SSHException:  
            logging.error('Error reading SSH protocol banner')
            sleep(30)
            continue

def check_running_system():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex((stand_ip, 22))
    if result == 0:
        logging.debug('port is open')
    else: 
        logging.error('port is closed')

    try:
        system_status = ssh_command('systemctl is-system-running')
        if system_status == 'running':
            logging.debug('System is running')
            sock.close()
            return True
        else:
            logging.error(f'System is not fully loaded yet: {system_status}')
    except paramiko.AuthenticationException:
        pass
    except ssh_exception.NoValidConnectionsError:
        pass

# def output_remote_load(stand):
#     sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
#     result = sock.connect_ex((stands_ip[stand], 22))
#     if result != 0:
#         with open('conf/load_cpu_' + stand + '.conf') as w:
#             w.write('-')
#         with open('conf/load_ram_' + stand + '.conf') as w:
#             w.write('-')
#     try:
#         output_cpu  = ssh_command("""grep 'cpu ' /proc/stat | awk '{usage=($2+$4)*100/($2+$4+$5)} END {print usage "%"}'""",
#                                   stand_ip=stand)
#         with open('conf/load_cpu_' + stand + '.conf') as w:
#             w.write(output_cpu)
#         output_ram  = ssh_command("""free -m | awk 'NR==2{printf $3 "M"}'""",
#                                   stand_ip=stand)
#         with open('conf/load_ram_' + stand + '.conf') as w:
#             w.write(output_ram)
#     except paramiko.AuthenticationException:
#         pass
#     except ssh_exception.NoValidConnectionsError:
#         pass
def output_remote_load(stand):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex((stands_ip[stand], 22))
    
    if result != 0:
        output_cpu = '-'
        output_ram = '-'
    else:
        try:
            output_cpu  = ssh_command("""grep 'cpu ' /proc/stat | awk '{usage=($2+$4)*100/($2+$4+$5)} END {print usage "%"}'""", stand_ip=stand)
            output_ram  = ssh_command("""free -m | awk 'NR==2{printf $3 "M"}'""", stand_ip=stand)
        except paramiko.AuthenticationException:
            output_cpu = 'Auth Error'
            output_ram = 'Auth Error'
        except ssh_exception.NoValidConnectionsError:
            output_cpu = 'Connection Error'
            output_ram = 'Connection Error'

    conn = psycopg2.connect(
        host="127.0.0.1",
        database="bendiks",
        user="postgre",
        password="1"
    )

    cursor = conn.cursor()

    select_query = f"SELECT * FROM main_table WHERE id = %s"
    cursor.execute(select_query, [stand])

    if cursor.fetchone() is not None:
        update_query = f"UPDATE main_table SET {stand}_cpu = %s, {stand}_ram = %s WHERE id = %s"
        data = (output_cpu, output_ram, stand)
        cursor.execute(update_query, data)
    else:
        insert_query = f"INSERT INTO main_table (id, {stand}_cpu, {stand}_ram) VALUES (%s, %s, %s)"
        data = (stand, output_cpu, output_ram)
        cursor.execute(insert_query, data)

    conn.commit()
    cursor.close()
    conn.close()
        
#@pysnooper.snoop()
def grub_default(kernel, host):
    def client_command(command):
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(hostname=host, username=user, password=password, port=port)
        stdin, stdout, stderr = client.exec_command(command)
        data_out = stdout.read().decode('utf-8') 
        data_err = stderr.read().decode('utf-8')
        logging.error(data_err)
        client.close()
        return data_out

    client_command(f"dpkg -s linux-image-{kernel} &> /dev/null || sudo apt-get install linux-image-{kernel} -y")
    kernel_conf = client_command("sudo cat /boot/grub/grub.cfg | grep menuentry_id | \
                                    awk '{{print $17}}' | grep {} | tr -d \"'\"".format(kernel)).rstrip('\n')
    client_command(f'''sudo sed -i 's/GRUB_DEFAULT=.*/GRUB_DEFAULT={kernel_conf}/' /etc/default/grub''')
    if args.AUDIT_OFF or args.PARSEC_IMPACT_AO:
        client_command('''sudo sed -i 's/\(GRUB_CMDLINE_LINUX_DEFAULT=.*\)"/\\1 audit=0"/' /etc/default/grub''')
    client_command('sudo update-grub')
    logging.debug(client_command('cat /etc/default/grub | grep GRUB_DEFAULT'))
    return 0

#@pysnooper.snoop()
def create_remote_file(local_file_path, remote_file_path):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(hostname=stand_ip, username=user, password=password, port=port)
    ftp = client.open_sftp()
    files = ftp.put(local_file_path, remote_file_path)
    ftp.close()
    client.close()

#@pysnooper.snoop()
def send_remote_command(command):
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(hostname=stand_ip, username=user, password=password, port=port)
    ssh.get_transport().set_keepalive(60)
    chanel = ssh.get_transport().open_session()
    chanel.get_pty()
    chanel.exec_command(command)
    output = chanel.makefile().read().decode('utf-8')
    err_output = chanel.makefile_stderr().read().decode('utf-8')
    logging.debug(output)
    logging.error(err_output)
    ssh.close()



# class BootOrder:
#     def __init__(self,
#                  stand=None,
#                  boottype='PXE'):
        
#         self.stand = stand
#         self.boot_type = boottype
#         self.show_config = 'show /system1/bootconfig1/oemhp_uefibootsource'
#         self.set_new_config = 'set /system1/bootconfig1/oemhp_uefibootsource{} bootorder=1'
#         self.old_mode_key = '-oKexAlgorithms=+diffie-hellman-group1-sha1'
#         self.no_fprint = '-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null'
#         self.reset_machine = 'reset /system1'
#         self.slot_count = 5
#         if os.path.isfile('/home/u/ilo.json'):
#             with open('/home/u/ilo.json', 'r') as ilocfg:
#                 self.ilo = json.load(ilocfg)
#         self.login = self.ilo[self.stand]['username']
#         self.password = self.ilo[self.stand]['password']
#         self.address = self.ilo[self.stand]['ip']
#         self.ssh_command = f'sshpass -p "{self.password}" ssh {self.no_fprint} {self.old_mode_key} -l {self.login} {self.address}'

#     def cmd(self, cmd):
#         output = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE).stdout.decode("utf-8")
#         return output

#     def set_boot_order(self):        
#         try:
#             for i in range(0, self.slot_count + 1, 1):
#                 answer = self.cmd(f'{self.ssh_command} {self.show_config}{i}')
#                 if self.boot_type in answer and i == 1:
#                     logging.debug(f'\033[93m{self.boot_type} загрузка уже в приоритете, настройка не требуется\033[0m\n')
#                     break
#                 elif self.boot_type in answer and i != 1:
#                     logging.debug(f'\033[93m{answer}\033[0m')
#                     result = self.cmd(f'{self.ssh_command} {self.set_new_config}'.format(i))
#                     if 'Bootorder being set' in result:
#                         logging.debug(f'\033[92mПриоритет загрузки успешно изменен на {self.boot_type}\033[0m\n')
#                     break
#         except Exception as e:
#             logging.error(f'Type:{type(e).__name__}, \nMessage:{str(e)}')

#     def reset_by_timer(self, func):
#         timer = 7200
#         interval = 60
#         for _ in range(timer // interval):
#             sleep(interval)
#             if not func.is_alive():
#                 return 0
#         logging.debug(f'Время ожидания {timer} сек. Истекло, будет выполнена перезагрузка')
#         logging.debug(self.cmd(f'{self.ssh_command} {self.reset_machine}'))

#     def reset(self):
#         logging.debug('execute IPMI hard reboot')
#         logging.debug(self.cmd(f'{self.ssh_command} {self.reset_machine}'))



class TestRunProvision(BootOrder):
    def __init__(self, 
                 stand=None, 
                 boottype='PXE',
                 bootorder=True,
                 clonezilla=True,
                 stand_ip=stand_ip,
                 kernel=args.KERNEL,
                 modes=True):
        super().__init__(stand, boottype)

        self.bootorder = bootorder
        self.clonezilla = clonezilla
        self.stand_ip = stand_ip
        self.kernel = kernel
        self.modes = modes

        if self.bootorder:
            self.set_boot_order()

    def provision(self):
        if self.clonezilla:
            if args.PSQL_BALANCE:
                if comm_and_log(clonezilla_command_balance) == 0:
                    write_status(success)
                else: write_status(fail)
            else:
                if comm_and_log(clonezilla_command) == 0:
                    write_status(success)
                else: write_status(fail)
            logging.debug('Clonezilla block done\n')

        if read_status() == success:
            write_status(in_prog)
            #comm_and_log('sshpass -v -p 1 ssh -o StrictHostKeyChecking=no \
            #            -o UserKnownHostsFile=/dev/null u@' + self.stand_ip + ' sudo reboot')
            ipmi.reset()
            sleep(3)
            if args.RELEASE not in systems and not args.PSQL_BALANCE:
                holder = 0
                while holder == 0:
                    try:
                        if grub_default(self.kernel, self.stand_ip) == 0:
                            holder += 1
                        else: sleep(60)
                    except Exception as e:
                        logging.error(str(e))
                        sleep(60)
                if self.modes:
                    comm_and_log('sshpass -v -p 1 ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
                                u@' + self.stand_ip + " sudo sed -i '/auth[[:space:]]*required[[:space:]]*pam_lastlog.so[[:space:]]*inactive=/s/^/#/' /etc/pam.d/common-auth")
                    comm_and_log('sshpass -v -p 1 ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
                                u@' + self.stand_ip + " cat /etc/pam.d/common-auth")
                    comm_and_log('sshpass -v -p 1 ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
                                u@' + self.stand_ip + ' sudo astra-modeswitch set ' + modes[args.MODE])
                    if modes[args.MODE] == '2':
                        comm_and_log(f'sshpass -v -p 1 ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null u@{stand_ip} sudo astra-mac-control enable')
                        comm_and_log(f'sshpass -v -p 1 ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null u@{stand_ip} sudo astra-mic-control enable')
            elif args.PSQL_BALANCE:
                socket_available()
            write_status(success)
            logging.debug('Grub block done\n')

        if args.RELEASE not in systems and not args.PSQL_BALANCE:
            if read_status() == success:
                write_status(in_prog)
                #comm_and_log('sshpass -v -p 1 ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
                #            u@' + self.stand_ip + ' sudo reboot')
                ipmi.reset()
                sleep(3)
                socket_available()
                write_status(success)
        else: 
            socket_available()



class ResultCallback(CallbackBase):
    def __init__(self, *args, **kwargs):
        super(ResultCallback, self).__init__(*args, **kwargs)
        self.output = ""

    def v2_runner_on_ok(self, result, **kwargs):
        self.output += result._result.get('stdout', '') + "\n"

def send_remote_command_ansible(command):
    loader = DataLoader()
    inventory_manager = InventoryManager(loader=loader, sources='localhost,')
    variable_manager = VariableManager(loader=loader, inventory=inventory_manager)
    
    host = Host(name=stand_ip)
    inventory_manager._hosts[host.name] = host
    
    play_source = dict(
        name = "Ansible Play",
        hosts = stand_ip,
        gather_facts = 'no',
        tasks = [
            dict(action=dict(module='command', args=dict(cmd=command)))
        ]
    )

    play = Play().load(play_source, variable_manager=variable_manager, loader=loader)
    tqm = None
    try:
        result_callback = ResultCallback()
        tqm = TaskQueueManager(
            inventory=inventory_manager,
            variable_manager=variable_manager,
            loader=loader,
            passwords=dict(vault_pass='1'),
            stdout_callback=result_callback,
        )
        tqm.run(play)
    finally:
        if tqm is not None:
            tqm.cleanup() 
    return result_callback.output.strip()

# def send_remote_command(command):
#     ssh = paramiko.SSHClient()
#     ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
#     ssh.connect(hostname=stand_ip, username=user, password=password, port=port)
#     chanel = ssh.get_transport().open_session()
#     chanel.get_pty()
#     chanel.exec_command(command)

#     while not chanel.exit_status_ready():
#         if chanel.recv_ready():
#             output = chanel.recv(1024).decode('utf-8')
#             logging.debug(output)
#         if chanel.recv_stderr_ready():
#             err_output = chanel.recv_stderr(1024).decode('utf-8')
#             logging.error(err_output)
#     ssh.close()

def db_kernel_changer(cpu_count, database, position=None):
    grub = GrubCommand()
    set_count = f'''sudo sed -i 's/\(GRUB_CMDLINE_LINUX_DEFAULT=.*\)"/\\1 maxcpus={cpu_count}"/' /etc/default/grub'''
    update = 'sudo update-grub'
    test_args = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
                   {sn} {fti} {tcyc} {tcas} {ba} {tcv} -q {cpu_count}'
    begin_args = test_args + ' -sf begin'
    end_args = test_args + ' -sf end'

    if position == 'begin':
        if database == 'tantor':
            dates = begin_args + f' {tantor_pkg} -db tantor'
        elif database == 'psql':
            dates = begin_args + f' {psql_version}'
    elif position == 'end':
        if database == 'tantor':
            dates = end_args + f' {tantor_pkg} -db tantor'
        elif database == 'psql':
            dates = end_args + f' {psql_version}'
    else:
        if database == 'tantor':
            dates = test_args + f' {tantor_pkg} -db tantor'
        elif database == 'psql':
            dates = test_args + f' {psql_version}'
    
    with open(f'/home/u/git/stress_test/allta_app/{dates_name}', 'w') as w:
        w.write(dates)
    
    grub.ex_command(set_count)
    grub.ex_command(update)
    comm_and_log('sshpass -v -p 1 ssh -o StrictHostKeyChecking=no \
                -o UserKnownHostsFile=/dev/null u@' + stand_ip + ' sudo reboot')
    socket_available()
    create_remote_file(f'/home/u/git/stress_test/allta_app/{dates_name}', f'/home/u/{dates_name}')

    if position == 'begin':
        create_remote_file('/home/u/git/stress_test/allta_app/starter.sh', '/home/u/starter.sh')
        send_remote_command(f'sudo bash /home/u/starter.sh {branch} {dates_name} {args.RELEASE} kernel')
    else: 
        if database == 'tantor':
            send_remote_command('sudo systemctl restart tantor-se-server-15.service')
            send_remote_command(f'cd /home/u/git/stress_test/{branch}/ && sudo {VENV_PATH} run.py -n {dates_name} -kn kernel')
        else:
            send_remote_command(f'cd /home/u/git/stress_test/{branch}/ && sudo {VENV_PATH} run.py -n {dates_name} -kn kernel')
        
    write_status(done)    

def freeipa_authentication_test():
    git_path = '/home/u/freeipa_test/gitipa'
    all_path = '/home/u/freeipa_test/gitipa/stress_test/freeipa'
    clients_ip = '10.177.103.201'
    kernel = '5.15.0-83-generic'

    if comm_and_log(cz_comm()['stand1']['1.7.5']) == 0:
        write_status(success)
    run_provision.bootorder = False
    run_provision.clonezilla = False
    run_provision.stand_ip = clients_ip
    run_provision.kernel = kernel
    run_provision.modes = False
    run_provision.provision()

    comm_and_log(f'cd {git_path} && {VENV_PATH} git_clone.py')
    comm_and_log(f'cd {git_path}/stress_test && git checkout freeipa')
    comm_and_log(f'cd {all_path} && {VENV_PATH} ipa_run.py {dates}')

    write_status(done)



######################################################################################################################
######################################################################################################################


with open(f'conf/work_status_{args.STAND}.conf', 'w') as wr:
        wr.write('Запущен')
write_status(in_prog)

run_provision = TestRunProvision(stand=args.STAND)
provision_thread = threading.Thread(target=run_provision.provision)
provision_thread.start()

reset_thread = threading.Thread(target=run_provision.reset_by_timer, args=(provision_thread,))
reset_thread.start()

provision_thread.join()

#dates.conf
create_remote_file(f'/home/u/git/stress_test/allta_app/{dates_name}', f'/home/u/{dates_name}')
#starter
create_remote_file('/home/u/git/stress_test/allta_app/starter.sh', '/home/u/starter.sh')
#stand_number
#with open('/home/u/git/stress_test/stand_number.conf', 'w') as wr:
#    wr.write(args.ST)
#create_remote_file('/home/u/git/stress_test/stand_number.conf', '/home/u/stand_number.conf')

if read_status() == success:
    write_status(in_prog)
    #comm_and_log('sshpass -v -p 1 ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
    #             u@' + stand_ip + ' sudo bash /home/u/starter.sh ' + branch + ' ' + dates_name)
    if args.OVF == 'ram':
        send_remote_command(f'sudo bash /home/u/starter.sh {branch} {dates_name} {args.RELEASE}')
        with open(f'/home/u/git/stress_test/allta_app/{dates_name}', 'w') as w:
            w.write(ovf_ram_dates)
        create_remote_file(f'/home/u/git/stress_test/allta_app/{dates_name}', f'/home/u/{dates_name}')
        send_remote_command(f'sudo bash /home/u/starter.sh {branch} {dates_name} {args.RELEASE}')
        write_status(done)
    elif args.OVF == 'sd':
        send_remote_command(f'sudo bash /home/u/starter.sh {branch} {dates_name} {args.RELEASE}')
        jira_send_status(FTI=args.CTI, 
                            TCYC=args.TCYCLE, 
                            TCAS=args.TCASE, 
                            TCV=args.RELEASE, 
                            status='fail')
        comm_and_log('sshpass -v -p 1 ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
                    u@' + stand_ip + ' sudo reboot')
        sleep(600)
        if check_running_system:
            jira_send_status(FTI=args.CTI, 
                            TCYC=args.TCYCLE, 
                            TCAS=args.TCASE, 
                            TCV=args.RELEASE, 
                            status='pass')
        write_status(done)
    elif args.DB_KERNELS == 'psql' or args.DB_KERNELS == 'tantor':
        db_kernel_changer(8, args.DB_KERNELS, position='begin')
        db_kernel_changer(16, args.DB_KERNELS)
        db_kernel_changer(24, args.DB_KERNELS)
        db_kernel_changer(32, args.DB_KERNELS, position='end')
    elif args.PSQL_BALANCE:
        send_remote_command(f'sudo bash /home/u/starter.sh {branch} {dates_name} {args.RELEASE} balance')
    elif args.FREEIPA_AUTH:
        freeipa_authentication_test()
    else:    
        send_remote_command(f'sudo bash /home/u/starter.sh {branch} {dates_name} {args.RELEASE}')
        write_status(done)

with open(f'conf/work_status_{args.STAND}.conf', 'w') as wr:
        wr.write('Готово')

#main()




    

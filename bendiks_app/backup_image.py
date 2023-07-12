#!/bin/python3

import subprocess
import os
import json
from time import sleep
import logging
from tempfile import mkstemp
import socket
import paramiko
from backup_image_command import cz_comm
import argparse
from backup_image_conf import *


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
clonezilla_command = cz_comm[args.STAND][args.RELEASE]
branch = args.BRANCH
parent_page = args.PARP
dates_name = f'dates_{args.STAND}.conf'
username = f'--username {__username}'
token = f'--token {__conf_token}'
confluence_space = "--confluence-space 'DD'"
confluence_parent_page = f'--confluence-parent-page "{parent_page}"'
confluence_new_page = f'--confluence-new-page "{args.TEST}_{args.RELEASE}_{args.MODE}_{args.KERNEL}_{args.STAND}"'
if args.TEST == 'EXT4 parsec':
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
pack_sql = '--package postgresql-11'
testlist = f'--testlist {args.AUDIT}'
if args.PSQL:
    dates = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
              -db {sn} {fti} {tcyc} {tcas} {ba} {tcv} -c {pack_sql}'
elif args.AUDIT:
    dates = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
              {sn} {testlist} {fti} {tcyc} {tcas} {ba} {tcv}'
elif args.TEST == 'EXT4 parsec':
    dates = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
              {fs} {sn} {fti} {tcyc} {tcas} {ba} {tcv} --parsec'
elif args.TEST == 'syslog-ng' or args.TEST == 'unix':
    dates = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
              {sn} {fti} {tcyc} {tcas} {ba} {tcv}'
else: 
    dates = f'{username} {token} {confluence_space} {confluence_parent_page} {confluence_new_page} \
              {fs} {sn} {fti} {tcyc} {tcas} {ba} {tcv}'

with open(f'/home/u/git/stress_test/bendiks_app/{dates_name}', 'w') as w:
    w.write(dates)

home_dir = os.path.expanduser('~')
if not os.path.isdir(f'/home/u/git/stress_test/bendiks_app/status_{args.STAND}'):
    os.mkdir(f'/home/u/git/stress_test/bendiks_app/status_{args.STAND}')
status_dir = f'/home/u/git/stress_test/bendiks_app/status_{args.STAND}'
if not os.path.isdir(f'/home/u/git/stress_test/bendiks_app/logs'):
    os.mkdir(f'/home/u/git/stress_test/bendiks_app/logs')
except_num = 1
if os.path.isfile(f'/home/u/git/stress_test/bendiks_app/logs/backup_image_{args.STAND}_testnum{args.TESTNUM}.log'):
    os.remove(f'/home/u/git/stress_test/bendiks_app/logs/backup_image_{args.STAND}_testnum{args.TESTNUM}.log')

logging.basicConfig(
        filename=f'/home/u/git/stress_test/bendiks_app/logs/backup_image_{args.STAND}_testnum{args.TESTNUM}.log', 
        level=logging.DEBUG, 
        filemode='a',
        format='%(asctime)s - %(levelname)s - %(funcName)s: %(lineno)d - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
)
logging.error('\n\n\nStart logging\n')
logging.error(f'{args.TEST}_{args.RELEASE}_{args.MODE}_{args.KERNEL}_{args.STAND}\n\n\n')


with open(f'conf/actual_log_path_{args.STAND}.conf', 'w') as w:
    w.write(f'/home/u/git/stress_test/bendiks_app/logs/backup_image_{args.STAND}_testnum{args.TESTNUM}.log')

def write_status(status):
    with open(status_dir + '/status.txt', 'w') as wr:
        wr.write(status)

def read_status():
    with open(status_dir + '/status.txt', 'r') as r:
        status = r.read()
        return status
    
fd, temp_file_err = mkstemp(dir='/tmp/', suffix='log', text=True)
fd, temp_file_out = mkstemp(dir='/tmp/', suffix='log', text=True)

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

def command(command, fd_close=False):
    f_err = open(temp_file_err, 'w')
    f_out = open(temp_file_out, 'w')
    result = subprocess.Popen([command], shell=True, stderr=f_err, stdout=f_out)
    output, error = result.communicate()
    text_comm = command
    #rc = result.wait()
    f_err.close()
    f_out.close()

    with open(temp_file_err) as r:
        data_err = r.read()
    with open(temp_file_out) as r:
        data_out = r.read()
        
    if fd_close == True:
        os.close(fd)

    return result.returncode, data_out, data_err, text_comm

def comm_and_log(comm):
    code, output, error, text_comm = command(comm)
    try:
        if error != '':
            logging.error(text_comm)
            logging.error('ErrorCode ' + f'{code}')
            logging.error(error)
            os.unlink(temp_file_err)
        if output != '':
            logging.debug(output)
            os.unlink(temp_file_out)
    except Exception as e:
        global except_num
        logging.error(f'Исключение №{except_num}\n{e}')
        #print('Обнаружено исключение №{}, событие записано в лог'.format(except_num))
        except_num = except_num + 1
    return code

def socket_available():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex((stand_ip, 22))
    if result == 0:
        logging.debug('port is open')
    else: logging.error('port is closed')
    sock.close()
    return result

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

    kernel_conf = client_command("sudo cat /boot/grub/grub.cfg | grep menuentry_id | \
                                    awk '{{print $17}}' | grep {} | tr -d \"'\"".format(kernel)).rstrip('\n')
    client_command(f'''sudo sed -i 's/GRUB_DEFAULT=.*/GRUB_DEFAULT={kernel_conf}/' /etc/default/grub''')
    client_command('sudo update-grub')
    logging.debug(client_command('cat /etc/default/grub | grep GRUB_DEFAULT'))
    return 0

def create_remote_file(local_file_path, remote_file_path):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(hostname=stand_ip, username=user, password=password, port=port)
    ftp = client.open_sftp()
    files = ftp.put(local_file_path, remote_file_path)
    ftp.close()
    client.close()

def send_remote_command(command):
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(hostname=stand_ip, username=user, password=password, port=port)
    chanel = ssh.get_transport().open_session()
    chanel.get_pty()
    chanel.exec_command(command)
    output = chanel.makefile().read().decode('utf-8')
    err_output = chanel.makefile_stderr().read().decode('utf-8')
    #stdin, stdout, stderr = ssh.exec_command(command)
    #output = stdout.read().decode('utf-8')
    #err_output = stderr.read().decode('utf-8')
    logging.debug(output)
    logging.error(err_output)
    ssh.close()

with open(f'conf/work_status_{args.STAND}.conf', 'w') as wr:
        wr.write('Запущен')
write_status(in_prog)
if comm_and_log(clonezilla_command) == 0:
    write_status(success)
else: write_status(fail)

if read_status() == success:
    write_status(in_prog)
    comm_and_log('sshpass -v -p 1 ssh -o StrictHostKeyChecking=no \
                -o UserKnownHostsFile=/dev/null u@' + stand_ip + ' sudo reboot')
    sleep(3)
    if args.RELEASE != 'debian10':
        holder = 0
        while holder == 0:
            try:
                if grub_default(args.KERNEL, stand_ip) == 0:
                    holder += 1
                else: sleep(60)
            except Exception as e:
                logging.error(e)
                sleep(60)
        comm_and_log('sshpass -v -p 1 ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
                    u@' + stand_ip + ' sudo astra-modeswitch set ' + modes[args.MODE])
    write_status(success)

if args.RELEASE != 'debian10':
    if read_status() == success:
        write_status(in_prog)
        comm_and_log('sshpass -v -p 1 ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
                    u@' + stand_ip + ' sudo reboot')
        sleep(3)
        while socket_available() != 0:
            sleep(30)
        write_status(success)

#dates.conf
create_remote_file(f'/home/u/git/stress_test/bendiks_app/{dates_name}', f'/home/u/{dates_name}')
#starter
create_remote_file('/home/u/git/stress_test/bendiks_app/starter.sh', '/home/u/starter.sh')
#stand_number
#with open('/home/u/git/stress_test/stand_number.conf', 'w') as wr:
#    wr.write(args.ST)
#create_remote_file('/home/u/git/stress_test/stand_number.conf', '/home/u/stand_number.conf')

if read_status() == success:
    write_status(in_prog)
    #comm_and_log('sshpass -v -p 1 ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
    #             u@' + stand_ip + ' sudo bash /home/u/starter.sh ' + branch + ' ' + dates_name)
    send_remote_command(f'sudo bash /home/u/starter.sh {branch} {dates_name}')
    write_status(done)

with open(f'conf/work_status_{args.STAND}.conf', 'w') as wr:
        wr.write('Готово')
        
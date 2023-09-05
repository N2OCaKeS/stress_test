from flask import (render_template, 
                   request,  
                   redirect, 
                   url_for, 
                   jsonify)
import string
import random
from collections import deque
import subprocess
from os import (path, 
                remove,  
                setsid)
from multiprocessing import Process
import paramiko
from paramiko import ssh_exception
import socket
import psycopg2
from backup_image_conf import (psyc,
                               stands_ip)
from time import sleep




main_options = sorted(['XFS', 'EXT4', 'NTFS', 'EXT4 parsec', 'postgresql', 'postgresql-sm', 
                  'auditd-p', 'auditd-u', 'auditd-f', 'syslog-ng', 'unix', 'postgresql-aud-off', 'SD-overflow', 'RAM-overflow'])
brest_options = ['apache-graph']

releases = ['1.7.1', '1.7.2', '1.7.3', '1.7.3.UU.1', '1.7.3.UU.2', '1.7.4', '1.7.4.UU.1', '1.7.5']
kernels = ['5.10.0-1057-generic', '5.10.142-1-generic', '5.15.0-33-generic', '5.15.0-33-lowlatency', 
           '5.10.176-1-generic', '5.15.0-70-generic', '5.15.0-70-lowlatency', '5.10.190-1-generic']

main_stands = ['stand1', 'stand2', 'stand3', 'stand4']
mobile_stands = ['stand1', 'stand2', 'stand3', 'stand4']
brest_stands = ['stand10', 'stand11', 'stand12']

#main_url = generate_random_string(60)
red_gif = 'http://10.177.103.10:8000/static/red_ring_64.gif'
ping_gif = 'http://10.177.103.10:8000/static/ping.gif'
green_gif = 'http://10.177.103.10:8000/static/blue_ring_64.gif'
done_gif = 'http://10.177.103.10:8000/static/done.gif'

with open('/home/u/url', 'r') as r:
    main_url = r.read().replace('\n', '').replace('\r', '')
with open('/home/u/url_mob', 'r') as r:
    mobile_url = r.read().replace('\n', '').replace('\r', '')
with open('/home/u/url_brest', 'r') as r:
    brest_url = r.read().replace('\n', '').replace('\r', '')

user_app = 'user'
user = 'u'
port = 22
with open('/home/u/up', 'r') as r:
    up = r.read()
with open('/home/u/brestp', 'r') as r:
    bp = r.read()

process_manager1 = None
process_manager2 = None
process_manager3 = None
process_manager4 = None
process_manager10 = None
process_manager11 = None
process_manager12 = None
process_list1 = []
process_list2 = []
process_list3 = []
process_list4 = []
process_list10 = []
process_list11 = []
process_list12 = []


def generate_random_string(length):
    letters_and_digits = string.ascii_letters + string.digits
    rand_string = ''.join(random.sample(letters_and_digits, length))
    return rand_string * 5


def index_page(general_page, ajax=None):
    
    if info_collector(general_page, ajax) == 'index':
        return redirect(url_for(f'index_{general_page}'))
    else: return info_collector(general_page, ajax)


def info_collector(page, ajax=None):

    tests = []
    logs = {}
    progress_logs = {}
    sett_logs = {}
    status_gif_logs = {}
    gif_mapping = {'Остановлен': red_gif, 
                   'Запущен': green_gif, 
                   'Готово': done_gif}
    stands_dict = {'main':main_stands,
                   'brest':brest_stands,
                   'mobile':mobile_stands}
    options = {'main':main_options,
               'brest':brest_options,
               'mobile':main_options}
    status_logs = {f'status_{stand}':'-' for stand in stands_dict[page]}
        
    
    for stand in stands_dict[page]:
        try:
            with open(f'conf/actual_log_path_{stand}.conf', 'r') as rl:
                real_path = rl.read()
            with open(real_path, 'r') as r:
                logs[f'{stand}_log'] = r.read()
                #logs[f'{stand}_log'] = '\n'.join(deque(r, maxlen=50))
        except FileNotFoundError:
            continue

    for stand in stands_dict[page]:
        try:
            with open(f'conf/all_output_{stand}.log', 'r') as r:
                progress_logs[f'progress_{stand}'] = r.read()
        except FileNotFoundError:
            progress_logs[f'progress_{stand}'] = ''

    for stand in stands_dict[page]:
        try:
            with open('conf/status_output_stand10.log', 'r') as rsc:
                sett_logs[f'{stand}_sett'] = rsc.read()
        except FileNotFoundError:
            sett_logs[f'{stand}_sett'] = ''

    for stand in stands_dict[page]:
        with open(f'conf/work_status_{stand}.conf', 'r') as rs:
            status = rs.read()
            status_logs[f'status_{stand}'] = status
            status_gif_logs[f'status_gif_{stand}'] = gif_mapping.get(status, ping_gif)
            if status_gif_logs[f'status_gif_{stand}'] == ping_gif:
                status_logs[f'status_{stand}'] = 'Нераспознан'
    
    if ajax == True:
        return jsonify({**status_logs,
                        **logs,
                        **status_gif_logs,
                        **sett_logs,
                        **progress_logs})    

    if page == 'mobile':
        test_list, releas_list, kernel_list = create_args('main')
    else: test_list, releas_list, kernel_list = create_args(page)
    
    if request.method == 'POST':
        selected_options = request.form.getlist('options')
        
        tests = [option for option in stands_dict[page] if option in selected_options]
        if not tests:
            tests = 'Тесты не выбраны'
        
        kernel = request.form.get('kernel')
        with open(f'conf/{page}_kernel_args.conf', 'w') as w:
            w.write(str(kernel))

        releas = request.form.getlist('releas')
        with open(f'conf/{page}_releas_args.conf', 'w') as w:
            w.write(str(releas))
        if not releas:
            releas = 'Релиз не выбран'
        
        with open(f'conf/{page}_tests_args.conf', 'w') as w:
            w.write(str(tests))
        
        return 'index'

    if page == 'brest':
        return render_template(f'{page}.html', 
                                options=options[page], 
                                test_list=test_list,
                                releas_list=releas_list,
                                kernel_list=kernel_list, 
                                releases=releases, 
                                kernels=kernels,
                                brest_url=brest_url,
                                **status_logs,
                                **logs,
                                **status_gif_logs,
                                **sett_logs,
                                **progress_logs)
    else:
        return render_template(f'{page}.html', 
                                options=options[page], 
                                test_list=test_list,
                                releas_list=releas_list,
                                kernel_list=kernel_list, 
                                releases=releases, 
                                kernels=kernels,
                                **status_logs,
                                **logs,
                                **status_gif_logs,
                                **sett_logs,
                                **progress_logs,                                                        
                                main_url=main_url,
                                mobile_url=mobile_url,
                                brest_url=brest_url)



def run_command_on_stand(num):
    process_manager = globals()[f'process_manager{num}']
    process_list = globals()[f'process_list{num}']
    command = request.form.get(f'command{num}')
    kernel = None
    if num == '1' or num == '2' or num =='3' or num == '4':
        prefix = 'main'
    elif num == '10' or num =='11' or num == '12':
        prefix = 'brest'

    if command == 'start':
        with open(f'front_stand{num}.log', 'w') as w:
            w.write('Start front logging\n\n')
        with open(f'conf/work_status_stand{num}.conf', 'w') as w:
            w.write('Запущен')
        with open(f'conf/{prefix}_tests_args.conf', 'r') as r:
            tests = r.read()
        with open(f'conf/{prefix}_releas_args.conf', 'r') as r:
            releas = str(r.read()).replace('[', '').replace(']', '').strip("'")
        
        if path.isfile(f'conf/{prefix}_kernel_args.conf'):
            with open(f'conf/{prefix}_kernel_args.conf', 'r') as r:
                kernel = r.read()

        command_to_run = f'python3 bendiks_back.py -rs {releas} -st stand4 -ts "{tests}"'
        command_to_run_kernel = f'python3 bendiks_back.py -rs {releas} -st stand4 -ts "{tests}" -kn "{kernel}"'

        def run_command_and_log(command):
            with open(f'front_stand{num}.log', 'a') as output:
                process = subprocess.Popen(command, stdout=output, stderr=output, shell=True, text=True, preexec_fn=setsid)
            process_list.append(process)
#            with open("process_list4.log", "w") as ot:
#                for process in process_list4:
#                    ot.write(str(process.pid) + '\n')

        if kernel != 'None':
            process_manager = Process(target=run_command_and_log, args=(command_to_run_kernel))
        else:
            process_manager = Process(target=run_command_and_log, args=(command_to_run))
        process_manager.start()

        if path.isfile(f'conf/{prefix}_kernel_args.conf'):
            remove(f'conf/{prefix}_kernel_args.conf')

    elif command == 'ok':
        with open(f'conf/work_status_stand{num}.conf', 'w') as w:
            w.write('Остановлен')

    elif command == 'stop':
        if process_manager is not None:
            for process in process_list:
                try:
                    process.terminate()
                except ProcessLookupError:
                    print(f"Процесс с PID {process.pid} не существует")
            process_manager = None
            process_list = []

        if path.isfile(f'conf/{prefix}_kernel_args.conf'):
            remove(f'conf/{prefix}_kernel_args.conf')
        with open(f'conf/work_status_stand{num}.conf', 'w') as w:
            w.write('Остановлен')

    return redirect(url_for(f'index_{prefix}'))



def create_args(page):
    with open(f'conf/{page}_tests_args.conf', 'r') as r:
            test_list = str(r.read())
    with open(f'conf/{page}_releas_args.conf', 'r') as r:
            releas_list = str(r.read())
    try:
        with open(f'conf/{page}_kernel_args.conf', 'r') as r:
                kernel_list = str(r.read())
    except FileNotFoundError:
        kernel_list = 'None'
    
    return test_list, releas_list, kernel_list



def ssh_command(command, stand_ip):
    client = paramiko.SSHClient()
    
    client.set_missing_host_key_policy(paramiko.WarningPolicy())
    client.connect(stand_ip, port=port, username=user, password='1')
    stdin, stdout, stderr = client.exec_command(command)
    response = stdout.read().decode().strip()
    client.close()
    return response


def output_remote_load(stand):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(2.5)
    try:
        result = sock.connect_ex((stands_ip[stand], 22))
        
        if result != 0:
            output_cpu = '-'
            output_ram = '-'
        else:
            try:
                output_cpu  = ssh_command("""top -bn1 | grep '%Cpu' | tail -1 | grep -P '(....|...) id,'|awk '{gsub(",",".",$8); print 100-$8 "%"}'""", 
                                        stand_ip=stands_ip[stand])
                output_ram  = ssh_command("""free -m | awk 'NR==2{printf $3 "M"}'""", 
                                        stand_ip=stands_ip[stand])
            except paramiko.AuthenticationException:
                output_cpu = 'Auth Error'
                output_ram = 'Auth Error'
            except (ssh_exception.NoValidConnectionsError, ssh_exception.SSHException):
                output_cpu = 'Connect Error'
                output_ram = 'Connect Error'

        conn = psycopg2.connect(
                                host=psyc['host'],
                                database=psyc['database'],
                                user=psyc['user'],
                                password=psyc['password']
                                )

        cursor = conn.cursor()

        id = 1 #row number
        update_query = f"UPDATE main_table SET {stand}_cpu = %s, {stand}_ram = %s WHERE id = %s"
        data = (output_cpu, output_ram, id)
        cursor.execute(update_query, data)

        conn.commit()
        cursor.close()
        conn.close()
    except socket.timeout:
        pass
    finally:
        conn.close()


def remote_storage_load(stand):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(2)
    try:
        result = sock.connect_ex((stands_ip[stand], 22))
        
        if result != 0:
            output_nvme = '-'
            output_sda = '-'
        else:
            try:
                output_nvme  = ssh_command("""iostat -dx | awk '/nvme0n1|nvme0c0n1/ {print $NF"%"}'""", 
                                        stand_ip=stands_ip[stand])
                output_sda  = ssh_command("""iostat -dx | awk '/sda/ {print $NF"%"}'""", 
                                        stand_ip=stands_ip[stand])
            except paramiko.AuthenticationException:
                output_nvme = 'Auth Error'
                output_sda = 'Auth Error'
            except (ssh_exception.NoValidConnectionsError, ssh_exception.SSHException):
                output_nvme = 'Connect Error'
                output_sda = 'Connect Error'

        conn = psycopg2.connect(
                                host=psyc['host'],
                                database=psyc['database'],
                                user=psyc['user'],
                                password=psyc['password']
                                )

        cursor = conn.cursor()

        id = 1 #row number
        update_query = f"UPDATE main_table SET {stand}_nvme = %s, {stand}_sda = %s WHERE id = %s"
        data = (output_nvme, output_sda, id)
        cursor.execute(update_query, data)

        conn.commit()
        cursor.close()
        conn.close()
    except socket.timeout:
        pass
    finally:
        conn.close()


def remote_sysstat_available(stand):
    ssh_command("""dpkg -s sysstat &> /dev/null || sudo apt-get install sysstat -y""", 
                stand_ip=stands_ip[stand])


def background_stat_storage_main():
    stands = main_stands

    while True:
        [remote_sysstat_available(str(stand)) for stand in stands]
        [remote_storage_load(str(stand)) for stand in stands]
        sleep(5)


def background_task_main():
    stands = main_stands

    while True:
        [output_remote_load(str(stand)) for stand in stands]
        sleep(3)


def background_task_brest():
    stands = brest_stands

    while True:
        [output_remote_load(str(stand)) for stand in stands]
        sleep(3)


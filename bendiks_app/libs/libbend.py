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
                setsid,
                getcwd)
from multiprocessing import Process
#import concurrent.futures
import paramiko
from paramiko import ssh_exception
import socket
import psycopg2
from backup_image_conf import (VENV_PATH,
                               STP_VERSION,
                               psyc,
                               stands_ip,
                               main_tests,
                               brest_tests,
                               releases,
                               kernels,
                               main_stands,
                               mobile_stands,
                               brest_stands,
                               test_run_stands,
                               rc_list,
                               releases_list,
                               repo_path,
                               LowServer_group,
                               MiddleServer_group,
                               group_tests)
from time import sleep
from libs.zefir import ZefirTestRun
import ctypes
import threading
import requests
import json



ls_group = sorted(LowServer_group)
ms_group = sorted(MiddleServer_group)
main_options = group_tests + sorted(main_tests)
brest_options = sorted(brest_tests)

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
                #logs[f'{stand}_log'] = r.read()
                logs[f'{stand}_log'] = '\n'.join(deque(r, maxlen=50))
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
            with open(f'conf/status_output_{stand}.log', 'r') as rsc:
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
            if status == 'Остановлен':
                progress_logs[f'progress_{stand}'] = ''
                logs[f'{stand}_log'] = ''
    
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
        tr_stands = request.form.getlist('stands')
        if tr_stands:
            rc_part = request.form.getlist('rc')
            releases_part = request.form.getlist('releaseslist')
            kernel_part = request.form.getlist('kernelslist')
            if rc_part:
                test_run = ZefirTestRun(use_kernels=kernel_part,
                                    stands=tr_stands,
                                    release=releases_part,
                                    rc=rc_part)
            else:
                test_run = ZefirTestRun(use_kernels=kernel_part,
                                        stands=tr_stands,
                                        release=releases_part)
            test_run.creater()
        else:
            selected_options = request.form.getlist('options')
            tests = [option for option in options[page] if option in selected_options]
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
            
            if str(tests) == "['_LowServer group']":
                with open(f'conf/{page}_tests_args.conf', 'w') as w:
                    w.write(str(ls_group))
            elif str(tests) == "['_MiddleServer group']":
                with open(f'conf/{page}_tests_args.conf', 'w') as w:
                    w.write(str(ms_group))
            else:
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
                                stands=test_run_stands,
                                kernelslist=kernels,
                                rc=rc_list, 
                                releaseslist=releases_list,
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
                                brest_url=brest_url,
                                stp_versions=STP_VERSION)



def run_command_on_stand(num):
    process_manager = globals()[f'process_manager{num}']
    process_list = globals()[f'process_list{num}']
    command = request.form.get(f'command{num}')
    kernel = None
    if num == '1' or num == '2' or num =='3' or num == '4' or num == '5':
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

        command_to_run = f'{VENV_PATH} bendiks_back.py -rs {releas} -st stand{num} -ts "{tests}"'
        command_to_run_kernel = f'{VENV_PATH} bendiks_back.py -rs {releas} -st stand{num} -ts "{tests}" -kn "{kernel}"'

        def run_command_and_log(command):
            with open(f'front_stand{num}.log', 'a') as cpu_ram_output:
                process = subprocess.Popen(command, stdout=cpu_ram_output, stderr=cpu_ram_output, shell=True, text=True, preexec_fn=setsid)
            process_list.append(process)

        if kernel != 'None':
            process_manager = Process(target=run_command_and_log, args=(command_to_run_kernel,))
        else:
            process_manager = Process(target=run_command_and_log, args=(command_to_run,))
        process_manager.start()

        if path.isfile(f'conf/{prefix}_kernel_args.conf'):
            remove(f'conf/{prefix}_kernel_args.conf')
        with open(f'conf/{prefix}_tests_args.conf', 'w') as w:
            w.write('')
        with open(f'conf/{prefix}_releas_args.conf', 'w') as w:
            w.write('')

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

    return index_page(prefix)



def create_args(page):
    with open(f'conf/{page}_tests_args.conf', 'r') as r:
            test_list = str(r.read())
    with open(f'conf/{page}_releas_args.conf', 'r') as r:
            releas_list = str(r.read())
            if releas_list == '[]':
                releas_list = 'Релиз не выбран'
    try:
        with open(f'conf/{page}_kernel_args.conf', 'r') as r:
                kernel_list = str(r.read())
                if kernel_list == 'None':
                    kernel_list = 'Ядро не выбрано'
    except FileNotFoundError:
        kernel_list = 'Ядро не выбрано'
    
    return test_list, releas_list, kernel_list



def ssh_command(command, stand_ip):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(stand_ip, port=port, username=user, password='1')
    stdin, stdout, stderr = client.exec_command(command)
    response = stdout.read().decode().strip()
    client.close()
    return response



def output_remote_load(stand):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(2.5)
    conn = None

    command = """
            top -bn1 | grep '%Cpu' | tail -1 | awk '{gsub(",",".",$8); 
            printf "%s::%s::%s::", 100-$8 "%", $2 "%", $4 "%"}'; 
            free -m | awk 'NR==2{printf "%sM\\n", $2-$7}'
            """

    try:
        result = sock.connect_ex((stands_ip[stand], 22))
        
        if result != 0:
            output_cpu = '-'
            output_ram = '-'
            output_cpu_user = '-'
            output_cpu_system = '-'
        else:
            try:
                cpu_ram_output = ssh_command(command, stand_ip=stands_ip[stand])
                cpu_ram_output = cpu_ram_output.split('::')
                output_cpu = cpu_ram_output[0]
                output_cpu_user = cpu_ram_output[1].replace(',','.')
                output_cpu_system = cpu_ram_output[2].replace(',','.')
                output_ram = cpu_ram_output[3].strip()
            except paramiko.AuthenticationException:
                output_cpu = 'Auth Error'
                output_ram = 'Auth Error'
                output_cpu_user = 'Auth Error'
                output_cpu_system = 'Auth Error'
            except (ssh_exception.NoValidConnectionsError, ssh_exception.SSHException, EOFError):
                output_cpu = 'Connect Error'
                output_ram = 'Connect Error'
                output_cpu_user = 'Connect Error'
                output_cpu_system = 'Connect Error'
            except socket.timeout as st:
                print(f'{type(st).__name__}\nНедоступен {stands_ip[stand]}, перезагружается или выключен.\n')

        conn = psycopg2.connect(
                                host=psyc['host'],
                                database=psyc['database'],
                                user=psyc['user'],
                                password=psyc['password']
                                )

        cursor = conn.cursor()

        id = 1 #row number
        update_query = f"UPDATE main_table SET {stand}_cpu = %s, {stand}_cpu_user = %s, {stand}_cpu_system = %s, {stand}_ram = %s WHERE id = %s"
        data = (output_cpu, output_cpu_user, output_cpu_system, output_ram, id)
        cursor.execute(update_query, data)

        conn.commit()
        cursor.close()
        conn.close()
    except socket.timeout:
        pass
    except IndexError as e:
        print(f'IndexError: {type(e).__name__}, Message: {str(e)}')
    except Exception as all_e:
        print(f'Error: {type(all_e).__name__}, Message: {str(all_e)}')
    finally:
        if conn:
            conn.close()


def remote_storage_load(stand):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(3.7)
    conn = None

    """
    Add temp block
    """
    if stand == 'stand1' or stand == 'stand2':
        temp_cpu_comm = 'cat /sys/class/thermal/thermal_zone1/temp'
    elif stand == 'stand3':
        temp_cpu_comm = 'cat /sys/class/thermal/thermal_zone0/temp; cat /sys/class/thermal/thermal_zone1/temp'
    elif stand == 'stand4' or stand == 'stand5':
        temp_cpu_comm = 'cat /sys/class/thermal/thermal_zone0/temp; cat /sys/class/thermal/thermal_zone1/temp'

    try:
        result = sock.connect_ex((stands_ip[stand], 22))
        
        if result != 0:
            output_nvme = '-'
            output_sda = '-'
            temp_cpu = '-'
        else:
            try:
                output_nvme  = ssh_command("""iostat -dx 1 2 | awk '/nvme0n1|nvme0c0n1/ {gsub(",", ".", $NF); \
                                              printf "%.1f%%\\n", $NF}' | tail -n 1""", 
                                        stand_ip=stands_ip[stand])
                block_device_name = ssh_command("lsblk | awk 'NR==2' | awk '{print $1;}'",
                                                stand_ip=stands_ip[stand])
                output_sda  = ssh_command("""iostat -dx 1 2 | awk '/""" + str(block_device_name) + """/ {gsub(",", ".", $NF); \
                                             printf "%.1f%%\\n", $NF}' | tail -n 1""", 
                                        stand_ip=stands_ip[stand])
                temp_cpu = ssh_command(temp_cpu_comm, stand_ip=stands_ip[stand])
            except paramiko.AuthenticationException:
                output_nvme = 'Auth Error'
                output_sda = 'Auth Error'
                temp_cpu = 'Auth Error'
            except (ssh_exception.NoValidConnectionsError, ssh_exception.SSHException):
                output_nvme = 'Connect Error'
                output_sda = 'Connect Error'
                temp_cpu = 'Connect Error'
            except socket.timeout as st:
                print(f'{type(st).__name__}\nНедоступен {stands_ip[stand]}, перезагружается или выключен.\n')

        conn = psycopg2.connect(
                                host=psyc['host'],
                                database=psyc['database'],
                                user=psyc['user'],
                                password=psyc['password']
                                )

        cursor = conn.cursor()

        id = 1 #row number
        update_query = f"UPDATE main_table SET {stand}_nvme = %s, {stand}_sda = %s, {stand}_temp_cpu = %s WHERE id = %s"
        if temp_cpu == '-' or temp_cpu == 'Auth Error' or temp_cpu == 'Connect Error':
            data = (output_nvme, output_sda, temp_cpu, id)
        else:
            if stand == 'stand1' or stand == 'stand2':
                data = (output_nvme, output_sda, f'{int(float(temp_cpu) / 1000)}°', id)
            elif stand == 'stand3' or stand == 'stand4' or stand == 'stand5':
                temp_cpu = temp_cpu.split('\n')
                data = (output_nvme, output_sda, f'{int(float(temp_cpu[0]) / 1000)}° | {int(float(temp_cpu[1]) / 1000)}°', id)
        cursor.execute(update_query, data)

        conn.commit()
        cursor.close()
        conn.close()
    except socket.timeout:
        pass
    except Exception as all_e:
        print(f'Error: {type(all_e).__name__}, Message: {str(all_e)}')
    finally:
        if conn:
            conn.close()


def remote_sysstat_available(stand):
    ssh_command("""dpkg -s sysstat &> /dev/null || sudo apt-get install sysstat -y""", 
                stand_ip=stands_ip[stand])
   

def background_stat_storage_main():
    stands = main_stands

    while True:
        [remote_storage_load(str(stand)) for stand in stands]
        sleep(4)


def background_task_main():
    stands = main_stands

    while True:
        [output_remote_load(str(stand)) for stand in stands]
        sleep(3)


# def background_stat_storage_main():
#     stands = main_stands

#     while True:
#         with concurrent.futures.ProcessPoolExecutor(max_workers=4) as executor:
#             executor.map(remote_storage_load, [str(stand) for stand in stands])
#         sleep(4)


# def background_task_main():
#     stands = main_stands

#     while True:
#         with concurrent.futures.ProcessPoolExecutor(max_workers=4) as executor:
#             executor.map(output_remote_load, [str(stand) for stand in stands])
#         sleep(3)


# def background_task_brest():
#     stands = brest_stands

#     while True:
#         with concurrent.futures.ProcessPoolExecutor(max_workers=4) as executor:
#             executor.map(output_remote_load, [str(stand) for stand in stands])
#         sleep(3)

def background_task_brest():
    stands = brest_stands

    while True:
        [output_remote_load(str(stand)) for stand in stands]
        sleep(3)


def update_settings_block():

    test_list, releas_list, kernel_list = create_args('main')
    return jsonify(test_list=test_list, 
                   releas_list=releas_list, 
                   kernel_list=kernel_list)


def get_kernels_from_rc(version_rc: str):
    pathlib = str(getcwd() + '/libs/datlib.so')
    clib = ctypes.CDLL(pathlib)

    def get_file(path, name):
        c_path = ctypes.c_char_p(bytes(path, encoding='utf8'))
        c_name = ctypes.c_char_p(bytes(name, encoding='utf8'))
        clib.download_file(c_path, c_name)

    pkg_path = repo_path[f'pkg_path_{version_rc.replace(".", "")}']
    vers_path = repo_path[f'vers_path_{version_rc.replace(".", "")}']

    get_kernels = ZefirTestRun()
    get_file(str(pkg_path), 'available_packages')
    get_file(str(vers_path), 'available_version')
    version, kernels = get_kernels.get_kernels_from_file()

    return jsonify(test_list=version, 
                   releas_list=f'''Kernels: \'{" ".join(kernels).replace(" ", "', '")}\'''', 
                   kernel_list='')



class BackgroundTasks:
    def __init__(self, target):
        self.target_function = target
        self.working = False
        self.thread = None

    def start(self):
        self.working = True
        if self.thread is None or not self.thread.is_alive(): 
            self.thread = threading.Thread(target=self.run)
            self.thread.start()

    def stop(self):
        self.working = False
        if self.thread is not None:
            self.thread.join()  

    def run(self):
        while self.working:
            self.target_function()


def get_aqs_json(path, __basic):
    url = 'https://git.astralinux.ru/projects/QA/repos/astra-qa-stand/browse/astra-config.json'
    headers = {
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:102.0) Gecko/20100101 Firefox/102.0',
            'authority': 'jira.astralinux.ru',
            'Authorization': __basic,
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'ru-RU,ru;q=0.8,en-US;q=0.5,en;q=0.3',
            'Accept-Encoding': 'gzip, deflate, br',
            'Referer': 'https://jira.astralinux.ru/secure/Tests.jspa',
            'X-Requested-With': 'XMLHttpRequest',
            'jira-project-id': '11200',
            'Connection': 'keep-alive',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin',
            'TE': 'trailers'
            }
        
    response = requests.get(url, headers=headers)
    assert response.status_code == 200, f'Request astra-config.json failed with status {response.status_code}'

    json_str = "".join(part['text'] for part in response.json()['lines'])
    correct_data = json.loads(json_str)

    with open('astra-config.json', 'w') as f:
        f.write(json.dumps(correct_data, indent=4)) 

    

    
    


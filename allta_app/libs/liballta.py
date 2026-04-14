import string
import random
import subprocess
import re
import socket
import psycopg2
import paramiko
import ctypes
import threading
import requests
import json
import logging
import redfish
import uuid
import time
import signal
import shlex
import pandas as pd


from flask import (render_template, 
                   request,  
                   redirect, 
                   url_for, 
                   jsonify)
from collections import deque
from os import (path, 
                remove,  
                setsid,
                getcwd,
                close,
                unlink,
                linesep,
                killpg,
                getpgid,
                rename)
from multiprocessing import Process
from tempfile import mkstemp
from paramiko import ssh_exception
from time import sleep
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional


from libs.zefir import ZefirTestRun, ZefirResultTable
from allta_image_conf import (
    VENV_PATH,
    stp_version,
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
    group_tests,
    stands_groups,
    JIRA_URL,
    CONFLUENCE_URL,
    GIT_URL,
    RELEASES_URL,
    ASTRA_DNS,
    releases_dict,
    cz_comm,
    allta_version,
    test_station_vms,
    allta_services_list,
    tokens,
    ilo,
    ACS_BASE_URL,
    stands_type
)



main_options = group_tests + sorted(main_tests)
brest_options = sorted(brest_tests)

#main_url = generate_random_string(60)
red_gif = 'http://10.177.103.10:8000/static/red_ring_64.gif'
red_gif_global = 'http://10.177.103.10:8000/static/wait.gif'
ping_gif = 'http://10.177.103.10:8000/static/ping.gif'
ping_gif_global = ''
green_gif = 'http://10.177.103.10:8000/static/blue_ring_64.gif'
green_gif_global = 'http://10.177.103.10:8000/static/testing.gif'
done_gif = 'http://10.177.103.10:8000/static/done.gif'
done_gif_global = 'http://10.177.103.10:8000/static/done2.gif'
busy_gif = 'http://10.177.103.10:8000/static/hend_testing.gif'
busy_gif_global = 'http://10.177.103.10:8000/static/hend_testing.gif'

with open('/home/u/url', 'r') as r:
    main_url = r.read().replace('\n', '').replace('\r', '')
with open('/home/u/url_mob', 'r') as r:
    mobile_url = r.read().replace('\n', '').replace('\r', '')
with open('/home/u/url_brest', 'r') as r:
    brest_url = r.read().replace('\n', '').replace('\r', '')

AUTH_LOGIN_URL = 'https://allta.devos.astralinux.ru:21500/api/auth/login'
AUTH_LOGOUT_URL = 'https://allta.devos.astralinux.ru:21500/api/auth/logout'
AUTH_CHECK_TOKEN_URL = 'https://allta.devos.astralinux.ru:21500/api/auth/verify'
COOKIE_NAME = 'access_token'
COOKIE_SECURE = False

user_app = 'user'
user = 'u'
__srv_pass = tokens['srv_pass']
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
process_manager13 = None
process_list1 = []
process_list2 = []
process_list3 = []
process_list4 = []
process_list10 = []
process_list11 = []
process_list12 = []
process_list13 = []
_queue_managers = {}
_workers = {}
_worker_threads = {}


fd, temp_file_err = mkstemp(dir='/tmp/', suffix='log', text=True)
fd, temp_file_out = mkstemp(dir='/tmp/', suffix='log', text=True)

def check_output_command(command, out=None):
    result = subprocess.Popen([command], shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    output, errors = result.communicate()
    output = linesep.join([s for s in output.splitlines() if s])
    errors = linesep.join([s for s in errors.splitlines() if s])
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
        close(fd)

    return result.returncode, data_out, data_err, text_comm

def comm_and_log(comm):
    code, output, error, text_comm = command(comm)
    try:
        if error != '':
            logging.error(text_comm)
            logging.error('ErrorCode ' + f'{code}')
            logging.error(error)
            unlink(temp_file_err)
        if output != '':
            logging.debug(output)
            unlink(temp_file_out)
    except Exception as e:
        global except_num
        logging.error(f'Исключение №{except_num}\n{e}')
        #print('Обнаружено исключение №{}, событие записано в лог'.format(except_num))
        except_num = except_num + 1
    return code


def generate_random_string(length):
    letters_and_digits = string.ascii_letters + string.digits
    rand_string = ''.join(random.sample(letters_and_digits, length))
    return rand_string * 5


def index_page(general_page, ajax=None):
    
    if info_collector(general_page, ajax) == 'index':
        return redirect(url_for(f'index_{general_page}'))
    else: return info_collector(general_page, ajax)


def server_diskspace_used():
    dates = {
        'system_disk_used': check_output_command("df -h | grep /dev/vda2 | awk '{print$5}'"),
        'ftp_disk_used': check_output_command("df -h | grep /dev/vdb1 | awk '{print$5}'"),
        'partimag_disk_used': check_output_command("df -h | grep /dev/vdc1 | awk '{print$5}'")
    }
    return dates



class TaskQueueManager:
    """
    Менеджер очереди для одного стенда
    """
    
    def __init__(self, 
                 stand_num: str, 
                 queue_dir: str = './conf/task_queues'):
        self.stand_num = stand_num
        self.queue_file = Path(queue_dir) / f'queue_stand{stand_num}.json'
        self.queue_dir = Path(queue_dir)
        self.queue_dir.mkdir(exist_ok=True)
        
        # Инициализируем файл, если не существует
        self._init_queue_file()
    
    def _init_queue_file(self):
        """Создать файл очереди, если не существует"""
        if not self.queue_file.exists():
            self._write_queue({
                'tasks': [],
                'status': 'idle',
                'current_task': None,
                'last_run': None
            })
    
    def _read_queue(self) -> Dict:
        """Прочитать очередь из файла"""
        with open(self.queue_file, 'r') as f:
            return json.load(f)
    
    def _write_queue(self, data: Dict):
        """Записать очередь в файл"""
        with open(self.queue_file, 'w') as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    
    def add_task(self, tests: list, release: list, kernel: list) -> str:
        """
        Добавить задачу в очередь
        
        Args:
            tests: список тестов, например ['CEPH', 'NTFS']
            release: список релизов, например ['1.7.10.64']
            kernel: список ядер, например ['5.15.0-170-generic'] или []
        
        Returns:
            str: ID созданной задачи
        """
        # Генерируем ID задачи
        task_id = f"{self.stand_num}_{int(time.time())}_{uuid.uuid4().hex[:8]}"
        
        task = {
            'id': task_id,
            'tests': str(tests),      
            'release': str(release),
            'kernel': str(kernel),
            'status': 'pending',
            'created_at': datetime.now().isoformat()
        }
        
        # Читаем текущую очередь
        queue_data = self._read_queue()
        
        # Добавляем задачу
        queue_data['tasks'].append(task)
        
        # Сохраняем обратно
        self._write_queue(queue_data)
        
        return task_id
    
    def get_tasks(self) -> List[Dict]:
        """Получить все задачи из очереди"""
        queue_data = self._read_queue()
        return queue_data.get('tasks', [])
    
    def get_pending_tasks(self) -> List[Dict]:
        """Получить только ожидающие задачи"""
        queue_data = self._read_queue()
        return [t for t in queue_data.get('tasks', []) if t.get('status') == 'pending']
    
    def clear_queue(self) -> int:
        """
        Очистить очередь (удалить все задачи)
        
        Returns:
            int: количество удаленных задач
        """
        queue_data = self._read_queue()
        task_count = len(queue_data.get('tasks', []))
        
        queue_data['tasks'] = []
        queue_data['status'] = 'idle'
        queue_data['current_task'] = None
        
        self._write_queue(queue_data)
        
        return task_count
    
    def get_queue_size(self) -> int:
        """Получить количество задач в очереди"""
        queue_data = self._read_queue()
        return len(queue_data.get('tasks', []))
    
    def get_status(self) -> Dict:
        """Получить статус очереди"""
        queue_data = self._read_queue()
        tasks = queue_data.get('tasks', [])
        pending = [t for t in tasks if t.get('status') == 'pending']
        running = [t for t in tasks if t.get('status') == 'running']
        
        return {
            'stand': self.stand_num,
            'status': queue_data.get('status', 'unknown'),
            'total_tasks': len(tasks),
            'pending_count': len(pending),
            'running_count': len(running),
            'tasks': tasks[-10:],  # последние 10 задач для отображения
            'current_task': queue_data.get('current_task')
        }
    
    def remove_task(self, task_id: str) -> bool:
        """
        Удалить конкретную задачу из очереди
        
        Returns:
            bool: True если задача была удалена
        """
        queue_data = self._read_queue()
        original_count = len(queue_data['tasks'])
        
        queue_data['tasks'] = [t for t in queue_data['tasks'] if t['id'] != task_id]
        
        if len(queue_data['tasks']) < original_count:
            self._write_queue(queue_data)
            return True
        
        return False


def get_queue_manager(stand_num: str) -> TaskQueueManager:
    """
    Получить менеджер очереди для стенда (создать если не существует)
    """
    if stand_num not in _queue_managers:
        _queue_managers[stand_num] = TaskQueueManager(stand_num)
    return _queue_managers[stand_num]


def add_to_queue():
    """
    Обработка данных формы и добавление задачи в очередь для стенда
    """
    # Получаем данные из формы
    selected_options = request.form.getlist('options')
    releas = request.form.getlist('releas')
    kernel = request.form.getlist('kernel')
    stand = request.form.get('stand_num')

    # Проверка, что стенд выбран
    if not stand:
        return None, "Стенд не выбран"

    # Обработка тестов
    tests = [option for option in main_options if option in selected_options]
    if tests:
        if isinstance(tests, list):
            tests_list = tests
        else:
            tests_list = [tests]
    else:
        tests_list = []
    
    # Обработка сгруппированных тестов
    found_group = False
    for group in group_tests:
        if str(tests) == '[\'' + str(group) + '\']':
            found_group = True
            tests_list = stands_groups['_'.join(group.replace('_', '').split(' '))]
            break
    
    if not found_group and not tests_list:
        return None, "Тесты не выбраны"
    
    # Обработка релиза
    if releas:
        if isinstance(releas, list):
            release_list = releas
        else:
            release_list = [releas]
    else:
        release_list = []
    
    if not release_list:
        return None, "Релиз не выбран"
    
    # Обработка ядра
    if kernel:
        if isinstance(kernel, list):
            kernel_list = kernel
        else:
            kernel_list = [kernel]
    else:
        kernel_list = []
        
    # Добавляем задачу в очередь стенда
    manager = get_queue_manager(stand)
    task_id = manager.add_task(
        tests=tests_list,
        release=release_list,
        kernel=kernel_list
    )
    
    return (stand, task_id), None



class StandWorker:
    """
    Воркер для выполнения задач стенда
    """
    
    def __init__(self, 
                 stand_num: str):
        self.stand_num = stand_num
        self.thread: Optional[threading.Thread] = None
        self.running = False
        self.current_process: Optional[subprocess.Popen] = None
        self.queue_file = Path(f'./conf/task_queues/queue_stand{stand_num}.json')
    
    def start(self) -> bool:
        """Запустить воркера"""
        if self.running and self.thread and self.thread.is_alive():
            return False
        
        self.running = True
        self.thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.thread.start()
        return True
    
    def stop(self):
        """Остановить воркера"""
        self.running = False
        
        # Останавливаем текущий процесс
        if self.current_process:
            try:
                # Отправляем SIGTERM всей группе процессов
                killpg(getpgid(self.current_process.pid), signal.SIGTERM)
            except:
                pass
            self.current_process = None
        
        # Обновляем статус в файле
        if self.queue_file.exists():
            with open(self.queue_file, 'r') as f:
                queue_data = json.load(f)
            
            queue_data['status'] = 'stopped'
            
            with open(self.queue_file, 'w') as f:
                json.dump(queue_data, f, indent=2)
    
    def is_alive(self) -> bool:
        """Проверить, работает ли воркер"""
        return self.running and self.thread and self.thread.is_alive()
    
    def _worker_loop(self):
        """Основной цикл воркера"""
        while self.running:
            if not self.queue_file.exists():
                time.sleep(2)
                continue
            
            with open(self.queue_file, 'r') as f:
                queue_data = json.load(f)
            
            # Проверяем статус остановки
            if queue_data.get('status') == 'stopped':
                break
            
            # Находим следующую задачу
            tasks = queue_data.get('tasks', [])
            pending = [t for t in tasks if t.get('status') == 'pending']
            
            if not pending:
                # Нет задач — останавливаем воркер
                break
            
            task = pending[0]
            
            # Помечаем как выполняемую
            for t in tasks:
                if t['id'] == task['id']:
                    t['status'] = 'running'
                    break
            
            queue_data['current_task'] = task
            queue_data['status'] = 'running'
            with open(self.queue_file, 'w') as f:
                json.dump(queue_data, f, indent=2)
            
            # Выполняем задачу
            success, message = self._execute_task(task)
            
            # Обновляем после выполнения
            with open(self.queue_file, 'r') as f:
                queue_data = json.load(f)
            
            if success:
                # Удаляем задачу
                queue_data['tasks'] = [t for t in queue_data['tasks'] if t['id'] != task['id']]
            else:
                # Помечаем как failed
                for t in queue_data['tasks']:
                    if t['id'] == task['id']:
                        t['status'] = 'failed'
                        t['error'] = message
                        break
            
            queue_data['current_task'] = None
            
            # Если есть еще задачи — остаемся в статусе running
            pending_remaining = [t for t in queue_data['tasks'] if t.get('status') == 'pending']
            if not pending_remaining:
                queue_data['status'] = 'idle'
            
            with open(self.queue_file, 'w') as f:
                json.dump(queue_data, f, indent=2)
        
        # Завершаем работу
        self.running = False
    
    def _execute_task(self, task: Dict) -> tuple[bool, str]:
        """Выполнить задачу"""
        testenv_status = prepare_testenv_status(method='get')
        
        tests = task['tests']
        release = task['release']
        kernel = task['kernel']
        
        if kernel and kernel != '[]':
            command = f'{VENV_PATH} allta_back.py -rs {release} -st stand{self.stand_num} -ts "{tests}" -kn "{kernel}" -te {testenv_status}'
        else:
            command = f'{VENV_PATH} allta_back.py -rs {release} -st stand{self.stand_num} -ts "{tests}" -te {testenv_status}'
        
        with open(f'front_stand{self.stand_num}.log', 'a') as log:
            log.write(f"\n{'='*60}\n")
            log.write(f"Task: {task['id']}\n")
            log.write(f"Release: {release}\n")
            log.write(f"Tests: {tests}\n")
            log.write(f"Kernel: {kernel}\n")
            log.write(f"Command: {command}\n")
            log.write(f"{'='*60}\n")
            log.flush()
            
            try:
                result = subprocess.run(
                    command,
                    shell=True,
                    text=True,
                    capture_output=True,  # захватываем stdout и stderr
                    preexec_fn=setsid
                )
                
                # Записываем stdout и stderr в лог
                if result.stdout:
                    log.write(f"STDOUT:\n{result.stdout}\n")
                if result.stderr:
                    log.write(f"STDERR:\n{result.stderr}\n")
                log.flush()
                
                if result.returncode == 0:
                    return True, "Выполнено успешно"
                else:
                    error_msg = result.stderr.strip() if result.stderr else f"Завершился с кодом {result.returncode}"
                    return False, error_msg
            except Exception as e:
                log.write(f"Failed: {e}\n")
                log.flush()
                return False, str(e)

_workers: Dict[str, StandWorker] = {}


def get_worker(stand_num: str) -> StandWorker:
    """Получить или создать воркера для стенда"""
    if stand_num not in _workers:
        _workers[stand_num] = StandWorker(stand_num)
    return _workers[stand_num]

def start_worker_for_stand(stand_num: str) -> bool:
    """Запустить воркера для стенда"""
    worker = get_worker(stand_num)
    return worker.start()

def stop_worker_for_stand(stand_num: str):
    """Остановить воркера для стенда"""
    worker = get_worker(stand_num)
    worker.stop()

def is_worker_running(stand_num: str) -> bool:
    """Проверить, работает ли воркер для стенда"""
    worker = get_worker(stand_num)
    return worker.is_alive()

def stop_current_test(stand_num: str):
    """
    Остановить текущий запущенный тест/прогон
    """
    # Останавливаем процесс по PID
    ppid = check_output_command(f"ps -fad -N | grep stand{stand_num} | awk {{'print $2'}}")
    if ppid:
        comm_and_log(f"pkill -TERM -g {ppid}")
    # Освобождаем сервер с помощью индикатора занятости
    busy_status_control(f'stand{stand_num}', 'stop')
 
def stop_queue(stand_num: str):
    """
    Остановить очередь (воркера)
    """
    # Останавливаем воркера
    stop_worker_for_stand(stand_num)
    
    # Обновляем статус в JSON
    manager = get_queue_manager(stand_num)
    queue_data = manager._read_queue()
    queue_data['status'] = 'stopped'
    manager._write_queue(queue_data)
    
    


def info_collector(page, ajax=None):

    tests = []
    logs = {}
    progress_logs = {}
    sett_logs = {}
    status_gif_logs = {}
    status_gif_global_logs = {}
    chmod_author = {}
    col3_body = {}
    gif_mapping = {'Остановлен': red_gif, 
                   'Запущен': green_gif, 
                   'Готово': done_gif,
                   'Занят': red_gif}
    gif_mapping_global = {'Остановлен': red_gif_global, 
                          'Запущен': green_gif_global, 
                          'Готово': done_gif_global,
                          'Занят': busy_gif_global}
    stands_dict = {'main': main_stands,
                   'brest': brest_stands,
                   'mobile': mobile_stands}
    options = {'main': main_options,
               'brest': brest_options,
               'mobile': main_options}
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
            status_gif_global_logs[f'status_gif_global_{stand}'] = gif_mapping_global.get(status, ping_gif)
            status_gif_logs[f'status_gif_{stand}'] = gif_mapping.get(status, ping_gif)
            if status_gif_logs[f'status_gif_{stand}'] == ping_gif:
                status_logs[f'status_{stand}'] = 'Нераспознан'
            if status == 'Остановлен':
                progress_logs[f'progress_{stand}'] = ''
                logs[f'{stand}_log'] = ''

    for stand in stands_dict[page]:
        try:
            with open(f'conf/chmod_author_{stand}.conf', 'r') as r:
                author = r.read()
                chmod_author[f'chmod_author_{stand}'] = author
        except FileNotFoundError:
            chmod_author[f'chmod_author_{stand}'] = ''

    for stand in stands_dict[page]:
        try:
            with open(f'conf/col3_body_{stand}.conf', 'r') as r:
                body = r.read()
                col3_body[f'col3_body_{stand}'] = body
        except FileNotFoundError:
            col3_body[f'col3_body_{stand}'] = ''

    if ajax == True:
        return jsonify({**status_logs,
                        **logs,
                        **status_gif_logs,
                        **status_gif_global_logs,
                        **sett_logs,
                        **progress_logs,
                        **chmod_author,
                        **col3_body})    

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
            
            kernel = request.form.getlist('kernel')
            with open(f'conf/{page}_kernel_args.conf', 'w') as w:
                w.write(str(kernel))
            if not kernel or kernel == '[]':
                kernel = ''

            releas = request.form.getlist('releas')
            with open(f'conf/{page}_releas_args.conf', 'w') as w:
                w.write(str(releas))
            if not releas:
                releas = 'Релиз не выбран'
            
            found_group = []
            for group in group_tests:
                if str(tests) == '[\'' + str(group) + '\']':
                    found_group.append(group)
                    with open(f'conf/{page}_tests_args.conf', 'w') as w:
                        w.write(str(sorted(stands_groups['_'.join(group.replace('_', '').split(' '))])))
            
            if not found_group:
                with open(f'conf/{page}_tests_args.conf', 'w') as w:
                    w.write(str(tests))
        
        return 'index'

    if page == 'brest':
        return render_template(f'{page}.html', 
                                allta_version=allta_version(),
                                options=options[page],
                                test_list=test_list,
                                releas_list=releas_list,
                                kernel_list=kernel_list, 
                                releases=releases(), 
                                kernels=kernels(),
                                brest_url=brest_url,
                                **status_logs,
                                **logs,
                                **status_gif_logs,
                                **status_gif_global_logs,
                                **sett_logs,
                                **progress_logs,
                                **chmod_author,
                                **col3_body)
    else:
        return render_template(f'{page}.html',
                                allta_version=allta_version(), 
                                options=options[page],
                                stands=test_run_stands,
                                kernelslist=kernels(),
                                rc=rc_list(), 
                                releaseslist=releases_list(),
                                test_list=test_list,
                                releas_list=releas_list,
                                kernel_list=kernel_list, 
                                releases=releases(), 
                                kernels=kernels(),
                                **status_logs,
                                **logs,
                                **status_gif_logs,
                                **status_gif_global_logs,
                                **sett_logs,
                                **progress_logs,
                                **chmod_author,
                                **col3_body,                                                        
                                main_url=main_url,
                                mobile_url=mobile_url,
                                brest_url=brest_url,
                                stp_versions=stp_version(),
                                repo_path=releases_dict().keys(),
                                stand1_snap=cz_comm()['stand1'].keys(),
                                stand2_snap=cz_comm()['stand2'].keys(),
                                stand3_snap=cz_comm()['stand3'].keys(),
                                stand4_snap=cz_comm()['stand4'].keys(),
                                stand5_snap=cz_comm()['stand5'].keys(),
                                stand6_snap=cz_comm()['stand6'].keys(),
                                stand7_snap=cz_comm()['stand7'].keys(),
                                stand8_snap=cz_comm()['stand8'].keys(),
                                stand9_snap=cz_comm()['stand9'].keys(),
                                stand10_snap=cz_comm()['stand10'].keys(),
                                stand11_snap=cz_comm()['stand11'].keys(),
                                stand12_snap=cz_comm()['stand12'].keys(),
                                stand13_snap=cz_comm()['stand13'].keys(),
                                **server_diskspace_used())



def busy_status_control(stand, name, version=None):
    if name == 'stop':
        with open(f'conf/work_status_{stand}.conf', 'w') as w:
            w.write('Остановлен')
        with open(f'conf/chmod_author_{stand}.conf', 'w') as w:
            w.write('')
        with open(f'conf/col3_body_{stand}.conf', 'w') as w:
            w.write('')
    elif name == 'dtimonin':
        with open(f'conf/work_status_{stand}.conf', 'w') as w:
            w.write('Занят')
        with open(f'conf/chmod_author_{stand}.conf', 'w') as w:
            w.write('Дмитрий Тимонин')
    elif name == 'ivelikanov':
        with open(f'conf/work_status_{stand}.conf', 'w') as w:
            w.write('Занят')
        with open(f'conf/chmod_author_{stand}.conf', 'w') as w:
            w.write('Иван Великанов')
    elif name == 'mfilippenko':
        with open(f'conf/work_status_{stand}.conf', 'w') as w:
            w.write('Занят')
        with open(f'conf/chmod_author_{stand}.conf', 'w') as w:
            w.write('Максим Филиппенко')
    elif name == 'amedvedev':
        with open(f'conf/work_status_{stand}.conf', 'w') as w:
            w.write('Занят')
        with open(f'conf/chmod_author_{stand}.conf', 'w') as w:
            w.write('Александр Медведев')
    elif name == 'ACS':
        with open(f'conf/work_status_{stand}.conf', 'w') as w:
            w.write('Занят')
        with open(f'conf/chmod_author_{stand}.conf', 'w') as w:
            w.write('ACS')
        with open(f'conf/col3_body_{stand}.conf', 'w') as w:
            w.write(f'Create clonezilla snapshot {version}')
    elif name == 'TestRunner':
        with open(f'conf/work_status_{stand}.conf', 'w') as w:
            w.write('Запущен')
        with open(f'conf/chmod_author_{stand}.conf', 'w') as w:
            w.write('TestRunner')
        with open(f'conf/work_status_stand1.conf', 'w') as w:
            w.write('Запущен')
        with open(f'conf/chmod_author_stand1.conf', 'w') as w:
            w.write('TestRunner')
    elif name == 'testrun done':
        with open(f'conf/col3_body_{stand}.conf', 'w') as w:
            w.write('Прогон завершен')
    elif name == 'testrun fail':
        with open(f'conf/col3_body_{stand}.conf', 'w') as w:
            w.write('Прогон завершен исключением')
    elif name == 'vmshub':
        with open(f'conf/work_status_{stand}.conf', 'w') as w:
            w.write('Занят')
        with open(f'conf/chmod_author_{stand}.conf', 'w') as w:
            w.write('VMs-Hub')
    elif name == 'VM-Manager':
        with open(f'conf/work_status_{stand}.conf', 'w') as w:
            w.write('Занят')
        with open(f'conf/chmod_author_{stand}.conf', 'w') as w:
            w.write('VM-Manager')
        with open(f'conf/col3_body_{stand}.conf', 'w') as w:
            w.write(f'VM-Manager tasks')



def run_command_on_stand(num, http=True):
    process_manager = globals()[f'process_manager{num}']
    process_list = globals()[f'process_list{num}']
    if http:
        command = request.form.get(f'command{num}')
    else: command = 'start'
    kernel = None
    if num == '3' or num == '4' or num == '10' or num =='11' or num == '12' or num == '13':
        prefix = 'main'
    #elif num == '10' or num =='11' or num == '12':
        #prefix = 'brest'

    if command == 'start':
        testenv_status = prepare_testenv_status(method='get')
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

        command_to_run = f'{VENV_PATH} allta_back.py -rs {releas} -st stand{num} -ts "{tests}" -te {testenv_status}'
        command_to_run_kernel = f'{VENV_PATH} allta_back.py -rs {releas} -st stand{num} -ts "{tests}" -kn "{kernel}" -te {testenv_status}'

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
        busy_status_control(f'stand{num}', 'stop')

    elif command == 'stop':
        ppid = check_output_command(f"ps -fad -N | grep stand{num} | awk {{'print $2'}}")
        comm_and_log(f"pkill -TERM -g {ppid}")

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
        busy_status_control(f'stand{num}', 'stop')

    if http:
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
                if kernel_list == 'None' or kernel_list == '[]':
                    kernel_list = 'Ядро не выбрано'
    except FileNotFoundError:
        kernel_list = 'Ядро не выбрано'
    
    return test_list, releas_list, kernel_list



def ssh_command(command, stand_ip):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(stand_ip, port=port, username=user, password=__srv_pass)
    stdin, stdout, stderr = client.exec_command(command)
    response = stdout.read().decode().strip()
    client.close()
    return response


#TODO заменено на get_server_load
# def output_remote_load(stand):
#     sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
#     sock.settimeout(2.5)
#     conn = None

#     command = """
#             top -bn1 | grep '%Cpu' | tail -1 | awk '{gsub(",",".",$8); 
#             printf "%s::%s::%s::", 100-$8 "%", $2 "%", $4 "%"}'; 
#             free -m | awk 'NR==2{printf "%sM\\n", $2-$7}'
#             """

#     try:
#         result = sock.connect_ex((stands_ip[stand], 22))
        
#         if result != 0:
#             output_cpu = '-'
#             output_ram = '-'
#             output_cpu_user = '-'
#             output_cpu_system = '-'
#         else:
#             try:
#                 cpu_ram_output = ssh_command(command, stand_ip=stands_ip[stand])
#                 cpu_ram_output = cpu_ram_output.split('::')
#                 output_cpu = cpu_ram_output[0]
#                 output_cpu_user = cpu_ram_output[1].replace(',','.')
#                 output_cpu_system = cpu_ram_output[2].replace(',','.')
#                 output_ram = cpu_ram_output[3].strip()
#             except paramiko.AuthenticationException:
#                 output_cpu = 'Auth Error'
#                 output_ram = 'Auth Error'
#                 output_cpu_user = 'Auth Error'
#                 output_cpu_system = 'Auth Error'
#             except (ssh_exception.NoValidConnectionsError, ssh_exception.SSHException, EOFError):
#                 output_cpu = 'Connect Error'
#                 output_ram = 'Connect Error'
#                 output_cpu_user = 'Connect Error'
#                 output_cpu_system = 'Connect Error'
#             except socket.timeout as st:
#                 print(f'{type(st).__name__}\nНедоступен {stands_ip[stand]}, перезагружается или выключен.\n')

#         conn = psycopg2.connect(
#                                 host=psyc['host'],
#                                 database=psyc['database'],
#                                 user=psyc['user'],
#                                 password=psyc['password']
#                                 )

#         cursor = conn.cursor()

#         id = 1 #row number
#         update_query = f"UPDATE main_table SET {stand}_cpu = %s, {stand}_cpu_user = %s, {stand}_cpu_system = %s, {stand}_ram = %s WHERE id = %s"
#         data = (output_cpu, output_cpu_user, output_cpu_system, output_ram, id)
#         cursor.execute(update_query, data)

#         conn.commit()
#         cursor.close()
#         conn.close()
#     except socket.timeout:
#         pass
#     except IndexError as e:
#         print(f'IndexError: {type(e).__name__}, Message: {str(e)}')
#     except Exception as all_e:
#         print(f'Error: {type(all_e).__name__}, Message: {str(all_e)}')
#     finally:
#         if conn:
#             conn.close()


# def remote_storage_load(stand):
#     sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
#     sock.settimeout(3.7)
#     conn = None

#     """
#     Add temp block
#     """
#     if stand == 'stand1' or stand == 'stand2':
#         temp_cpu_comm = 'cat /sys/class/thermal/thermal_zone1/temp'
#     elif stand == 'stand3':
#         temp_cpu_comm = 'cat /sys/class/thermal/thermal_zone0/temp; cat /sys/class/thermal/thermal_zone1/temp'
#     elif stand == 'stand4' or stand == 'stand5':
#         temp_cpu_comm = 'cat /sys/class/thermal/thermal_zone0/temp; cat /sys/class/thermal/thermal_zone1/temp'

#     try:
#         result = sock.connect_ex((stands_ip[stand], 22))
        
#         if result != 0:
#             output_nvme = '-'
#             output_sda = '-'
#             temp_cpu = '-'
#         else:
#             try:
#                 output_nvme  = ssh_command("""iostat -dx 1 2 | awk '/nvme0n1|nvme0c0n1/ {gsub(",", ".", $NF); \
#                                               printf "%.1f%%\\n", $NF}' | tail -n 1""", 
#                                         stand_ip=stands_ip[stand])
#                 block_device_name = ssh_command("lsblk | awk 'NR==2' | awk '{print $1;}'",
#                                                 stand_ip=stands_ip[stand])
#                 output_sda  = ssh_command("""iostat -dx 1 2 | awk '/""" + str(block_device_name) + """/ {gsub(",", ".", $NF); \
#                                              printf "%.1f%%\\n", $NF}' | tail -n 1""", 
#                                         stand_ip=stands_ip[stand])
#                 temp_cpu = ssh_command(temp_cpu_comm, stand_ip=stands_ip[stand])
#             except paramiko.AuthenticationException:
#                 output_nvme = 'Auth Error'
#                 output_sda = 'Auth Error'
#                 temp_cpu = 'Auth Error'
#             except (ssh_exception.NoValidConnectionsError, ssh_exception.SSHException):
#                 output_nvme = 'Connect Error'
#                 output_sda = 'Connect Error'
#                 temp_cpu = 'Connect Error'
#             except socket.timeout as st:
#                 print(f'{type(st).__name__}\nНедоступен {stands_ip[stand]}, перезагружается или выключен.\n')

#         conn = psycopg2.connect(
#                                 host=psyc['host'],
#                                 database=psyc['database'],
#                                 user=psyc['user'],
#                                 password=psyc['password']
#                                 )

#         cursor = conn.cursor()

#         id = 1 #row number
#         update_query = f"UPDATE main_table SET {stand}_nvme = %s, {stand}_sda = %s, {stand}_temp_cpu = %s WHERE id = %s"
#         if temp_cpu == '-' or temp_cpu == 'Auth Error' or temp_cpu == 'Connect Error':
#             data = (output_nvme, output_sda, temp_cpu, id)
#         else:
#             if stand == 'stand1' or stand == 'stand2':
#                 data = (output_nvme, output_sda, f'{int(float(temp_cpu) / 1000)}°', id)
#             elif stand == 'stand3' or stand == 'stand4' or stand == 'stand5':
#                 temp_cpu = temp_cpu.split('\n')
#                 data = (output_nvme, output_sda, f'{int(float(temp_cpu[0]) / 1000)}° | {int(float(temp_cpu[1]) / 1000)}°', id)
#         cursor.execute(update_query, data)

#         conn.commit()
#         cursor.close()
#         conn.close()
#     except socket.timeout:
#         pass
#     except Exception as all_e:
#         print(f'Error: {type(all_e).__name__}, Message: {str(all_e)}')
#     finally:
#         if conn:
#             conn.close()


def get_server_load(stand):
    """
    Обращается к серверу prometheus для забора интересующих метрик, 
    записывает собранные данные в БД.
    """
    prometheus_url = 'http://10.177.103.10:9090/api/v1/query'
    server_ip = f'{stands_ip[stand]}:9100'
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1.5)
    conn = None

    try:
        result = sock.connect_ex((stands_ip[stand], 22))
        
        if result != 0:
            output_cpu = '-'
            output_ram = '-'
            output_cpu_user = '-'
            output_cpu_system = '-'
            output_nvme = '-'
            output_sda = '-'
            temp_cpu = '-'
        else:
            try:
                def get_prometheus_data(query):
                    response = requests.get(prometheus_url, params={'query': query})
                    response.raise_for_status()
                    
                    result = response.json()
                    data_by_instance = {}
                    #logging.debug(result)
                    
                    for metric_data in result['data']['result']:
                        value = float(metric_data['value'][1])
                        instance = metric_data['metric'].get('instance', 'unknown')
                        data_by_instance[instance] = value
                    
                    return data_by_instance
                
                if stands_ip[stand] == '10.177.103.204' or stands_ip[stand] == '10.177.103.205':
                    device_name = 'nvme0c0n1'
                else: device_name = 'nvme0n1'

                # Запросы метрик
                cpu_user = get_prometheus_data(f'rate(node_cpu_seconds_total{{mode="user", instance="{server_ip}"}}[5s])')
                cpu_system = get_prometheus_data(f'rate(node_cpu_seconds_total{{mode="system", instance="{server_ip}"}}[5s])')
                mem_total = get_prometheus_data(f'node_memory_MemTotal_bytes{{instance="{server_ip}"}}')
                mem_available = get_prometheus_data(f'node_memory_MemAvailable_bytes{{instance="{server_ip}"}}')
                cpu_temp1 = get_prometheus_data(f'node_hwmon_temp_celsius{{instance="{server_ip}", sensor="temp1"}}')
                cpu_temp2 = get_prometheus_data(f'node_hwmon_temp_celsius{{instance="{server_ip}", sensor="temp2"}}')
                nvme_usage = get_prometheus_data(f'rate(node_disk_io_time_seconds_total{{device="{device_name}", instance="{server_ip}"}}[5s])')
                sda_usage = get_prometheus_data(f'rate(node_disk_io_time_seconds_total{{device="sda", instance="{server_ip}"}}[5s])')

                cpu_total_usage = abs(round((1 - (cpu_user.get(server_ip, 0.0) + cpu_system.get(server_ip, 0.0))) * 100 -100, 1))
                mem_usage_mb = round((mem_total.get(server_ip, 0.0) - mem_available.get(server_ip, 0.0)) / 1024 / 1024)
                cpu_temp_str = f'{cpu_temp1.get(server_ip, 0.0):.1f}°C / {cpu_temp2.get(server_ip, 0.0):.1f}°C'
                nvme_usage_str = f'{nvme_usage.get(server_ip, 0.0) * 100:.1f}%'
                sda_usage_str = f'{sda_usage.get(server_ip, 0.0) * 100:.1f}%'

                logging.debug(f'Instance: {server_ip}')
                logging.debug(f'CPU Total Usage: {cpu_total_usage}%')
                logging.debug(f'CPU User Mode: {cpu_user.get(server_ip, 0.0) * 100:.1f}%')
                logging.debug(f'CPU System Mode: {cpu_system.get(server_ip, 0.0) * 100:.1f}%')
                logging.debug(f'Memory Usage: {mem_usage_mb}M')
                logging.debug(f'CPU Temperature: {cpu_temp_str}')
                logging.debug(f'NVMe Usage: {nvme_usage_str}')
                logging.debug(f'SDA Usage: {sda_usage_str}')
                
                output_cpu = f'{cpu_total_usage}%'
                output_cpu_user = f'{cpu_user.get(server_ip, 0.0) * 100:.1f}%'
                output_cpu_system = f'{cpu_system.get(server_ip, 0.0) * 100:.1f}%'
                output_ram = f'{mem_usage_mb}M'
                temp_cpu = f'{cpu_temp_str}'
                output_nvme = f'{nvme_usage_str}'
                output_sda = f'{sda_usage_str}'
            except socket.timeout as st:
                logging.error(f'{type(st).__name__}\nНедоступен {stands_ip[stand]}, перезагружается или выключен.\n')
            except Exception as e:
                logging.error(f'IndexError: {type(e).__name__}, Message: {str(e)}')
                output_cpu = f'{type(e).__name__}'
                output_ram = f'{type(e).__name__}'
                output_cpu_user = f'{type(e).__name__}'
                output_cpu_system = f'{type(e).__name__}'
                output_nvme = f'{type(e).__name__}'
                output_sda = f'{type(e).__name__}'
                temp_cpu = f'{type(e).__name__}'

        conn = psycopg2.connect(
                                host=psyc['host'],
                                database=psyc['database'],
                                user=psyc['user'],
                                password=psyc['password']
                                )

        cursor = conn.cursor()

        id = 1 #row number
        update_query = f"UPDATE main_table SET {stand}_cpu = %s, {stand}_cpu_user = %s, {stand}_cpu_system = %s, {stand}_ram = %s, {stand}_nvme = %s, {stand}_sda = %s, {stand}_temp_cpu = %s WHERE id = %s"
        data = (output_cpu, output_cpu_user, output_cpu_system, output_ram, output_nvme, output_sda, temp_cpu, id)
        cursor.execute(update_query, data)

        conn.commit()
        cursor.close()
        conn.close()
    except socket.timeout:
        pass
    except IndexError as e:
        logging.error(f'IndexError: {type(e).__name__}, Message: {str(e)}')
    except Exception as all_e:
        logging.error(f'Error: {type(all_e).__name__}, Message: {str(all_e)}')
    finally:
        if conn:
            conn.close()



def remote_sysstat_available(stand):
    ssh_command("""dpkg -s sysstat &> /dev/null || sudo apt-get install sysstat -y""", 
                stand_ip=stands_ip[stand])
   

#def background_stat_storage_main():
#    stands = main_stands
#
#    while True:
#        [get_server_load(str(stand)) for stand in stands]
#        sleep(4)


def background_task_main():
    stands = main_stands

    while True:
        [get_server_load(str(stand)) for stand in stands]
        sleep(4)


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

#def background_task_brest():
#    stands = brest_stands
#
#    while True:
#        [output_remote_load(str(stand)) for stand in stands]
#        sleep(3)


def update_changelog_block(rc):
    request = f'http://10.177.103.10:8989/get_components_for_testrun_by_changelog?astra_linux_build_version={rc}&first_level_dependencies=true&return_dct_component_with_packages=false'
    response = requests.get(request).json()
    print(response)
    print(response['result'])
    status = response['status']
    result = response['result']
    return jsonify(status=status,
                   result=result)


def update_settings_block():

    test_list, releas_list, kernel_list = create_args('main')
    return jsonify(test_list=test_list, 
                   releas_list=releas_list, 
                   kernel_list=kernel_list)


def get_kernels_from_rc(version_rc: str, get_list=False):
    pathlib = str(getcwd() + '/libs/datlib.so')
    clib = ctypes.CDLL(pathlib)

    def get_file(path, name):
        c_path = ctypes.c_char_p(bytes(path, encoding='utf8'))
        c_name = ctypes.c_char_p(bytes(name, encoding='utf8'))
        clib.download_file(c_path, c_name)

    pkg_path = repo_path()[f'pkg_path_{version_rc}']
    vers_path = repo_path()[f'vers_path_{version_rc}']

    get_kernels = ZefirTestRun()
    get_file(str(pkg_path), 'available_packages')
    get_file(str(vers_path), 'available_version')
    version, kernels = get_kernels.get_kernels_from_file()

    if get_list:
        return kernels
    else:
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
    url = 'https://git.astralinux.ru/projects/QA/repos/astra-qa-stand/raw/astra-config.json?at=refs%2Fheads%2Fmaster'
    headers = {
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:102.0) Gecko/20100101 Firefox/102.0',
            'authority': JIRA_URL,
            'Authorization': __basic,
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'ru-RU,ru;q=0.8,en-US;q=0.5,en;q=0.3',
            'Accept-Encoding': 'gzip, deflate, br',
            'Referer': f'https://{JIRA_URL}/secure/Tests.jspa',
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

    with open(f'{path}/astra-config.json', 'wb') as f:
        f.write(response.content)

    

class ReleaseToRepo:
    def __init__(self,
                 current_directory=None):
        self.load_filename = 'releases-index.json'
        self.gen_filename = 'releases.json'
        self.cur_directory = current_directory


    def get_releases_index(self):
        url = 'https://releases.devos.astralinux.ru/index.json'
        response = requests.get(url)
        assert response.status_code == 200, f'Request {self.load_filename} failed with status {response.status_code}'

        with open(f'{self.cur_directory}/{self.load_filename}', 'wb') as f:
            f.write(response.content)

     
    def generate_releases_file(self):
        prefix = 'deb https://releases.devos.astralinux.ru/'
        sufix = '_x86-64 main contrib non-free'

        with open(f'{self.cur_directory}/{self.load_filename}', 'r') as r:
            releases = json.load(r)

        def __path_seporator(build_version: str):
            release = '.'.join(build_version.split('.')[:2])
            update = '.'.join(build_version.split('.')[:3])

            return [f"{prefix}{releases['releases'][release][update][build_version]['files'][i]['mount_point']} {release}{sufix}"
                    for i in range(len(releases['releases'][release][update][build_version]['files']))]

        def __repo_filter(dates, key: str):
            no_base_repo = ['1.7.4', '1.7.3.UU.2', '1.7.3.UU.1', '1.7.3', '1.7.2.UU.1', '1.7.2', '1.7.1', '1.7.0']

            if key.startswith('1.7') and key not in no_base_repo:
                return [v for v in dates[key] if 'base-repository' in v]
            elif key.startswith('1.7') and key in no_base_repo:
                return [v for v in dates[key] if not 'installation' in v and not 'update-repository' in v]
            elif key.startswith('1.8'):
                return [v for v in dates[key] if not 'installation-di' in v]
            else: return [v for v in dates[key]]

        seporated_dates = {
            key: __path_seporator(version) for key, version in releases_dict().items()
        }

        filtered_dates = {
            key: __repo_filter(seporated_dates, key) for key, version in seporated_dates.items()
        }

        sorted_dict = {k: v for k, v in sorted(filtered_dates.items())}

        with open(f'{self.cur_directory}/{self.gen_filename}', 'w') as w:
            json.dump(sorted_dict, w, indent=4)
    

def create_vm_snapshot(stand, snapshot_name):
    vms = test_station_vms
    cmd = f'sudo vboxmanage snapshot {vms[stand]} take {snapshot_name}'
    
    if stand in vms.keys():
        ssh_command(command=cmd, stand_ip=stands_ip['stand5'])


def backup_snapshot(stand, snapshot):
    command = f'{cz_comm()[stand][snapshot]}'
    subprocess.run(command, shell=True)


def backup_vm_snapshot(stand, snapshot):
    vms = test_station_vms
    commands = [f'sudo virsh --connect qemu:///system destroy {vms[stand]} выключить',
                f'sudo virsh snapshot-revert --domain {vms[stand]} --snapshotname {cz_comm()[stand][snapshot]}',
                f'sudo virsh --connect qemu:///system start {vms[stand]}']

    [ssh_command(command=cmd, stand_ip=stands_ip['stand5']) for cmd in commands]


def power_on_stand(stand):
    vms = test_station_vms
    cmd = f'sudo virsh --connect qemu:///system start {vms[stand]}'

    if stand in vms.keys():
        ssh_command(command=cmd, stand_ip=stands_ip['stand5'])


class BootOrder:
    def __init__(self,
                 stand=None,
                 boottype='PXE'):
        
        self.stand = stand
        self.boot_type = boottype
        self.show_config = 'show /system1/bootconfig1/oemhp_uefibootsource'
        self.set_new_config = 'set /system1/bootconfig1/oemhp_uefibootsource{} bootorder=1'
        self.old_mode_key = '-oKexAlgorithms=+diffie-hellman-group1-sha1'
        self.no_fprint = '-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null'
        self.reset_machine = 'reset /system1'
        self.slot_count = 5
        self.ilo = ilo
        self.login = self.ilo[self.stand]['username']
        self.password = self.ilo[self.stand]['password']
        self.address = self.ilo[self.stand]['ip']
        self.ssh_command = f"sshpass -p '{self.password}' ssh {self.no_fprint} {self.old_mode_key} -l {self.login} {self.address}"
        self.client = redfish.redfish_client(base_url=self.address, username=self.login, password=self.password)

    def cmd(self, cmd):
        output = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE).stdout.decode("utf-8")
        return output
    
    def set_boot_order(self):
        if self.stand == 'stand3' or self.stand == 'stand4' or self.stand == 'stand10' \
            or self.stand == 'stand11' or self.stand == 'stand12' or self.stand == 'stand13':
            self.__set_boot_order_ilo()
        elif self.stand == 'stand5':
            self.__set_boot_order_idrac()

    def __set_boot_order_ilo(self):        
        try:
            for i in range(0, self.slot_count + 1, 1):
                answer = self.cmd(f'{self.ssh_command} {self.show_config}{i}')
                if self.boot_type in answer and i == 1:
                    logging.debug(f'\033[93m{self.boot_type} загрузка уже в приоритете, настройка не требуется\033[0m\n')
                    break
                elif self.boot_type in answer and i != 1:
                    logging.debug(f'\033[93m{answer}\033[0m')
                    result = self.cmd(f'{self.ssh_command} {self.set_new_config}'.format(i))
                    if 'Bootorder being set' in result:
                        logging.debug(f'\033[92mПриоритет загрузки успешно изменен на {self.boot_type}\033[0m\n')
                    break
        except Exception as e:
            logging.error(f'Type:{type(e).__name__}, \nMessage:{str(e)}')

    def __set_boot_order_idrac(self):
        self.client.login(auth="session")

        try:
            response = self.client.get('/redfish/v1/Systems/System.Embedded.1')
            system_info = response.dict
            logging.debug("System Information: ", system_info)

            response = self.client.get('/redfish/v1/Systems/System.Embedded.1/BootSources')
            boot_sources = response.dict
            logging.debug("Boot Sources: ", boot_sources)

            body = {
                "Boot": {
                    "BootSourceOverrideTarget": "Pxe",
                    "BootSourceOverrideEnabled": "Once"
                }
            }
            response = self.client.patch('/redfish/v1/Systems/System.Embedded.1', body=body)
            if response.status == 200:
                logging.debug(f'\033[92mПриоритет загрузки успешно изменен на {self.boot_type}\033[0m\n')
        except Exception as e:
            logging.error(f'Type:{type(e).__name__}, \nMessage:{str(e)}')
        finally:
            self.client.logout()

    def __reboot_idrac(self):
        self.__set_boot_order_idrac()
        self.client.login(auth="session")

        try:
            body = {
                "ResetType": "ForceRestart"
            }
            response = self.client.post('/redfish/v1/Systems/System.Embedded.1/Actions/ComputerSystem.Reset', body=body)
            if response.status in [200, 204]:
                logging.debug('execute IPMI iDRAC hard reboot successfully')
            else:
                logging.error(f'execute IPMI iDRAC hard reboot failed, status: {response.status}')
        except Exception as e:
            logging.error(f'Type:{type(e).__name__}, \nMessage:{str(e)}')
        finally:
            self.client.logout()

    def reset_by_timer(self, func):
        timer = 7200
        interval = 60
        for _ in range(timer // interval):
            sleep(interval)
            if not func.is_alive():
                return 0
        logging.debug(f'Время ожидания {timer} сек. Истекло, будет выполнена перезагрузка')
        if self.stand == 'stand3' or self.stand == 'stand4' or self.stand == 'stand10' \
            or self.stand == 'stand11' or self.stand == 'stand12' or self.stand == 'stand13':
            logging.debug(self.cmd(f'{self.ssh_command} {self.reset_machine}'))
        elif self.stand == 'stand5':
            self.__reboot_idrac()

    def reset(self):
        if self.stand == 'stand3' or self.stand == 'stand4' or self.stand == 'stand10' \
            or self.stand == 'stand11' or self.stand == 'stand12' or self.stand == 'stand13':
            logging.debug('execute IPMI hard reboot')
            logging.debug(self.cmd(f'{self.ssh_command} {self.reset_machine}'))
        elif self.stand == 'stand5':
            self.__reboot_idrac()



class TestTimeWatchdog:
    """
    Класс позволяет вести динамический подсчет времени, 
    затраченного на прогон с одним ядром.

    :param str upd_version: upd version (1.7 or 1.8 etc)
    :param str stand: номер стенда
    """
    def __init__(self,
                 upd_version,
                 stand):

        self.upd_version = upd_version
        self.stand = stand
        self.times_path = 'test_times.json'


    def transfer_test_time(self, test_name: str, time: str):
        """
        :param test_name: наименование теста в прогоне
        :param time: время, затраченное на выполнения теста
        """
        with open(self.times_path, 'r') as r:
            data = json.loads(r.read())
        
        if self.upd_version in data and self.stand in data[self.upd_version]: 
            data[self.upd_version][self.stand][test_name] = time

        with open(self.times_path, 'w') as w:
            json.dump(data, w, indent=4)


    def _counting_total_time(self):
        """
        Подсчет общего времени, затраченного на прогон с одним ядром
        """
        with open(self.times_path, 'r') as r:
            data = json.loads(r.read())

        def parse_time(time_str):
            if 'days' in time_str:
                days, time_str = time_str.split(' days, ')
                days = int(days)
            elif 'day' in time_str:
                days, time_str = time_str.split(' day, ')
                days = int(days)
            else:
                days = 0

            t = datetime.strptime(time_str.strip(), "%H:%M:%S")
            return timedelta(days=days, hours=t.hour, minutes=t.minute, seconds=t.second)

        def sum_times(times):
            total = timedelta()
            for time_str in times:
                total += parse_time(time_str)
            return total

        def format_time(total):
            days = total.days
            hours, remainder = divmod(total.seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            if days > 1:
                return f"{days} days {hours}:{minutes:02}:{seconds:02}"
            elif days > 0:
                return f"{days} day {hours}:{minutes:02}:{seconds:02}"
            else:
                return f"{hours}:{minutes:02}:{seconds:02}"

        for version, stands in data.items():
            for stand, tests in stands.items():
                times = [time for test, time in tests.items() if test != "Total time"]
                total_time = sum_times(times)
                data[version][stand]["Total time"] = format_time(total_time)

        rows = {}
        for version, stands in data.items():
            for stand, tests in stands.items():
                for test, time in tests.items():
                    if test not in rows:
                        rows[test] = {}
                    rows[test][(version, stand)] = time

        return rows


    def create_html(self):
        """
        Создает html на основе полученных данных
        """

        current_day = datetime.now().date()
        html_head = f"""
        <br />
        <br />
        <h1>Время выполнения прогона для одного ядра. Актуально на {current_day}</h1>
        """

        df = pd.DataFrame(self._counting_total_time()).transpose()
        df = df.reindex(columns=sorted(df.columns, key=lambda x: (x[0], x[1])))

        total_time_row = df.loc['Total time']
        df = df.drop('Total time')
        df = pd.concat([df, total_time_row.to_frame().T])
        df.fillna('', inplace=True)

        html_result = df.to_html()
        html_page = '\n'.join([html_head + html_result])

        print(html_page)
        with open('templates/times.html', 'w') as w:
            w.write(html_page)



def prepare_testenv_status(method: str, switch='off') -> str:
    if method == 'put':
        with open('conf/prepare_testenv_status.conf', 'w') as w:
            status = switch
            w.write(status)
        return status
    elif method == 'get':
        with open('conf/prepare_testenv_status.conf', 'r') as r:
            status = r.read().strip()
        return status
    


def services_health_status():
    get_dict = request.args.get('get_dict', False)
    status_dict_raw = {
        i: check_output_command(f"sudo systemctl status {i} | grep Active: | awk '{{print$1, $2, $3}}'") for i in allta_services_list
    }
    status_dict = {
        key: ' '.join(re.sub(r'\\W+', ' ', value.strip()).replace('\n', ' ').split(' ')[:3]) for key, value in status_dict_raw.items()
    }

    if get_dict:
        return jsonify(status_dict)

    if all('active (running)' in status or 'active (exited)' in status for status in status_dict.values()):
        print('All services are running or exited successfully')
        return jsonify({'services_health_status': 'ok'})
    else:
        print('Some service(s) are not running or exited successfully')
        return jsonify({'services_health_status': 'fail'})
    


def response_used_astra_services():
    dns = None
    try:
        available_dns = {
            ip: subprocess.run(f'ping -c 1 {ip}', shell=True).returncode for ip in ASTRA_DNS
        }
        if 0 in available_dns.values():
            print('dns pass')
            dns = 200

        jira = requests.get(f'https://{JIRA_URL}').status_code
        life = requests.get(f'https://{CONFLUENCE_URL}').status_code
        git = requests.get(f'https://{GIT_URL}').status_code
        releases = requests.get(f'https://{RELEASES_URL}').status_code
        return jira, life, git, releases, dns
    except Exception as e:
        jira, life, git, releases, dns = str(type(e).__name__), str(e)
        return jira, life, git, releases, dns
    


def available_astra_services_checker():
    wait_time = 120 #Минут ожидания
    requests_frequency = 180 #Периодичность обращений в секундах 
    status = 0
    except_counter = 0
    while status == 0:
        jira, life, git, releases_dev, dns = response_used_astra_services()
        try:
            if jira == 200 and life == 200 and git == 200 and releases_dev == 200 and dns == 200:
                status += 1
                logging.info('\nAll astra services are available\n\n')
                return True
            else:
                except_counter += 1
                sleep(requests_frequency)
                if except_counter == wait_time * 60 / requests_frequency:
                    logging.error('\n\nAvailable astra services timeout')
                    logging.error(f'Except count = {except_counter}, aborted')
                    logging.error(f'Services status:\nJira - {jira}\nLife - {life}\nGit - {git}\n\
                                  Releases - {releases_dev}\nDNS - {dns}\n\n')
                    status += 1
                    return False
        except Exception as e:
            except_counter += 1
            sleep(requests_frequency)
            if except_counter == wait_time * 60 / requests_frequency:
                logging.error('\n\nException\nAvailable astra services timeout')
                logging.error(f'Except count = {except_counter}, aborted')
                logging.error(f'Services status:\nJira - {jira}\nLife - {life}\nGit - {git}\n\
                                  Releases - {releases_dev}\nDNS - {dns}\n\n')
                status += 1
                return False



def astra_services_health_status():
    get_dict = request.args.get('get_dict', False)
    services = ['Jira', 'Life', 'Git', 'Releases', 'DNS']
    statuses = response_used_astra_services()
    status_dict = {
        service: 'ok' if status == 200 else 'Fail' for service, status in zip(services, statuses)
    }
    
    if get_dict:
        return jsonify(status_dict)
    
    if all(x == 200 for x in response_used_astra_services()):
        print('All astra services are available')
        return jsonify({'astra_services_health_status': 'ok'})
    else:
        print('Some service(s) are not available')
        return jsonify({'astra_services_health_status': 'fail'})



class TestrunManager:
    """
    Класс позволяет добавлять новые версии релиз кандидатов, создавать "снимки"
    серверов, создавать тестовые прогоны, запускать прогоны.
    Создан путем переноса основного функционала из ТГ-бота. 
    """

    def __init__(self,
                 rc=None,
                 build_version=None,
                 stand=None):

        self.rc = rc
        self.build_version = build_version
        self.stand = stand
        self.__conf_token = tokens['conf_token']
        self.__username = tokens['username']
        self.__basic = tokens['jira_token']
        self.__password = tokens['pass']


    def run_tests(self, version, stand):
        tests_dir = 'conf/main_tests_args.conf'
        releases_dir = 'conf/main_releas_args.conf'

        group = f'{stand}_group'
        tests = stands_groups[group]

        with open(releases_dir, 'w') as w:
            w.write(str([version]))
        with open(tests_dir, 'w') as w:
            w.write(str(tests))

        run_command_on_stand(list(stand)[-1], http=False)
        sleep(20)


    def update_changelog(self, value):
        path = './ChangeLog'
        with open(path, 'r') as r:
            version = r.readline()
            text = r.read()
        upp_version = int(version.split(' ')[2].split('.')[-1]) + 1
        pre_version = '.'.join(version.split(' ')[2].split('.')[:-1])
        new_version = f"{' '.join(version.split(' ')[:-1])} {pre_version}.{upp_version}"

        print(version)
        print(new_version)
        print('.'.join(version.split(' ')[2].split('.')[:-1]))

        commit = f'{new_version}\n* Add {value}\n\n\n\n\n'

        with open(path, 'w') as w:
            w.write(f'{commit}\n{version}{text}')


    def create_test_run(self, version: str, final=None):
        check_len_version = version.split('.')
        if len(check_len_version) == 4 and check_len_version[3] != 'UU':
            release = '.'.join(check_len_version[:3]) 
        elif len(check_len_version) == 6 and check_len_version[3] == 'UU':
            release = '.'.join(check_len_version[:5]) 
        
        stands = test_run_stands
        kernels = get_kernels_from_rc(version, get_list=True)

        test_run = ZefirTestRun(use_kernels=kernels,
                                stands=stands,
                                release=release,
                                rc=version,
                                final=final)
        test_run.creater()


    def update_stp(self, version):
        zefir_table = ZefirResultTable(test_cycle_version=str(version),
                                    token=self.__conf_token,
                                    basic_auth=self.__basic,
                                    username=self.__username)
        zefir_table


    @staticmethod
    def write_log(message, log_name='acs', max_size_mb=10):
        """Запись в разные файлы логов с ротацией"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        log_file = f'{log_name}.log'
        
        # Ротация если файл больше max_size_mb
        if path.exists(log_file) and path.getsize(log_file) > max_size_mb * 1024 * 1024:
            file_size_mb = path.getsize(log_file) / 1024 / 1024 
            backup_file = f'{log_name}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'
            rename(log_file, backup_file)
            print(f"Log rotated: {backup_file} (was {file_size_mb:.2f} MB)")
        
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(f"{timestamp} - {message}\n")
        print(f"{timestamp} - {message}")


    def acs_create_snapshot(self, version: str, stand):
        if stand == 'AllStands':
            logname = 'acs_allstands'
        else:
            logname = f'acs_{stand}'

        self.write_log(f"\n\n=== ACS Snapshot Started ===", logname)
        self.write_log(f"Parameters: version={version}, stand={stand}", logname)
        
        try:
            res_all_repos = requests.get("http://allta.devos.astralinux.ru/rest/api/get-repo-path-as-json").text
            data_repos = json.loads(res_all_repos)
            needed_repos = data_repos.get(version)
            
            if needed_repos:
                self.write_log(f"Found needed repos for version {version}: {len(needed_repos)} repos", logname)
                repos_to_one_str = "\n".join(needed_repos)
                res_ver = requests.post(f"{ACS_BASE_URL}/versions", json={"name": version,
                                                                    "digit_name": version})
                data = res_ver.json()
                id_new_version = data.get("data")
                requests.post(f"{ACS_BASE_URL}/repos", json={"link": repos_to_one_str,
                                                        "version_id": id_new_version})
                self.write_log(f"Created version {version} with id {id_new_version}", logname)
                
            if version.startswith('1.8'):
                restore_version = '1.8.1.6'
            elif version.startswith('1.7'):
                restore_version = '1.7.5'
            else:
                self.write_log(f"ERROR: Unsupported version: {version}", logname)
                return {'error': f'Unsupported version: {version}'}
            
            self.write_log(f"Restore version: {restore_version}",logname)
            
            stand_mapping = {
                'stand3': 'LowServer',
                'stand4': 'MiddleServer',
                'stand5': 'HighServer',
                'stand10': 'LowServer2',
                'stand11': 'LowServer3',
                'stand12': 'LowServer4',
                'stand13': 'LowServer5', 
            }
            
            if stand == 'AllStands':
                self.write_log("Starting AllStands mode - will process 6 stands", logname)
                stands_config = [
                    ('stand3', 'LowServer'),
                    ('stand4', 'MiddleServer'),
                    ('stand10', 'LowServer2'),
                    ('stand11', 'LowServer3'),
                    ('stand12', 'LowServer4'),
                    ('stand13', 'LowServer5')
                ]
                
                total = len(stands_config)
                for idx, (stand_key, stand_name) in enumerate(stands_config, 1):
                    self.write_log(f"Processing stand {idx}/{total}: {stand_key} -> {stand_name}", logname)
                    
                    try:
                        busy_status_control(stand_key, 'ACS', version=version)
                        self.write_log(f"Busy status control passed for {stand_key}", logname)
                        
                        response = requests.post(f"{ACS_BASE_URL}/create_full_snap", 
                                            params={"restore_version": restore_version,
                                                    "version_to_update": version,
                                                    "password_cs": self.__password,
                                                    "stand_name": stand_name})
                        
                        self.write_log(f"ACS request for {stand_key}: status={response.status_code}", logname)
                        
                        if response.status_code != 200:
                            self.write_log(f"WARNING: Non-200 response for {stand_key}: {response.text[:200]}", logname)
                        
                        if idx < total:
                            self.write_log(f"Sleeping 300 seconds before next stand...", logname)
                            time.sleep(300)
                            
                    except Exception as e:
                        self.write_log(f"ERROR processing {stand_key}: {str(e)}", logname)
                        continue
                
                self.write_log(f"ACS completed for all stands with version {version}", logname)
                return {'message': f'ACS started for all stands with version {version}'}
            
            elif stand in stand_mapping:
                self.write_log(f"Processing single stand: {stand} -> {stand_mapping[stand]}", logname)
                
                busy_status_control(stand, 'ACS', version=version)
                self.write_log("Busy status control passed", logname)
                
                response = requests.post(f"{ACS_BASE_URL}/create_full_snap", 
                                    params={"restore_version": restore_version,
                                            "version_to_update": version,
                                            "password_cs": self.__password,
                                            "stand_name": stand_mapping[stand]})
                
                self.write_log(f"ACS request completed with status: {response.status_code}", logname)
                
                if response.status_code == 200:
                    self.write_log(f"SUCCESS: ACS started successfully for {stand}", logname)
                    return {'message': f'ACS started successfully for {stand}', 'details': response.text}
                else:
                    self.write_log(f"ERROR: ACS request failed for {stand}: status={response.status_code}", logname)
                    return {'error': f'ACS request failed with status {response.status_code}', 
                            'details': response.text}
            else:
                self.write_log(f"ERROR: Wrong stand parameter: {stand}", logname)
                return {'error': f'Wrong stand: {stand}. Available stands: {list(stand_mapping.keys())} + AllStands'}
                
        except Exception as e:
            self.write_log(f"UNEXPECTED ERROR: {str(e)}", logname)
            return {'error': f'Internal error: {str(e)}'}


    def generate_repo_path(self):
        pkg_path = '/dists/{}/main/binary-amd64/Packages'
        vers_path = '/dists/{}/Release'

        def sort_element(repo_list: list, element):
            [repo_list.insert(0, repo_list.pop(repo_list.index(i))) for i in repo_list if element in i]
            return repo_list[0]

        with open('./releases.json', 'r') as rj:
            links = json.load(rj)

        pkg_path_dict = {
            f"pkg_path_{key}": sort_element([f"{value.split(' ')[1]}{pkg_path}".format(value.split(' ')[2])  
            for value in links[key] if any(x in value for x in ['devel-repository', 'base-repository', 'installation'])], 'installation')
            for key in links.keys()
        }

        vers_path_dict = {
            f"vers_path_{key}": sort_element([f"{value.split(' ')[1]}{vers_path}".format(value.split(' ')[2])   
            for value in links[key] if any(x in value for x in ['devel-repository', 'base-repository', 'installation'])], 'installation')
            for key in links.keys()
        }

        return {**pkg_path_dict, **vers_path_dict}
        
        
    def write_allta_conf(self, data):
        with open('./allta_conf.json', 'w') as w:
            json.dump(data, w, indent=4)


    def add_kernels(self, value):
        with open('./allta_conf.json', 'r') as r:
            config = json.load(r)

        rc_kernels = get_kernels_from_rc(value, get_list=True)
        print(rc_kernels)
        config['kernels'] += [kern for kern in rc_kernels if kern not in set(config['kernels'])]
        return sorted(config['kernels'])


    def add_testrun_folder(self, rc):
        main_folder = 2744
        counter = 0

        def __create_testrun_folder(name: str):
                add_folder_url = f'https://jira.astralinux.ru/rest/atm/1.0/folder'
                headers = {
                    'Authorization': self.__basic
                }
                data = {
                        "projectKey": "BT",
                        "name": f"/stress_test/{name}",
                        "type": "TEST_RUN"
                        }

                print(data)
                response = requests.post(add_folder_url, headers=headers, json=data)
                print(response.status_code)
                print(response.text)
                value = response.json()
                print(value)
                print(f"vers {name.split('/')[-1]}")
                config['cycle_tree_index'][name.split('/')[-1]] = str(value['id'])
                config['cycle_tree_index'] = {k: v for k, v in sorted(config['cycle_tree_index'].items())}
                print(config['cycle_tree_index'])
                self.write_allta_conf(config)

        while counter < 2:
            counter += 1
            with open('./allta_conf.json', 'r') as r:
                config = json.load(r)

            print(config['cycle_tree_index'].keys())
            if rc not in config['cycle_tree_index'].keys():
                check_len_version = rc.split('.')
                if len(check_len_version) == 4 and check_len_version[3] != 'UU':
                    if '.'.join(check_len_version[:3]) in config['cycle_tree_index'].keys():
                        parentfolder = '.'.join(check_len_version[:3])
                        name = rc
                        __create_testrun_folder(f'{parentfolder}/{name}')
                    else: __create_testrun_folder('.'.join(check_len_version[:3]))
                elif len(check_len_version) == 6 and check_len_version[3] == 'UU':
                    if '.'.join(check_len_version[:5]) in config['cycle_tree_index'].keys():
                        parentfolder = '.'.join(check_len_version[:5])
                        name = rc
                        __create_testrun_folder(f'{parentfolder}/{name}')
                    else: __create_testrun_folder('.'.join(check_len_version[:5]))
                else: 
                    name = rc
                    __create_testrun_folder(name)


    def mod_allta_conf(self, value, rc, uu_value=None):
        with open('./allta_conf.json', 'r') as r:
            data = json.load(r)

        cz_name = 'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h "{}" \
                    -l ru_RU.UTF-8 startdisk restore {}-{}rc{} nvme0n1'

        if value not in data['releases_dict'].keys():
            if uu_value:
                data['releases_dict'][value] = uu_value
            else: data['releases_dict'][value] = value
            data['releases_dict'] = {k: v for k, v in sorted(data['releases_dict'].items())}
        if value not in data['rc_list']:
            data['rc_list'].append(value)
            data['rc_list'] = sorted(data['rc_list']) 
        if uu_value:
            if '.'.join(value.split('.')[:5]) not in data['releases_list']:
                data['releases_list'].append('.'.join(value.split('.')[:5]))
                data['releases_list'] = sorted(data['releases_list'])
        else:
            if '.'.join(value.split('.')[:3]) not in data['releases_list']:
                data['releases_list'].append('.'.join(value.split('.')[:3]))
                data['releases_list'] = sorted(data['releases_list'])
        if value not in data['release_version']:
            data['release_version'].append(value)
            data['release_version'] = sorted(data['release_version'])
        if value not in data['releases']:
            data['releases'].append(value)       
            data['releases'] = sorted(data['releases']) 
        if value not in data['build_rc_relation'].keys():
            data['build_rc_relation'][value] = rc
            data['build_rc_relation'] = {k: v for k, v in sorted(data['build_rc_relation'].items())}
            
        for stand in stands_type['phys'].keys():
            if uu_value:
                slice_value = 5
            else: slice_value = 3
            if stand != 'stand5':
                if value not in data['cz_comm'][stand].keys():
                    data['cz_comm'][stand][value] = cz_name.format(stands_ip[stand],
                                                                stands_type['phys'][stand],
                                                                ''.join(value.split('.')[:slice_value]),
                                                                ''.join(value.split('.')[slice_value:]))
            data['cz_comm'][stand] = {k: v for k, v in sorted(data['cz_comm'][stand].items())}

        self.write_allta_conf(data)
        
        repo = ReleaseToRepo(current_directory='.')
        repo.get_releases_index()
        repo.generate_releases_file()

        data['repo_path'] = {k: v for k, v in sorted(self.generate_repo_path().items())}
        self.write_allta_conf(data)

        data['kernels'] = self.add_kernels(value)
        self.write_allta_conf(data)

        self.add_testrun_folder(value)
        self.update_changelog(value)
    
    

    def addrc(self, build=None, rc=None):
        self.mod_allta_conf(value=build, rc=rc)
        return {'message': f'{rc} for {build} added successfully'}
            


    def adduurc(self, build=None, rc=None, uu_value=None):
        #uu_value Использовать только если UU, иначе игнорировать
        self.mod_allta_conf(value=build, rc=rc, uu_value=uu_value)
        return {'message': f'{rc} for {uu_value} added successfully'}
      


    def acs(self, rc=None, stand=None):
        result = self.acs_create_snapshot(version=rc, stand=stand)
        
        if isinstance(result, dict) and 'error' in result:
            return result
        
        if isinstance(result, dict) and 'message' in result:
            return result
        
        return {'message': f'ACS start for {rc} is successfully', 'details': result}
            


    def add_testrun(self, rc=None, final=None):
        if final == 'final':
            self.create_test_run(version=rc, final=True)
            return {'message': f'Add testrun for {rc} in mode {final} is successfully'}
        else: self.create_test_run(version=rc)
        return {'message': f'Add testrun for {rc} in mode {final} is successfully'}
            


    def runtests(self, rc=None, stand=None):
        self.run_tests(rc, stand)
            


    def runalltests(self, rc=None):           
        [self.run_tests(rc, test) for test in stands_type['phys'].keys()]


            

#!/bin/python3

from flask import Flask, render_template, request, send_from_directory, redirect, url_for
import string
import random
import subprocess
from os import path, remove, kill
import signal
#import logging
import threading
#from queue import Queue, Empty


app = Flask(__name__)
app.config['SECRET_KEY'] = 'srv_2113'

# logging.basicConfig(
#         filename='front.log', 
#         level=logging.INFO, 
#         filemode='a',
#         format='%(asctime)s - %(levelname)s - %(funcName)s: %(lineno)d - %(message)s',
#         datefmt='%Y-%m-%d %H:%M:%S',
# )
# logging.info('Start front logging\n\n')

# def stream_watcher(identifier, stream, queue):
#     for line in stream:
#         queue.put((identifier, line))

#     if not stream.closed:
#         stream.close()

# def log_outputs(process):
#     q = Queue()
#     out_thread = threading.Thread(target=stream_watcher, name='stdout-watcher', args=('STDOUT', process.stdout, q), daemon=True)
#     err_thread = threading.Thread(target=stream_watcher, name='stderr-watcher', args=('STDERR', process.stderr, q), daemon=True)

#     out_thread.start()
#     err_thread.start()

#     while True:
#         try:
#             if not out_thread.is_alive() and not err_thread.is_alive():
#                 break

#             identifier, line = q.get_nowait()
#             if identifier == 'STDOUT':
#                 logging.info(f'Standart Out:\n{line}')
#             else:
#                 logging.error(f'Standart Error:\n{line}')

#         except Empty:
#             pass


def generate_random_string(length):
    letters_and_digits = string.ascii_letters + string.digits
    rand_string = ''.join(random.sample(letters_and_digits, length))
    return rand_string * 5

with open('/home/u/url', 'r') as r:
    main_url = r.read().replace('\n', '').replace('\r', '')
with open('/home/u/up', 'r') as r:
    up = r.read()

options = sorted(['XFS', 'EXT4', 'NTFS', 'EXT4 parsec', 'postgresql', 'postgresql-sm', 
                  'auditd-p', 'auditd-u', 'auditd-f', 'syslog-ng', 'unix', 'postgresql-aud-off', 'SD-overflow', 'RAM-overflow'])
releases = ['1.7.1', '1.7.2', '1.7.3', '1.7.3.UU.1', '1.7.3.UU.2', '1.7.4', '1.7.4.UU.1', 'debian10', 'debian10-5.15', 'debian11-6.1']
kernels = ['5.10.142-1-generic', '5.15.0-33-generic', '5.15.0-33-lowlatency', '5.10.176-1-generic', '5.15.0-70-generic', '5.15.0-70-lowlatency']
#main_url = generate_random_string(60)
red_gif = 'http://10.177.103.10:8000/static/red.gif'
ping_gif = 'http://10.177.103.10:8000/static/ping.gif'
green_gif = 'http://10.177.103.10:8000/static/green.gif'
done_gif = 'http://10.177.103.10:8000/static/done.gif'
status_stand1 = '-'
status_stand2 = '-'
status_stand3 = '-'
status_stand4 = '-'
process = None
process2 = None
process3 = None
process4 = None
pid = None
pid2 = None
pid3 = None
pid4 = None



@app.route('/')
def redirect_login():
    return redirect(url_for('login'))

# @app.errorhandler(404)
# def page_not_found(e):
#     # При обращении к несуществующему URL, перенаправляем на правильный URL
#     return redirect(url_for('index'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        # Обработка логики входа пользователя
        username = request.form.get('username')
        password = request.form.get('password')
        if username == 'u' and password == str(up).replace('\n', '').replace('\r', ''):
            return redirect(f'/{main_url}')  # Перенаправление на главную страницу после успешного входа
        else:
            return render_template('login.html', error="Неверный логин или пароль")
    else:
        return render_template('login.html')


@app.route(f'/{main_url}', methods=['GET', 'POST'])
def index():
    tests = []
    stand = []
    stand1_log = ''
    stand2_log = ''
    stand3_log = ''
    stand4_log = ''
    
    try:
        with open('conf/actual_log_path_stand1.conf', 'r') as rl:
            real_path1 = rl.read()
            with open(real_path1, 'r') as r:
                stand1_log = r.read()
        with open('conf/actual_log_path_stand2.conf', 'r') as rl:
            real_path2 = rl.read()
            with open(real_path2, 'r') as r:
                stand2_log = r.read()
        with open('conf/actual_log_path_stand3.conf', 'r') as rl:
            real_path3 = rl.read()
            with open(real_path3, 'r') as r:
                stand3_log = r.read()
        with open('conf/actual_log_path_stand4.conf', 'r') as rl:
            real_path4 = rl.read()
            with open(real_path4, 'r') as r:
                stand4_log = r.read()
    except FileNotFoundError:
        pass


    try:
        with open('conf/all_output_stand1.log', 'r') as r:
            progress_stand1 = r.read()
    except FileNotFoundError:
        progress_stand1 = ''
    try:
        with open('conf/all_output_stand2.log', 'r') as r:
            progress_stand2 = r.read()
    except FileNotFoundError:
        progress_stand2 = ''
    try:
        with open('conf/all_output_stand3.log', 'r') as r:
            progress_stand3 = r.read()
    except FileNotFoundError:
        progress_stand3 = ''
    try:
        with open('conf/all_output_stand4.log', 'r') as r:
            progress_stand4 = r.read()
    except FileNotFoundError:
        progress_stand4 = ''




    try:
        with open('conf/status_output_stand1.log', 'r') as rsc:
            stand1_sett = rsc.read()
    except FileNotFoundError:
        stand1_sett = ''
    try:
        with open('conf/status_output_stand2.log', 'r') as rsc:
            stand2_sett = rsc.read()
    except FileNotFoundError:
        stand2_sett = ''
    try:
        with open('conf/status_output_stand3.log', 'r') as rsc:
            stand3_sett = rsc.read()
    except FileNotFoundError:
        stand3_sett = ''
    try:
        with open('conf/status_output_stand4.log', 'r') as rsc:
            stand4_sett = rsc.read()
    except FileNotFoundError:
        stand4_sett = ''



    with open('conf/work_status_stand1.conf', 'r') as rs:
        status_stand1 = rs.read()
        if status_stand1 == 'Остановлен':
            status_gif_stand1 = red_gif
        elif status_stand1 == 'Запущен':
            status_gif_stand1 = green_gif
        elif status_stand1 == 'Готово':
            status_gif_stand1 = done_gif
        else: 
            status_stand1 = 'Нераспознан'
            status_gif_stand1 = ping_gif

    with open('conf/work_status_stand2.conf', 'r') as rs:
        status_stand2 = rs.read()
        if status_stand2 == 'Остановлен':
            status_gif_stand2 = red_gif
        elif status_stand2 == 'Запущен':
            status_gif_stand2 = green_gif
        elif status_stand2 == 'Готово':
            status_gif_stand2 = done_gif
        else: 
            status_stand2 = 'Нераспознан'
            status_gif_stand2 = ping_gif

    with open('conf/work_status_stand3.conf', 'r') as rs:
        status_stand3 = rs.read()
        if status_stand3 == 'Остановлен':
            status_gif_stand3 = red_gif
        elif status_stand3 == 'Запущен':
            status_gif_stand3 = green_gif
        elif status_stand3 == 'Готово':
            status_gif_stand3 = done_gif
        else: 
            status_stand3 = 'Нераспознан'
            status_gif_stand3 = ping_gif

    with open('conf/work_status_stand4.conf', 'r') as rs:
        status_stand4 = rs.read()
        if status_stand4 == 'Остановлен':
            status_gif_stand4 = red_gif
        elif status_stand4 == 'Запущен':
            status_gif_stand4 = green_gif
        elif status_stand4 == 'Готово':
            status_gif_stand4 = done_gif
        else: 
            status_stand4 = 'Нераспознан'
            status_gif_stand4 = ping_gif




    with open('conf/tests_args.conf', 'r') as r:
            test_list = str(r.read())
    with open('conf/releas_args.conf', 'r') as r:
            releas_list = str(r.read())
    try:
        with open('conf/kernel_args.conf', 'r') as r:
                kernel_list = str(r.read())
    except FileNotFoundError:
        kernel_list = 'None'

    if request.method == 'POST':
        selected_options = request.form.getlist('options')
        selected_stand = request.form.getlist('stands')
        
        tests = [option for option in options if option in selected_options]
        if not tests:
            tests = 'Тесты не выбраны'
        #tests = ', '.join(test).replace(',','')
        
        # kernel = request.form.getlist('kernel')
        # with open('conf/kernel_args.conf', 'w') as w:
        #     if len(kernel) == 0:
        #         w.write('None')
        #     else: w.write(str(kernel))
        kernel = request.form.get('kernel')
        with open('conf/kernel_args.conf', 'w') as w:
            w.write(str(kernel))

        releas = request.form.getlist('releas')
        with open('conf/releas_args.conf', 'w') as w:
            w.write(str(releas))
        if not releas:
            releas = 'Релиз не выбран'
        
        with open('conf/tests_args.conf', 'w') as w:
            w.write(str(tests))
        
        return redirect(url_for('index'))
            

    return render_template('main.html', 
                           options=options, 
                           test_list=test_list,
                           releas_list=releas_list,
                           kernel_list=kernel_list, 
                           releases=releases, 
                           stand=stand,
                           kernels=kernels,
                           status_stand1=status_stand1,
                           status_stand2=status_stand2,
                           status_stand3=status_stand3,
                           status_stand4=status_stand4,
                           stand1_log=stand1_log,
                           stand2_log=stand2_log,
                           stand3_log=stand3_log,
                           stand4_log=stand4_log,
                           status_gif_stand1=status_gif_stand1,
                           status_gif_stand2=status_gif_stand2,
                           status_gif_stand3=status_gif_stand3,
                           status_gif_stand4=status_gif_stand4,
                           stand1_sett=stand1_sett,
                           stand2_sett=stand2_sett,
                           stand3_sett=stand3_sett,
                           stand4_sett=stand4_sett,
                           progress_stand1=progress_stand1,
                           progress_stand2=progress_stand2,
                           progress_stand3=progress_stand3,
                           progress_stand4=progress_stand4,
                           main_url=main_url)

@app.route('/static/<path:path>')
def send_static(path):
    return send_from_directory('static', path)


@app.route('/run-command-stand1', methods=['POST'])
def run_command_stand1():
    global pid
    global process
    command = request.form.get('command1')
    kernel = None

    if command == 'start':
        with open("front_stand1.log", "w") as w:
            w.write('Start front logging\n\n')
        with open('conf/work_status_stand1.conf', 'w') as w:
            w.write('Запущен')
        with open('conf/tests_args.conf', 'r') as r:
            tests = r.read()
        with open('conf/releas_args.conf', 'r') as r:
            releas = str(r.read()).replace('[', '').replace(']', '').strip("'")
        if path.isfile('conf/kernel_args.conf'):
            with open('conf/kernel_args.conf', 'r') as r:
                kernel = r.read()

        def run_command_and_log(command):
            with open("front_stand1.log", "a") as output:
                process = subprocess.Popen(command, stdout=output, stderr=output, shell=True, text=True)
            return process.pid
    
        command_to_run = f'python3 bendiks_back.py -rs {releas} -st stand1 -ts "{tests}"'
        command_to_run_kernel = f'python3 bendiks_back.py -rs {releas} -st stand1 -ts "{tests}" -kn "{kernel}"'
        if kernel != 'None':
            pid = threading.Thread(target=run_command_and_log, args=(command_to_run_kernel,)).start()
        else:
            pid = threading.Thread(target=run_command_and_log, args=(command_to_run,)).start()
        
        if path.isfile('conf/kernel_args.conf'):
            remove('conf/kernel_args.conf')
    
    elif command == 'ok':
        with open('conf/work_status_stand1.conf', 'w') as w:
            w.write('Остановлен')
    
    elif command == 'stop':
        if pid is not None:
            try:
                kill(pid, 0) 
                kill(pid, signal.SIGKILL) 
            except ProcessLookupError:
                print(f"Процесс с pid {pid} не существует")
            
        if process is not None:
            process.terminate()
            process = None

        if path.isfile('conf/kernel_args.conf'):
            remove('conf/kernel_args.conf')
        with open('conf/work_status_stand1.conf', 'w') as w:
            w.write('Остановлен')

    return redirect(url_for('index'))



@app.route('/run-command-stand2', methods=['POST'])
def run_command_stand2():
    global pid2
    global process2
    command = request.form.get('command2')
    kernel = None

    if command == 'start':
        with open("front_stand2.log", "w") as w:
            w.write('Start front logging\n\n')
        with open('conf/work_status_stand2.conf', 'w') as w:
            w.write('Запущен')
        with open('conf/tests_args.conf', 'r') as r:
            tests = r.read()
        with open('conf/releas_args.conf', 'r') as r:
            releas = str(r.read()).replace('[', '').replace(']', '').strip("'")
        
        if path.isfile('conf/kernel_args.conf'):
            with open('conf/kernel_args.conf', 'r') as r:
                kernel = r.read()

        command_to_run = f'python3 bendiks_back.py -rs {releas} -st stand2 -ts "{tests}"'
        command_to_run_kernel = f'python3 bendiks_back.py -rs {releas} -st stand2 -ts "{tests}" -kn "{kernel}"'
        
        def run_command_and_log(command):
            with open("front_stand2.log", "a") as output:
                process = subprocess.Popen(command, stdout=output, stderr=output, shell=True, text=True)
            return process.pid
        
        if kernel != 'None':
            pid2 = threading.Thread(target=run_command_and_log, args=(command_to_run_kernel,)).start()
        else:
            pid2 = threading.Thread(target=run_command_and_log, args=(command_to_run,)).start()

        if path.isfile('conf/kernel_args.conf'):
            remove('conf/kernel_args.conf')
    
    elif command == 'ok':
        with open('conf/work_status_stand2.conf', 'w') as w:
            w.write('Остановлен')
    
    elif command == 'stop':
        if pid2 is not None:
            try:
                kill(pid2, 0) 
                kill(pid2, signal.SIGKILL) 
            except ProcessLookupError:
                print(f"Процесс с pid {pid2} не существует")
            
        if process2 is not None:
            process2.terminate()
            process2 = None

        if path.isfile('conf/kernel_args.conf'):
            remove('conf/kernel_args.conf')
        with open('conf/work_status_stand2.conf', 'w') as w:
            w.write('Остановлен')

    return redirect(url_for('index'))



@app.route('/run-command-stand3', methods=['POST'])
def run_command_stand3():
    global pid3
    global process3
    command = request.form.get('command3')
    kernel = None

    if command == 'start':
        with open("front_stand3.log", "w") as w:
            w.write('Start front logging\n\n')
        with open('conf/work_status_stand3.conf', 'w') as w:
            w.write('Запущен')
        with open('conf/tests_args.conf', 'r') as r:
            tests = r.read()
        with open('conf/releas_args.conf', 'r') as r:
            releas = str(r.read()).replace('[', '').replace(']', '').strip("'")
        
        if path.isfile('conf/kernel_args.conf'):
            with open('conf/kernel_args.conf', 'r') as r:
                kernel = r.read()

        command_to_run = f'python3 bendiks_back.py -rs {releas} -st stand3 -ts "{tests}"'
        command_to_run_kernel = f'python3 bendiks_back.py -rs {releas} -st stand3 -ts "{tests}" -kn "{kernel}"'

        def run_command_and_log(command):
            with open("front_stand3.log", "a") as output:
                process = subprocess.Popen(command, stdout=output, stderr=output, shell=True, text=True)
            return process.pid
        
        if kernel != 'None':
            pid3 = threading.Thread(target=run_command_and_log, args=(command_to_run_kernel,)).start()
        else:
            pid3 = threading.Thread(target=run_command_and_log, args=(command_to_run,)).start()

        if path.isfile('conf/kernel_args.conf'):
            remove('conf/kernel_args.conf')
    
    elif command == 'ok':
        with open('conf/work_status_stand3.conf', 'w') as w:
            w.write('Остановлен')
        
    
    elif command == 'stop':
        if pid3 is not None:
            try:
                kill(pid3, 0) 
                kill(pid3, signal.SIGKILL)
            except ProcessLookupError:
                print(f"Процесс с pid {pid3} не существует")
            
        if process3 is not None:
            process3.terminate()
            process3 = None

        if path.isfile('conf/kernel_args.conf'):
            remove('conf/kernel_args.conf')
        with open('conf/work_status_stand3.conf', 'w') as w:
            w.write('Остановлен')

    return redirect(url_for('index'))


@app.route('/run-command-stand4', methods=['POST'])
def run_command_stand4():
    global pid4
    global process4
    command = request.form.get('command4')
    kernel = None

    if command == 'start':
        with open("front_stand4.log", "w") as w:
            w.write('Start front logging\n\n')
        with open('conf/work_status_stand4.conf', 'w') as w:
            w.write('Запущен')
        with open('conf/tests_args.conf', 'r') as r:
            tests = r.read()
        with open('conf/releas_args.conf', 'r') as r:
            releas = str(r.read()).replace('[', '').replace(']', '').strip("'")
        
        if path.isfile('conf/kernel_args.conf'):
            with open('conf/kernel_args.conf', 'r') as r:
                kernel = r.read()

        command_to_run = f'python3 bendiks_back.py -rs {releas} -st stand4 -ts "{tests}"'
        command_to_run_kernel = f'python3 bendiks_back.py -rs {releas} -st stand4 -ts "{tests}" -kn "{kernel}"'

        def run_command_and_log(command):
            with open("front_stand4.log", "a") as output:
                process = subprocess.Popen(command, stdout=output, stderr=output, shell=True, text=True)
            return process.pid
        
        if kernel != 'None':
            pid4 = threading.Thread(target=run_command_and_log, args=(command_to_run_kernel,)).start()
        else:
            pid4 = threading.Thread(target=run_command_and_log, args=(command_to_run,)).start()

        if path.isfile('conf/kernel_args.conf'):
            remove('conf/kernel_args.conf')
    
    elif command == 'ok':
        with open('conf/work_status_stand4.conf', 'w') as w:
            w.write('Остановлен')



#
#        """TODO убивать дочерние процессы"""
#




    elif command == 'stop':
        if pid4 is not None:
            try:
                kill(pid4, 0) 
                kill(pid4, signal.SIGKILL) 
            except ProcessLookupError:
                print(f"Процесс с pid {pid4} не существует")
            
        if process4 is not None:
            process4.terminate()
            process4 = None

        if path.isfile('conf/kernel_args.conf'):
            remove('conf/kernel_args.conf')
        with open('conf/work_status_stand4.conf', 'w') as w:
            w.write('Остановлен')

    return redirect(url_for('index'))




# if __name__ == '__main__':
#     app.run(host='127.0.0.1', port=8000, debug=True)


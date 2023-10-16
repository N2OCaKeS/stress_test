#!/bin/python3

from flask import (Flask, 
                   render_template, 
                   request, 
                   send_from_directory, 
                   redirect, 
                   url_for, 
                   jsonify)
import socket
from libs.zefir import ZefirResultTable
import psycopg2
import json
from time import sleep
from libs.libilo import iLOConsoleCaller
from libs.libbend import (index_page,
                          BackgroundTasks,
                          run_command_on_stand,
                          ssh_command,
                          background_task_main,
                          background_task_brest,
                          background_stat_storage_main,
                          update_settings_block,
                          get_kernels_from_rc,
                          main_url,
                          mobile_url,
                          brest_url,
                          up,
                          bp, 
                          psyc,
                          stands_ip,
                          user_app)


app = Flask(__name__)
app.config['SECRET_KEY'] = 'srv_2113'

with open('/home/u/tokens.json', 'r') as r:
    tokens = json.load(r)
__conf_token = tokens['conf_token']
__username = tokens['username']
__jira_token = tokens['jira_token']

thread_task_main = BackgroundTasks(target=background_task_main)
thread_task_brest = BackgroundTasks(target=background_task_brest)
thread_storage_main = BackgroundTasks(target=background_stat_storage_main)
thread_task_main.start()
thread_task_brest.start()
thread_storage_main.start()


@app.route('/update_cpumeminfo', methods=['GET'])
def restart_cpumeminfo():
    thread_task_main.stop()
    thread_task_brest.stop()
    thread_storage_main.stop()
    sleep(1)
    thread_task_main.start()
    thread_task_brest.start()
    thread_storage_main.start()
    return 0


@app.route('/')
def redirect_login():
    return redirect(url_for('login'))


@app.errorhandler(500)
def internal_server_error(e):
    #app.logger.error(e)
    return redirect(url_for('login'))


@app.errorhandler(404)
def page_not_found(e):
    #app.logger.error(e)
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if username == user_app and password == str(up).replace('\n', '').replace('\r', ''):
            return redirect(f'/{main_url}')
        elif username == user_app and password == str(bp).replace('\n', '').replace('\r', ''):
            return redirect(f'/{brest_url}')  
        else:
            return render_template('login.html', error="Неверный логин или пароль")
    else:
        return render_template('login.html')


@app.route(f'/{mobile_url}', methods=['GET', 'POST'])
def index_mobile():
    return index_page('mobile')
    

@app.route(f'/{main_url}', methods=['GET', 'POST'])
def index_main():
    return index_page('main')
    

@app.route(f'/{brest_url}', methods=['GET', 'POST'])
def index_brest():
    return index_page('brest')
    

@app.route('/static/<path:path>')
def send_static(path):
    return send_from_directory('static', path)


@app.route('/run-command-stand<num>', methods=['POST'])
def run_command(num):
    return run_command_on_stand(num)


@app.route('/update/<version>')
def update_stp(version):
    """
    Обновить состав тестового прогона
    """
    zefir_table = ZefirResultTable(test_cycle_version=str(version),
                                   token=__conf_token,
                                   basic_auth=__jira_token,
                                   username=__username)
    zefir_table

    return index_page('main')


@app.route('/api/load_info/<stand>', methods=['GET'])
def get_load_info(stand):
    conn = psycopg2.connect(
                            host=psyc['host'],
                            database=psyc['database'],
                            user=psyc['user'],
                            password=psyc['password']
                            )

    id = 1
    cursor = conn.cursor()
    select_query = f"SELECT {stand}_cpu, {stand}_cpu_user, {stand}_cpu_system, {stand}_ram, {stand}_nvme, {stand}_sda, {stand}_temp_cpu FROM main_table WHERE id = %s"
    cursor.execute(select_query, [id])

    result = cursor.fetchone()
    if result is not None:
        load_cpu = result[0]
        load_cpu_user = result[1]
        load_cpu_system = result[2]
        temp_cpu = result[6]
        load_ram = result[3]
        load_nvme = result[4]
        load_sda = result[5]
    else:
        load_cpu = '-'
        load_cpu_user = '-'
        load_cpu_system = '-'
        temp_cpu = '-'
        load_ram = '-'
        load_nvme = '-'
        load_sda = '-'

    cursor.close()
    conn.close()
    
    return jsonify({
        f"load_cpu_{stand}":load_cpu,
        f"load_cpu_user_{stand}":load_cpu_user,
        f"load_cpu_system_{stand}":load_cpu_system,
        f"temp_cpu_{stand}":temp_cpu,
        f"load_ram_{stand}":load_ram,
        f"load_nvme_{stand}":load_nvme,
        f"load_sda_{stand}":load_sda
    }) 


@app.route('/update_page_info_<page>')
def update(page):
    return index_page(f'{page}', ajax=True)


@app.route('/update_power_status/<stand>')
def check_running_system(stand):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.5)   
    try:
        result = sock.connect_ex((stands_ip[stand], 22))
        if result == 0:
            return jsonify(is_running=True)
        else: 
            return jsonify(is_running=False)
    except socket.timeout:
        return jsonify(is_running=False)
    except Exception as e:
        print(e)
        return jsonify(is_running=False)
    finally:
        sock.close()

  
@app.route('/reboot/<stand>', methods=['POST'])
def reboot(stand):
    ssh_command('sudo reboot', 
                stand_ip=stands_ip[stand])
   

@app.route('/poweroff/<stand>', methods=['POST'])
def poweroff(stand):
    ssh_command('sudo poweroff', 
                stand_ip=stands_ip[stand])


@app.route('/ilo/<stand>', methods=['POST'])
def ilo_console_caller(stand):
    icc = iLOConsoleCaller(stand_number=stand)
    icc.ilo_console_loader()


@app.route('/update_block_<part>', methods=['GET', 'POST'])
def update_block(part):
    if part == 'components':
        return update_settings_block()
    else:
        return get_kernels_from_rc(part)

# if __name__ == '__main__':
#     app.run(host='127.0.0.1', port=8000, debug=True)


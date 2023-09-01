#!/bin/python3

from flask import (Flask, 
                   render_template, 
                   request, 
                   send_from_directory, 
                   redirect, 
                   url_for, 
                   jsonify)
import socket
import threading
from libs.zefir import ZefirResultTable
import threading
import psycopg2
import json
from libs.libilo import iLOConsoleCaller
from libs.libbend import (index_page,
                          run_command_on_stand,
                          ssh_command,
                          background_task,
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


t = threading.Thread(target=background_task)
t.start()


@app.route('/')
def redirect_login():
    return redirect(url_for('login'))


@app.errorhandler(404)
def page_not_found():
    # При обращении к несуществующему URL, перенаправляем на правильный URL
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

    return redirect(url_for('index'))


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
    select_query = f"SELECT {stand}_cpu, {stand}_ram FROM main_table WHERE id = %s"
    cursor.execute(select_query, [id])

    result = cursor.fetchone()
    if result is not None:
        load_cpu = result[0]
        load_ram = result[1]
    else:
        load_cpu = '-'
        load_ram = '-'

    cursor.close()
    conn.close()
    
    return jsonify({
        f"load_cpu_{stand}": load_cpu,
        f"load_ram_{stand}": load_ram
    }) 


@app.route('/update_page_info')
def update():
    return index_page('main', ajax=True)


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


# if __name__ == '__main__':
#     app.run(host='127.0.0.1', port=8000, debug=True)


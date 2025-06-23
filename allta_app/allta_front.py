#!/home/u/python/Python-3.12.1/venv/bin/python3.12

from flask import (Flask, 
                   render_template, 
                   request, 
                   send_from_directory, 
                   redirect, 
                   url_for, 
                   jsonify,
                   send_file)
from flask_cors import CORS
import socket
from libs.zefir import ZefirResultTable
import psycopg2
#import asyncio
import json
from time import sleep
from libs.libilo import iLOConsoleCaller
from libs.liballta import (index_page,
                          BackgroundTasks,
                          BootOrder,
                          run_command_on_stand,
                          ssh_command,
                          background_task_main,
                          update_settings_block,
                          update_changelog_block,
                          get_kernels_from_rc,
                          backup_snapshot,
                          backup_vm_snapshot,
                          busy_status_control,
                          power_on_stand,
                          main_url,
                          mobile_url,
                          brest_url,
                          up,
                          bp, 
                          psyc,
                          stands_ip,
                          user_app)
from allta_image_conf import testname_columns, JIRA_URL, CONFLUENCE_URL, known_bugs, annotations
from backup.backuplibs import Backup, check_command
from statistics_conf import statistics_conf
import requests


app = Flask(__name__)
CORS(app)
app.config['SECRET_KEY'] = 'srv_2413'

with open('/home/u/tokens.json', 'r') as r:
    tokens = json.load(r)
__conf_token = tokens['conf_token']
__username = tokens['username']
__jira_token = tokens['jira_token']

thread_task_main = BackgroundTasks(target=background_task_main)
#thread_task_brest = BackgroundTasks(target=background_task_brest)
#thread_storage_main = BackgroundTasks(target=background_stat_storage_main)
thread_task_main.start()
#thread_task_brest.start()
#thread_storage_main.start()


@app.route('/update_cpumeminfo', methods=['GET'])
def restart_cpumeminfo():
    thread_task_main.stop()
    #thread_task_brest.stop()
 #   thread_storage_main.stop()
    sleep(5)
    thread_task_main.start()
    #thread_task_brest.start()
    #thread_storage_main.start()
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
    if stand == "stand1" or stand == "stand2" or stand == "stand6" or stand == "stand7" or stand == "stand8" or stand == "stand9":
        ssh_command('sudo reboot', 
                    stand_ip=stands_ip[stand])
        return {"status": "success", "message": "ssh reboot entered"}, 200
    elif stand == "stand3" or stand == "stand4" or stand == "stand5":
        ipmi = BootOrder(stand=stand)
        ipmi.reset()
        return {"status": "success", "message": "ipmi reboot entered"}, 200
    else: return {"status": "success", "message": f"stand {stand} not detected"}, 404
   

@app.route('/poweroff/<stand>', methods=['POST'])
def poweroff(stand):
    ssh_command('sudo poweroff', 
                stand_ip=stands_ip[stand])
    return {"status": "success"}, 200


@app.route('/poweron/<stand>', methods=['POST'])
def poweron(stand):
    power_on_stand(stand)
    return {"status": "success"}, 200


@app.route('/ilo/<stand>', methods=['POST'])
def ilo_console_caller(stand):
    icc = iLOConsoleCaller(stand_number=stand)
    icc.ilo_console_loader()


@app.route('/update_changelog_block_<version>', methods=['GET', 'POST'])
def update_block_changelog(version):
    return update_changelog_block(version)


@app.route('/update_kernel_block_<part>', methods=['GET', 'POST'])
def update_kernel_block(part):
    if part == 'components':
        return update_settings_block()
    else:
        return get_kernels_from_rc(part)
    

@app.route('/backup/<stand>/<version>')
def backup(stand, version):
    """
    Загрузить нужный снимок
    """
    if stand == "stand6" or stand == "stand7" or stand == "stand8" \
    or stand == "stand9" or stand == "stand1" or stand == "stand2":
        backup_vm_snapshot(stand, version)
    else:
        backup_snapshot(stand, version)
    return index_page('main')


@app.route('/busy_status/<stand>/<name>')
def busy_status(stand, name):
    """
    установить нужный статус
    """
    busy_status_control(stand, name)
    return index_page('main')


@app.route('/rest/api/busy_status_off/<stand>')
def busy_status_stop(stand):
    """
    установить статус в состояние "Остановить"
    """
    busy_status_control(stand, 'stop')
    return index_page('main')


@app.route('/backup/allta', methods=['POST'])
def backup_request():
    backup_ip = '10.177.5.21'
    username = request.form.get('username')
    password = request.form.get('password')
    connection_status = check_command(ip=backup_ip,
                                      user=username,
                                      password=password)

    if 'Authentication failed' in connection_status:
        return {"status": "failed", "message": "Authentication failed"}, 401
    else:
        backup = Backup(ip=backup_ip,
                        user=username,
                        password=password)

        backup.run()
        return {"status": "success", "message": "Backup successfully completed"}, 200


@app.route('/all-statistics', methods=['POST'])
def all_statistics():
    url = 'http://allta.devos.astralinux.ru:7777/all-statistics'
    data = {
        'username':request.form.get('username'),
        'token':request.form.get('token')
    }

    headers = {
    'Content-Type': 'application/json'
    }

    requests.post(url=url, data=json.dumps(data), headers=headers)
    return {"status": "success", "message": "Recalculate all statistics successfully done"}, 200


# Apache statistics
@app.route("/apache-statistics", methods=['POST'])
def apache_statistics():
    url = 'http://allta.devos.astralinux.ru:7777/base-statistics'
    data = {
            'title_statistics':'Apache',
            'username': request.form.get('username'),
            'token': request.form.get('token'),
            'set_of_test_types': statistics_conf['Apache']["set_of_test_types"]
    }

    headers = {
    'Content-Type': 'application/json'
    }

    requests.post(url=url, data=json.dumps(data), headers=headers)
    return {"status": "success", "message": "Recalculate Apache statistics successfully done"}, 200


# FreeIPA statistics
@app.route("/freeipa-statistics", methods=['POST'])
def freeipa_statistics():
    url = 'http://allta.devos.astralinux.ru:7777/freeipa-statistics'
    data = {
            'title_statistics':'FreeIPA',
            'username': request.form.get('username'),
            'token': request.form.get('token'),
            'set_of_test_types': statistics_conf['FreeIPA']["set_of_test_types"]
    }

    headers = {
    'Content-Type': 'application/json'
    }

    requests.post(url=url, data=json.dumps(data), headers=headers)
    return {"status": "success", "message": "Recalculate FreeIPA statistics successfully done"}, 200

# Parsec statistics
@app.route("/parsec-statistics", methods=['POST'])
def parsec_statistics():
    url = 'http://allta.devos.astralinux.ru:7777/parsec-statistics'
    data = {
            'title_statistics': 'Parsec',
            'username': request.form.get('username'),
            'token': request.form.get('token'),
            'set_of_test_types': statistics_conf['Parsec']["set_of_test_types"],
            'comparison_list': statistics_conf['Parsec']["comparison_list"]
    }
    
    headers = {
    'Content-Type': 'application/json'
    }

    requests.post(url=url, data=json.dumps(data), headers=headers)
    return {"status": "success", "message": "Recalculate Parsec statistics successfully done"}, 200


# PostgreSQL statistics
@app.route("/postgresql-statistics", methods=['POST'])
def postgresql_statistics():
    url = 'http://allta.devos.astralinux.ru:7777/postgresql-statistics'
    data = {
            'title_statistics': 'PostgreSQL',
            'username': request.form.get('username'),
            'token': request.form.get('token'),
            'set_of_test_types': statistics_conf['PostgreSQL']["set_of_test_types"],
            'comparison_list': statistics_conf['PostgreSQL']["comparison_list"],
            'comparison_kernel_list': statistics_conf['PostgreSQL']["comparison_kernel_list"]
    }

    headers = {
    'Content-Type': 'application/json'
    }

    requests.post(url=url, data=json.dumps(data), headers=headers)
    return {"status": "success", "message": "Recalculate PostgreSQL statistics successfully done"}, 200


# Qemu/KVM/Libvirt statistics
@app.route("/virt-statistics", methods=['POST'])
def virt_statistics():
    url = 'http://allta.devos.astralinux.ru:7777/virt-statistics'
    data = {
            'title_statistics': 'Qemu/KVM/Libvirt',
            'username': request.form.get('username'),
            'token': request.form.get('token'),
            'set_of_test_types': statistics_conf['Qemu/KVM/Libvirt']["set_of_test_types"],
            'comparison_list': statistics_conf['Qemu/KVM/Libvirt']["comparison_list"]
    }

    headers = {
    'Content-Type': 'application/json'
    }

    requests.post(url=url, data=json.dumps(data), headers=headers)
    return {"status": "success", "message": "Recalculate Qemu/KVM/Libvirt statistics successfully done"}, 200


# UnixBench statistics
@app.route("/unixbench-statistics", methods=['POST'])
def unixbench_statistics():
    url = 'http://allta.devos.astralinux.ru:7777/base-statistics'
    data = {
            'title_statistics': 'UnixBench',
            'username': request.form.get('username'),
            'token': request.form.get('token'),
            'set_of_test_types': statistics_conf['UnixBench']["set_of_test_types"],
            'comparison_list': statistics_conf['UnixBench']["comparison_list"]
    }

    headers = {
    'Content-Type': 'application/json'
    }

    requests.post(url=url, data=json.dumps(data), headers=headers)
    return {"status": "success", "message": "Recalculate UnixBench statistics successfully done"}, 200


# Системные службы statistics
@app.route("/systemservices-statistics", methods=['POST'])
def systemservices_statistics():
    url = 'http://allta.devos.astralinux.ru:7777/base-statistics'
    data = {
            'title_statistics':'Системные службы',
            'username': request.form.get('username'),
            'token': request.form.get('token'),
            'set_of_test_types': statistics_conf['Системные службы']["set_of_test_types"]
    }

    headers = {
    'Content-Type': 'application/json'
    }

    requests.post(url=url, data=json.dumps(data), headers=headers)
    return {"status": "success", "message": "Recalculate System services statistics successfully done"}, 200


# Файловые системы statistics
@app.route("/filesystems-statistics", methods=['POST'])
def filesystems_statistics():
    url = 'http://allta.devos.astralinux.ru:7777/base-statistics'
    data = {
            'title_statistics':'Файловые системы',
            'username': request.form.get('username'),
            'token': request.form.get('token'),
            'set_of_test_types': statistics_conf['Файловые системы']["set_of_test_types"],
            'comparison_list': statistics_conf['Файловые системы']["comparison_list"]
    }

    headers = {
    'Content-Type': 'application/json'
    }

    requests.post(url=url, data=json.dumps(data), headers=headers)
    return {"status": "success", "message": "Recalculate File systems statistics successfully done"}, 200


@app.route('/rest/api/get-testname-columns', methods=['GET'])
def get_testname_columns():
    return jsonify(testname_columns)


@app.route('/rest/api/get-times', methods=['GET'])
def get_times():
    return send_file('./templates/times.html', as_attachment=True)


@app.route('/rest/api/get-stand', methods=['GET'])
def get_stand():
    return send_file('./templates/stand.html', as_attachment=True)


@app.route('/rest/api/get-astra-config', methods=['GET'])
def get_astra_config():
    return send_file('./astra-config.json', as_attachment=True)


@app.route('/rest/api/get-box-config', methods=['GET'])
def get_box_config():
    return send_file('./box-config.json', as_attachment=True)


@app.route('/rest/api/get-jira-url', methods=['GET'])
def get_jira_url():
    return JIRA_URL, 200


@app.route('/rest/api/get-confluence-url', methods=['GET'])
def get_confluence_url():
    return CONFLUENCE_URL, 200


@app.route('/rest/api/get-repo-path', methods=['GET'])
def get_repo_path():
    return send_file('./releases.json', as_attachment=True)


@app.route('/rest/api/get-repo-path-as-json', methods=['GET'])
def get_repo_path_2():
    with open('./releases.json', 'r') as r:
        releases = r.read()
    return releases


@app.route('/rest/api/get-ping1-pic', methods=['GET'])
def get_ping1_pic():
    return send_from_directory('static', 'ping1.jpeg')


@app.route('/rest/api/get-ping2-pic', methods=['GET'])
def get_ping2_pic():
    return send_from_directory('static', 'ping2.jpg')


@app.route('/rest/api/known-bugs', methods=['GET'])
def get_known_bugs():
    return jsonify(known_bugs), 200


@app.route('/rest/api/available-kernels-from-<rc>', methods=['POST'])
def available_kernels_from_rc(rc):
    return get_kernels_from_rc(version_rc=rc, get_list=True)

@app.route('/rest/api/annotations', methods=['GET'])
def get_annotations():
    response = jsonify(annotations)
    response.headers['Content-Type'] = 'application/json; charset=utf-8'
    return response, 200


# if __name__ == '__main__':
#     app.run(host='127.0.0.1', port=8000, debug=True)


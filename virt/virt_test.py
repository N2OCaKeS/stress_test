from libs.virtlib import (check_output_command, 
                          cmd, 
                          send_remote_command,
                          create_remote_file,
                          get_remote_file)
from virt_conf import TEST_MASHINES, TESTDIR
from threading import Thread
import requests
import os
import datetime


astra_config_url = 'http://bendiks.devos.astralinux.ru/rest/api/get-astra-config'
response_ac = requests.get(astra_config_url)
if response_ac.status_code == 200:
    with open('astra-config.json', 'wb') as acb:
        acb.write(response_ac.content)
else:
    print(f'Failed to get file from {astra_config_url}: {response_ac.status_code}')

vms = [f'testvm{number}' for number in range(1, TEST_MASHINES + 1)]
box_name = 'smolensk-vanilla-gui/1.8.0.14'
box_url = 'http://qa111.devos.astralinux.ru/vault/vagrant/smolensk-vanilla-gui-1.8.0.json'
rc_name = '1.8.0.14'
check_vm_ip = "virsh domifaddr {} | awk '{{print $4}}' | tail -n 2"
set_exec_bit = 'sudo chmod +x /home/{}/cpu_load'
run_test = 'cd /home/{} && sudo ./cpu_load'
power_off = 'virsh destroy {}'
user = 'vagrant'
password = 'vagrant'
stop_host_monitor = False
load_host_monitor_results = {}

if not os.path.isdir(TESTDIR):
    os.mkdir(TESTDIR)

# add_box
cmd(f'vagrant box add --provider virtualbox {box_name} {box_url}')
cmd(f'vagrant mutate {box_name} libvirt --input-provider virtualbox --force-virtio')

# create_vm
cmd(f'UPDATE={box_name} BOX_URL={box_url} RC={rc_name} vagrant up --provider=libvirt')

vm_dates = {
    vm: {
        'ip': check_output_command(check_vm_ip.format(vm)).split('/')[0],
        'login':f'{user}',
        'password':f'{password}'
        } 
    for vm in vms
}
print(f'VM dates is:\n{vm_dates}')


try: 
    [
        create_remote_file(local_file_path='./libs/cpu_load', 
                           remote_file_path=f'/home/{user}/cpu_load', 
                           ip=vm_dates[vm]['ip'], 
                           user=user, 
                           password=password) 
                           for vm in vms
    ]
except Exception as e:
    print(f'Error is: {str(type(e).__name__)}\nMessage: {str(e)}')

try:
    [
        send_remote_command(command=set_exec_bit.format(user),
                            ip=vm_dates[vm]['ip'], 
                            user=user, 
                            password=password) 
                            for vm in vms
    ]
except Exception as e:
    print(f'Error is: {str(type(e).__name__)}\nMessage: {str(e)}')


def load_host_monitor():
    global stop_host_monitor
    global load_host_monitor_results

    def __check_cpu_load():
            comm = """top -bn1 | grep '%Cpu' | tail -1 | awk '{gsub(",",".",$8); printf "%s", 100-$8 "%"}'"""
            result = check_output_command(comm)
            return result
    
    while not stop_host_monitor:
        load_host_monitor_results[datetime.datetime.now().strftime('%H:%M:%S')] = __check_cpu_load()
             
    with open(f'{TESTDIR}/host_results.txt', 'w') as w:
        w.write(f'{load_host_monitor_results}\n')


def run_vm_test(vm):
    try:
        send_remote_command(command=run_test.format(user),
                            ip=vm_dates[vm]['ip'], 
                            user=user, 
                            password=password)        
    except Exception as e:
        print(f'Error is: {str(type(e).__name__)}\nMessage: {str(e)}')

thread_monitor = Thread(target=load_host_monitor)
thread_monitor.start()

threads = []
for vm in vms:
    thread = Thread(target=run_vm_test, args=(vm,))
    thread.start()
    threads.append(thread)
[thread.join() for thread in threads]
stop_host_monitor = True

if thread_monitor.is_alive():
    thread_monitor.join()
    

try: 
    [
        get_remote_file(remote_file_path=f'/home/{user}/result.txt',
                        local_file_path=f'{TESTDIR}/result_{vm}.txt',
                        ip=vm_dates[vm]['ip'], 
                        user=user, 
                        password=password) 
                        for vm in vms
    ]
except Exception as e:
    print(f'Error is: {str(type(e).__name__)}\nMessage: {str(e)}')


# Run before end general test, else every VM will shutdown 300 sec before reboot
def vms_off():
    try:
        [
            cmd(power_off.format(vm_name) for vm_name in vms)
        ]
    except Exception as e:
        print(f'Error is: {str(type(e).__name__)}\nMessage: {str(e)}')


vms_off()
        
print(load_host_monitor_results)


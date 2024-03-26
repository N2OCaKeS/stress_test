from libs.virtlib import (check_output_command, 
                          cmd, 
                          send_remote_command,
                          create_remote_file,
                          get_remote_file)
from virt_conf import TEST_MASHINES
from threading import Thread


vms = TEST_MASHINES
box_name = 'smolensk-vanilla-gui/1.8.0.14'
box_url = 'http://qa111.devos.astralinux.ru/vault/vagrant/smolensk-vanilla-gui-1.8.0.json'
check_vm_ip = "virsh domifaddr {} | awk '{{print $4}}' | tail -n 2"
set_exec_bit = 'sudo chmod +x /home/{}/cpu_load'
run_test = 'cd /home/{} && sudo ./cpu_load'
user = 'vagrant'
password = 'vagrant'

# add_box
cmd(f'vagrant box add --provider virtualbox {box_name} {box_url}')
cmd(f'vagrant mutate {box_name} libvirt --input-provider virtualbox --force-virtio')

# create_vm
cmd(f'UPDATE={box_name} BOX_URL={box_url} vagrant up --provider=libvirt')

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


def run_vm_test(vm):
    try:
        send_remote_command(command=run_test.format(user),
                            ip=vm_dates[vm]['ip'], 
                            user=user, 
                            password=password)        
    except Exception as e:
        print(f'Error is: {str(type(e).__name__)}\nMessage: {str(e)}')

threads = []
for vm in vms:
    thread = Thread(target=run_vm_test, args=(vm,))
    thread.start()
    threads.append(thread)
[thread.join() for thread in threads]
    

try: 
    [
        get_remote_file(remote_file_path=f'/home/{user}/result.txt',
                        local_file_path=f'./result_{user}.txt',
                        ip=vm_dates[vm]['ip'], 
                        user=user, 
                        password=password) 
                        for vm in vms
    ]
except Exception as e:
    print(f'Error is: {str(type(e).__name__)}\nMessage: {str(e)}')




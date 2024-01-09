import subprocess
import os
from time import sleep


set_box = '174'


def check_output_command(command: str):
    result = subprocess.Popen([command], shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    output, errors = result.communicate()
    output = os.linesep.join([s for s in output.splitlines() if s])
    errors = os.linesep.join([s for s in errors.splitlines() if s])
    if errors == "":
        return output
    else:
        return errors

def cmd(command):
    return subprocess.run(command, shell=True).returncode



vagrant_boxes = {'1804':{'url':'http://qa111.devos.astralinux.ru/vault/vagrant/smolensk-vanilla-gui-1.8.0.json',
                         'name':'smolensk-vanilla-gui/1.8.0.4'},
                 '1803':{'url':'http://qa111.devos.astralinux.ru/vault/vagrant/smolensk-vanilla-gui-1.8.0.json',
                         'name':'smolensk-vanilla-gui/1.8.0.3'},
                 '1802':{'url':'http://qa111.devos.astralinux.ru/vault/vagrant/smol-1.8.0.json',
                         'name':'smolensk-vanilla-gui/1.8.0.2'},
                 '174':{'url':'http://qa111.devos.astralinux.ru/vault/vagrant/smolensk-vanilla-gui-1.7.4.json',
                        'name':'smolensk-vanilla-gui/1.7.4'},
                 '175':{'url':'http://qa111.devos.astralinux.ru/vault/vagrant/smolensk-vanilla-gui-1.7.5.json',
                        'name':'smolensk-vanilla-gui/1.7.5'}       
                 }

box_url = vagrant_boxes[set_box]['url']
box_name = vagrant_boxes[set_box]['name']
VMs = ['database1', 'database2', 'database3', 'lbdb1', 'lbdb2', 'lbdb3', 'dcfreeipa']
no_fprint = '-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null'
vbox_nat = 'QANetwork'
vbox_std_name_interface = 'vboxnet0'
vbox_nat_ip = '10.0.0.0'
vbox_subnet_mask = '19'
vbox_bridge_network = '10.177.103.0'
vbox_bridge_ip = '10.177.103.1'
vbox_bridge_mask = '255.255.224.0'
attempts_count = 0
ansible_commands = [
    'sudo -u u ansible-playbook bl_contrprimer.yml',
    'sudo -u u ansible-playbook tasks/checks/db/replication.yml',
    'sudo -u u ansible-playbook tasks/checks/db/load_balancing.yml',
    'sudo -u u ansible-playbook tasks/tests/HA_DB_upgrade/high_availability_db_upgrade.yml'
]

vm_dates = {
        'database1':{'host-port':'2021',
                     'ip':'10.0.0.11',
                     'sshnum':'',
                     'ip_bridge':'10.177.103.111'},
        'database2':{'host-port':'2022',
                     'ip':'10.0.0.12',
                     'sshnum':'1',
                     'ip_bridge':'10.177.103.112'},
        'database3':{'host-port':'2025',
                     'ip':'10.0.0.13',
                     'sshnum':'2',
                     'ip_bridge':'10.177.103.113'},
        'lbdb1':{'host-port':'2024',
                 'ip':'10.0.0.41',
                 'sshnum':'3',
                 'ip_bridge':'10.177.103.141'},
        'lbdb2':{'host-port':'2023',
                 'ip':'10.0.0.42',
                 'sshnum':'4',
                 'ip_bridge':'10.177.103.142'},
        'lbdb3':{'host-port':'2026',
                 'ip':'10.0.0.43',
                 'sshnum':'5',
                 'ip_bridge':'10.177.103.143'},
        'pgpool':{'host-port':'2027',
                  'ip':'10.0.0.31',
                  'sshnum':'6',
                  'ip_bridge':'10.177.103.131'},
        'dcfreeipa':{'host-port':'2029',
                     'ip':'10.0.0.10',
                     'sshnum':'7',
                     'ip_bridge':'10.177.103.110'}
}


def vm_port(vm_name):
    bash_command = f"""sudo vboxmanage showvminfo {vm_name} | grep 'Rule' \
                    | awk -F',' '{{for(i=1;i<=NF;i++) if ($i ~ /host port/) print $i}}' \
                    | awk '{{print $NF}}'"""
    return check_output_command(bash_command)

def colors(text, color:str) -> str:
    reset = '\033[0m'
    yellow = '\033[93m'
    green = '\033[92m'
    red = '\033[91m'

    if color == 'yellow':
        return f'{yellow}{text}{reset}'
    elif color == 'green':
        return f'{green}{text}{reset}'
    elif color == 'red':
        return f'{red}{text}{reset}'

def set_natnetwork(vm, nat_name):
    try:
        cmd(f'vboxmanage controlvm {vm} poweroff'); sleep(1)
        if check_output_command("vboxmanage natnetwork list | grep Name | awk '{print$2}'") != vbox_nat:
            cmd(f'vboxmanage natnetwork add --netname {vbox_nat} --network "{vbox_nat_ip}/{vbox_subnet_mask}" --enable')
        cmd(f'vboxmanage modifyvm {vm} --nic1 natnetwork')
        cmd(f'vboxmanage modifyvm {vm} --natnetwork1 {nat_name}')
        cmd(f'vboxmanage modifyvm "{vm}" --nic1 natnetwork --nat-network1 {nat_name}')
        cmd(f'''vboxmanage natnetwork modify --netname {nat_name} --port-forward-4 \
            "ssh{vm_dates[vm]['sshnum']}:tcp:[]:{vm_dates[vm]['host-port']}:[{vm_dates[vm]['ip']}]:22"''')
        cmd(f'vboxmanage startvm {vm} --type headless')
    except Exception as e:
        print(f'Type:{str(type(e).__name__)},\nError: {str(e)}')

def set_hostonly_network(vm, adapter_name):
    try:
        with open('/etc/vbox/networks.conf', 'w') as wr:
            wr.write(f'* {vbox_bridge_network}/{vbox_subnet_mask} {vbox_std_name_interface}')
        cmd('systemctl restart vboxdrv vboxballoonctrl-service vboxautostart-service vboxweb-service')
        cmd(f'vboxmanage controlvm {vm} poweroff'); sleep(1)
        if adapter_name not in check_output_command("vboxmanage list hostonlyifs | grep Name | awk '{print $2}'"):
            cmd(f'vboxmanage hostonlyif create')
            cmd(f'vboxmanage hostonlyif ipconfig {adapter_name} --ip {vbox_bridge_ip} --netmask {vbox_bridge_mask}')
        cmd(f'vboxmanage modifyvm {vm} --nic1 hostonly')
        cmd(f'vboxmanage modifyvm {vm} --hostonlyadapter1 {adapter_name}')
        cmd(f'vboxmanage startvm {vm} --type headless')
    except Exception as e:
        print(f'Type:{str(type(e).__name__)},\nError: {str(e)}')

def set_bridge_network(vm, adapter_name):
    try:
        # if vm == 'dcfreeipa':
        #     num_interface = '1'
        # else: num_interface = '2'
        num_interface = '1'

        cmd(f'vboxmanage controlvm {vm} poweroff'); sleep(1)
        cmd(f'vboxmanage modifyvm {vm} --nic{num_interface} bridged')
        cmd(f'vboxmanage modifyvm {vm} --bridgeadapter{num_interface} {adapter_name}')
        cmd(f'vboxmanage startvm {vm} --type headless')
    except Exception as e:
        print(f'Type:{str(type(e).__name__)},\nError: {str(e)}')

def check_vm_list():
    return check_output_command('vboxmanage list vms')

def backup_vms_snapshots():
    status_code = []
    status_code += [cmd(f'vboxmanage controlvm {vm} poweroff') for vm in VMs]
    status_code += [cmd(f"vboxmanage snapshot {vm} restore 'snapshot_1'") for vm in VMs]
    status_code += [cmd(f'vboxmanage startvm {vm} --type headless') for vm in VMs]
    return sum(i > 0 for i in status_code)

def check_ping():
    task_code = [0]

    def create_vm(vm: str):
        vm_list = [vm, 'test'] #добавление ВМ 'test' устраняет баг с некорректным импортом репозитория
        [cmd(f'vboxmanage controlvm {vm} poweroff') for vm in vm_list]
        [cmd(f'vboxmanage  unregistervm --delete {vm}') for vm in vm_list]
        [cmd(f'UPDATE={box_name} BOX_URL={box_url} VM_NAME={vm} vagrant up --provider=virtualbox') for vm in vm_list]
        cmd(f'vboxmanage controlvm {vm} poweroff')
        cmd(f'vboxmanage startvm {vm} --type headless')
        cmd(f'vboxmanage snapshot "{vm}" take "snapshot_1"')
        colors("Result reinstall VM:\n", "yellow")
        if cmd(f"ping -c 1 {vm_dates[vm]['ip_bridge']}") != 0:
            task_code[0] += 1

    [create_vm(vm) for vm in VMs if cmd(f"ping -c 1 {vm_dates[vm]['ip_bridge']}") != 0]
    return int(task_code[0])

# # #Prepare
cmd('sudo bash bl_prepare_vbox.sh')

# # # Создать ВМ
cmd(f'vagrant box add {box_url} --force')
cmd(f'UPDATE={box_name} BOX_URL={box_url} vagrant up --provider=virtualbox')

# # # Network set
bridge_iface = check_output_command("vboxmanage list bridgedifs | grep Name | awk '{print$2}' | head -n 1")
print(f'Bridge interface found as: {colors(bridge_iface, "yellow")}')
#cmd(f'vboxmanage natnetwork add --netname {vbox_nat} --network "10.0.0.0/19" --enable --dhcp on')
#[set_natnetwork(vm, vbox_nat) for vm in VMs if vm in check_vm_list()]
[set_bridge_network(vm, bridge_iface) for vm in VMs if vm in check_vm_list()]
[cmd(f'vboxmanage snapshot "{vm}" take "snapshot_1"') for vm in VMs if vm in check_vm_list()]
cmd('vboxmanage natnetwork list')
cmd('vboxmanage list hostonlyifs')
cmd('vboxmanage list bridgedifs')
cmd('vboxmanage list vms')

if check_ping() != 0:
    check_count = 0
    while check_count < 10:
        if check_ping() != 0:
            check_count += 1
        else: break
    if check_count >= 10:
        print('Не удалось решить проблемы с настройкой сети, ВМ недоступна(ы)')
        exit(1)


# # # SSH connect info
#vm_ports = {name:f'ssh u@localhost -p {vm_port(name)}' for name in VMs}
#vm_ports = {name:f'sshpass -v -p 1 ssh {no_fprint} u@localhost -p {vm_dates[name]["host-port"]}' for name in VMs}
vm_creds = {name:f'sshpass -v -p 1 ssh {no_fprint} u@{vm_dates[name]["ip_bridge"]}' for name in VMs}
#[cmd(f'ssh-keygen -R [127.0.0.1]:{port}') for port in vm_ports.keys()]
print('\n***--------- Connecting credentials ---------***\n')
#for key, value in vm_ports.items():
#    print(colors(key, 'green'), value)
for key, value in vm_creds.items():
    print(colors(key, 'yellow'), value)


while attempts_count < 10:
    for command in ansible_commands:
        print(colors(f'Begin task: {command}', 'yellow'))
        result_code = cmd(command)
        print(f'\nResult code: {result_code}\n')
        if command == ansible_commands[-1] and result_code == 0:
            print('Ansible commands cycle is fully executed')
        if result_code != 0:
            print(f'\nResult code: {colors(result_code, "red")}\n')
            negotive_attempt = 0
            while negotive_attempt < 5:
                if command == ansible_commands[0]:
                    if backup_vms_snapshots() == 0:
                        if check_ping() != 0:
                            negotive_attempt += 1
                    else:
                        print('При восстановлении снимков произошла ошибка')
                        negotive_attempt += 1
                        break
                result_code = cmd(command)
                print(f'\nResult code: {result_code}\n')
                if result_code == 0:
                    break
                else: 
                    negotive_attempt += 1
                    print(f'\nResult code: {colors(result_code, "red")}\n')
            if negotive_attempt >= 5:
                attempts_count += 1
                if backup_vms_snapshots() == 0:
                    if check_ping() != 0:
                        negotive_attempt += 1
                else:
                    print('При восстановлении снимков произошла ошибка')
                break
    else:
        attempts_count += 1
        break

print(f'Attempts count was: {attempts_count}')


#sudo vboxmanage showvminfo database3
#sudo vboxmanage startvm database3 --type headless
#VBoxManage controlvm database3 poweroff
#VBoxManage unregistervm --delete "VM name"
#ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null u@localhost -p 2200

#VBoxManage controlvm test poweroff
#VBoxManage clonehd "box-disk001.vmdk" "box-disk001.vdi" --format vdi
#VBoxManage modifymedium disk "box-disk001.vdi" --resize 50000
#rm box-disk001.vmdk
#VBoxManage clonehd "box-disk001.vdi" "box-disk002.vmdk" --format vmdk
#rm box-disk001.vmdk
#mv box-disk002.vmdk box-disk001.vmdk
#vboxmanage startvm test --type headless


backup_VMs = '''for vm in 'database1' 'database2' 'database3' 'lbdb1' 'lbdb2' 'lbdb3' 'dcfreeipa'; do sudo vboxmanage controlvm "$vm" poweroff; done \
              for vm in 'database1' 'database2' 'database3' 'lbdb1' 'lbdb2' 'lbdb3' 'dcfreeipa'; do sudo vboxmanage snapshot "$vm" restore 'snapshot_1'; done \
              for vm in 'database1' 'database2' 'database3' 'lbdb1' 'lbdb2' 'lbdb3' 'dcfreeipa'; do sudo vboxmanage startvm "$vm" --type headless; done'''

#for vm in 'database1' 'database2' 'database3' 'lbdb1' 'lbdb2' 'lbdb3' 'dcfreeipa'; do sudo vboxmanage showvminfo "$vm" | grep State; done

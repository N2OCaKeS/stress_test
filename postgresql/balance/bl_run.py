import subprocess
import os
from time import sleep


box_url_18 = 'http://qa111.devos.astralinux.ru/vault/vagrant/smol-1.8.0.json'
box_name_18 = 'smolensk-vanilla-gui/1.8.0.2'
box_url_174 = 'http://qa111.devos.astralinux.ru/vault/vagrant/smolensk-vanilla-gui-1.7.4.json'
box_name_174 = 'smolensk-vanilla-gui/1.7.4'
box_url_175 = 'http://qa111.devos.astralinux.ru/vault/vagrant/smolensk-vanilla-gui-1.7.5.json'
box_name_175 = 'smolensk-vanilla-gui/1.7.5'
VMs = ['database1', 'database2', 'database3', 'lbdb1', 'lbdb2', 'lbdb3', 'pgpool', 'dcfreeipa']
no_fprint = '-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null'
vbox_nat = 'QANetwork'
vbox_std_name_interface = 'vboxnet0'
vbox_nat_ip = '10.0.0.0'
vbox_subnet_mask = '19'
vbox_bridge_network = '10.177.103.0'
vbox_bridge_ip = '10.177.103.1'
vbox_bridge_mask = '255.255.224.0'

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
    return subprocess.run(command, shell=True)

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

def set_bridge_network(vm, adapter_name):
    try:
        cmd(f'vboxmanage controlvm {vm} poweroff'); sleep(1)
        if adapter_name not in check_output_command("vboxmanage list hostonlyifs | grep Name | awk '{print $2}'"):
            cmd(f'vboxmanage hostonlyif create')
            cmd(f'vboxmanage hostonlyif ipconfig {adapter_name} --ip {vbox_bridge_ip} --netmask {vbox_bridge_mask}')
        cmd(f'vboxmanage modifyvm {vm} --nic1 hostonly')
        cmd(f'vboxmanage modifyvm {vm} --hostonlyadapter1 {adapter_name}')
        cmd(f'vboxmanage startvm {vm} --type headless')
    except Exception as e:
        print(f'Type:{str(type(e).__name__)},\nError: {str(e)}')

def check_vm_list():
    return check_output_command('vboxmanage list vms')


# # #Prepare
cmd('sudo bash bl_prepare_vbox.sh')

# # # Создать ВМ
cmd(f'vagrant box add {box_url_174} --force')
cmd(f'UPDATE={box_name_174} BOX_URL={box_url_174} vagrant up --provider=virtualbox')

# # # Network set
with open('/etc/vbox/networks.conf', 'w') as wr:
    wr.write(f'* {vbox_bridge_network}/{vbox_subnet_mask} {vbox_std_name_interface}')
cmd('systemctl restart vboxdrv vboxballoonctrl-service vboxautostart-service vboxweb-service')
#cmd(f'vboxmanage natnetwork add --netname {vbox_nat} --network "10.0.0.0/19" --enable --dhcp on')
#[set_natnetwork(vm, vbox_nat) for vm in VMs if vm in check_vm_list()]
[set_bridge_network(vm, vbox_std_name_interface) for vm in VMs if vm in check_vm_list()]
cmd('vboxmanage natnetwork list')
cmd('vboxmanage list hostonlyifs')
cmd('vboxmanage list vms')


# # # SSH connect info
#vm_ports = {name:f'ssh u@localhost -p {vm_port(name)}' for name in VMs}
#vm_ports = {name:f'sshpass -v -p 1 ssh {no_fprint} u@localhost -p {vm_dates[name]["host-port"]}' for name in VMs}
vm_creds = {name:f'sshpass -v -p 1 ssh {no_fprint} u@{vm_dates[name]["ip_bridge"]}' for name in VMs}
#[cmd(f'ssh-keygen -R [127.0.0.1]:{port}') for port in vm_ports.keys()]
for key, value in vm_creds.items():
    print(colors(key, 'yellow'), value)


#cmd('ansible-playbook bl_contrprimer.yml')
#cmd('ansible-playbook tasks/checks/db/replication.yml')


#sudo vboxmanage showvminfo database3
#sudo vboxmanage startvm database3 --type headless
#VBoxManage controlvm database3 poweroff
#ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null u@localhost -p 2200


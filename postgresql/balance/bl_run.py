import subprocess
import os


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


box_url_18 = 'http://qa111.devos.astralinux.ru/vault/vagrant/smol-1.8.0.json'
box_name_18 = 'smolensk-vanilla-gui/1.8.0.2'
box_url_174 = 'http://qa111.devos.astralinux.ru/vault/vagrant/smolensk-vanilla-gui-1.7.4.json'
box_name_174 = 'smolensk-vanilla-gui/1.7.4'
box_url_175 = 'http://qa111.devos.astralinux.ru/vault/vagrant/smolensk-vanilla-gui-1.7.5.json'
box_name_175 = 'smolensk-vanilla-gui/1.7.5'
VMs = ['database1', 'database2', 'database3', 'lbdb1', 'lbdb2', 'lbdb1', 'pgpool', 'dc_freeipa']

cmd('sudo bash bl_prepare.sh')
cmd(f'vagrant box add {box_url_175} --force')

#Создать интерфейс vboxnet0 в Vbox
cmd('VBoxManage hostonlyif create')

#Создать ВМ
cmd(f'UPDATE={box_name_175} vagrant up --provider=virtualbox')

#Задать интерфейсу vboxnet0 ip адрес
cmd('VBoxManage hostonlyif ipconfig vboxnet0 --ip 192.168.60.1')

vm_ports = {name:f'ssh u@localhost -p {vm_port(name)}' for name in VMs}
for item in vm_ports.items():
    print(item)


import subprocess
import os
from time import sleep
from libs.zefir import UploaderZC
from psb_conf import REPORT_PATH
import pandas
import argparse
import json
import requests


"""
INFO

pgpool2 имеет некоторую особенность, что при совершении failover или failback он на короткий промежуток времени запрещает подключение пользователей, 
из-за чего pgbench завершается с ошибкой, и не получается идеальной доступности сервиса.
Поэтому вместо использования pgbench был написан свой скрипт для симуляции клиентов, который спокойно переваривает ошибки подключения и просто 
записывает их как неудачные запросы. 
Оказалось, что даже при текущем поведении в failover/failback получается не так уж и много запросов не обрабатывается кластером (при обновлении 3 БД со связкой с 3-мя балансировщиками), 
всего не больше 150 при общем количестве запросов около 67000, что составляет в среднем не больше 0.2% проваленных запросов. Кроме того, 
ниже я скинул ссылку на коммит из Github репозитория pgpool2, где наконец летом этого года кто-то занялся этой проблемой с запретом подключения пользователей, 
так что в будущем можно ожидать, что процент проваленных запросов при обновлении будет ещё меньше.
https://github.com/pgpool/pgpool2/commit/4aa657e055250da9db9a4c5cde7260e8f24707cb
"""


parser = argparse.ArgumentParser()
parser.add_argument('-sn',
                    action='store',
                    required=True,
                    help='stand number',
                    dest='STAND')

parser.add_argument('-fti', '--folder-tree-id',
                    action='store',
                    required=True,
                    help='folder-tree-id',
                    dest='FTI')

parser.add_argument('-tcyc', '--test-cycle-name',
                    action='store',
                    required=True,
                    help='test-cycle-name',
                    dest='TCYC')

parser.add_argument('-tcas', '--test-case-name',
                    action='store',
                    required=True,
                    help='test-case-name',
                    dest='TCAS')

parser.add_argument('-ba', '--basic-auth',
                    action='store',
                    required=True,
                    help='basic-auth',
                    dest='BA')

parser.add_argument('-u', '--username',
                    action='store',
                    required=True,
                    help='confluence user',
                    dest='USER')

parser.add_argument('-tk', '--token',
                    action='store',
                    required=False,
                    default=None,
                    help='confluence access token',
                    dest='TOKEN')

parser.add_argument('-cs', '--confluence-space',
                    action='store',
                    required=True,
                    help='confluence space',
                    dest='SPACE')

parser.add_argument('-cpp', '--confluence-parent-page',
                    action='store',
                    required=True,
                    help='confluence parent page',
                    dest='PPAGE')

parser.add_argument('-cnp', '--confluence-new-page',
                    action='store',
                    required=True,
                    help='confluence new page',
                    dest='NPAGE')

parser.add_argument('-tcv', '--test-cycle-version',
                    action='store',
                    required=True,
                    help='test-cycle-version',
                    dest='TCV')

parser.add_argument('-vbox', '--set-vbox',
                    action='store',
                    required=True,
                    help='set-vbox to vm',
                    dest='SET_BOX')

args = parser.parse_args()


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


current_dir = os.path.dirname(os.path.abspath(__file__))
# vbox_path = os.path.join(current_dir, 'balance', 'vbox.json')
# with open(vbox_path, 'r') as vbox:
#     vagrant_boxes = json.load(vbox)

# set_box = args.SET_BOX + '.s'
# check_len_version = set_box.split('.')
# if len(check_len_version) == 7 and check_len_version[3] == 'UU':
#     set_box = '.'.join(check_len_version[:5]) + '.s'
# elif len(check_len_version) == 5 and check_len_version[3] != 'UU':
#     set_box = '.'.join(check_len_version[:3]) + '.s'
# box = [i for i in vagrant_boxes['vagrant_box'] if set_box in i]
# box_url = box[0][set_box][1]
# box_name = box[0][set_box][0]

astra_config_url = 'http://bendiks.devos.astralinux.ru/rest/api/get-astra-config'
response_ac = requests.get(astra_config_url)
if response_ac.status_code == 200:
    with open('astra-config.json', 'wb') as acb:
        acb.write(response_ac.content)
else:
    print(f'Failed to get file from {astra_config_url}: {response_ac.status_code}')

with open('astra-config.json', 'r') as r:
    dates = json.loads(r.read())

def __box_wrapper(box):
    true_key = False
    box_name = ''
    box_url = ''
    for i in dates['astra-version']['vagrant_box']:
        if box in str(i):
            for key in i.keys():
                if str(key).endswith('s'):
                    true_key = key
                    box_name = i[true_key][0]
                    box_url = i[true_key][1]             
            
    if true_key == False:
        for i in dates['astra-version']['vagrant_box']:
            if str(box).startswith('1.7'):
                if '1.7.1.s' in str(i):
                    box_name = i['1.7.1.s'][0]
                    box_url = i['1.7.1.s'][1]
            elif str(box).startswith('1.8'):
                if '1.8.0.s' in str(i):
                    box_name = i['1.8.0.s'][0]
                    box_url = i['1.8.0.s'][1]
    
    return box_name, box_url

box_name, box_url = __box_wrapper(args.SET_BOX)
kernel = str(args.TCYC).split('_')[2]
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
# ansible_commands = [
#     'cd balance && sudo -u u ansible-playbook bl_contrprimer.yml -v',
#     'cd balance && sudo -u u ansible-playbook tasks/checks/db/replication.yml',
#     'cd balance && sudo -u u ansible-playbook tasks/checks/db/load_balancing.yml',
#     'cd balance && sudo -u u ansible-playbook tasks/tests/HA_DB_upgrade/high_availability_db_upgrade.yml'
# ]
ansible_commands = [
    'cd balance && ansible-playbook bl_contrprimer.yml -vvv',
    'cd balance && ansible-playbook tasks/checks/db/replication.yml -vv',
    'cd balance && ansible-playbook tasks/checks/db/load_balancing.yml -vv',
    'cd balance && ansible-playbook tasks/tests/HA_DB_upgrade/high_availability_db_upgrade.yml -vv'
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

def colors(text, color: str) -> str:
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


class CheckVMs():

    def __init__(self,
                 rebuild=False):
        
        self.rebuild = rebuild
        self.task_code = [0]

    def check_ping(self):
        bad_vms = [vm for vm in VMs if cmd(f"ping -c 1 {vm_dates[vm]['ip_bridge']}") != 0]
        return bad_vms

    def create_vm(self, vm: str):
        vm_list = [vm, 'test'] #добавление ВМ 'test' устраняет баг с некорректным импортом репозитория
        [cmd(f'vboxmanage controlvm {vm} poweroff') for vm in vm_list]
        [cmd(f'vboxmanage  unregistervm --delete {vm}') for vm in vm_list]
        [cmd(f'cd balance &&  UPDATE={box_name} BOX_URL={box_url} VM_NAME={vm} KERNEL={kernel} RC={args.SET_BOX} vagrant up --provider=virtualbox') for vm in vm_list]
        cmd(f'vboxmanage controlvm {vm} poweroff')
        cmd(f'vboxmanage startvm {vm} --type headless')
        cmd(f'vboxmanage snapshot "{vm}" take "snapshot_1"')
        colors("Result reinstall VM:\n", "yellow")
        if cmd(f"ping -c 1 {vm_dates[vm]['ip_bridge']}") != 0:
            self.task_code[0] += 1
        return int(self.task_code[0])

    def build_all_vms(self):
        if self.rebuild == True:
            def dir_is_empty(path):
                return len(os.listdir(path)) == 0

            boxes_path = '/root/.vagrant.d/boxes/'
            vms_path = '/root/VirtualBox\\ VMs/'

            # if os.path.isdir(boxes_path) and not dir_is_empty(boxes_path):
            #     cmd(f'rm -r {boxes_path}*')
            #     print(f'*** {boxes_path} cleared')
            # else: print(f'Some problem with remove {boxes_path}')
            # if os.path.isdir(vms_path) and not dir_is_empty(vms_path):
            #     cmd(f'rm -r {vms_path}*')
            #     print(f'*** {vms_path} cleared')
            # else: print(f'Some problem with remove {vms_path}')

            [cmd(f'vboxmanage controlvm {vm} poweroff') for vm in VMs]
            [cmd(f'vboxmanage  unregistervm --delete {vm}') for vm in VMs]

            try:
                cmd(f'rm -r {boxes_path}*')
                print(f'*** {boxes_path} cleared')
                cmd(f'rm -r {vms_path}*')
                print(f'*** {vms_path} cleared')
            except Exception as e:
                print(str(e))
                print(f'Some problem with remove')

        cmd(f'cd balance && vagrant box add {box_url} --force')
        cmd(f'cd balance && UPDATE={box_name} BOX_URL={box_url} KERNEL={kernel} RC={args.SET_BOX} vagrant up --provider=virtualbox')

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

    def check_available_vms(self):
        attempt_count = 1
        check_count = 0
        if vm.check_ping():
            vm.rebuild = True
            vm.build_all_vms()
            check_count += 1
            while check_count < attempt_count:
                if vm.check_ping():
                    vm.build_all_vms()
                    check_count += 1
                    print('Check count: ' + str(check_count))
                else: 
                    print(colors('All vms is available', 'green'))
                    break
            if check_count >= 1:
                print(colors('Не удалось решить проблемы с настройкой сети, ВМ недоступна(ы)', 'red'))
                uzs.upload_test_cycle_status(zefir_status='fail')
                exit(1)
        else: print(colors('All vms is available', 'green'))


vm = CheckVMs()
uzs = UploaderZC(folder_tree_id=args.FTI,
                test_cycle_name=args.TCYC,
                test_case_name=args.TCAS,
                basic_auth=args.BA,
                test_cycle_version=args.TCV,
                token=args.TOKEN,
                username=args.USER,
                grade_stand=args.STAND,
                conf_space=args.SPACE,
                conf_parent_page=args.PPAGE,
                conf_new_page_name=args.NPAGE)
uzs.upload_test_cycle_status('progress')



# # #Prepare
cmd('sudo bash balance/bl_prepare_vbox.sh')

# # # Create VMs 
vm.build_all_vms()
vm.check_available_vms()


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


while attempts_count < 2:
    for command in ansible_commands:
        print(colors(f'Begin task: {command}', 'yellow'))
        result_code = cmd(command)
        print(f'\nResult code: {result_code}\n')
        if command == ansible_commands[-1] and result_code == 0:
            print(colors('Ansible commands cycle is fully executed', 'green'))
        if result_code != 0:
            print(f'\nResult code: {colors(result_code, "red")}\n')
            negotive_attempt = 0
            while negotive_attempt < 2:
                if command == ansible_commands[0]:
                    if backup_vms_snapshots() != 0:
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
                    if command == ansible_commands[0]:
                        vm.check_available_vms()
            if negotive_attempt >= 2:
                if command == ansible_commands[0]:
                    vm.rebuild = True
                    vm.build_all_vms()
                    vm.check_available_vms()
                attempts_count += 1
                #break
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


if os.path.isfile('results_balance.txt'):
    print('File "results_balance.txt" exist')
    with open('results_balance.txt', 'r') as r:
        results = dict(line.rstrip().split(':') for line in r)
        print(results)
    
    dates = {'Name':results.keys(),
             'Results':results.values()}
    
    if not os.path.exists(REPORT_PATH):
        os.mkdir(REPORT_PATH, mode=0o755)
    
    df = pandas.DataFrame(dates); print(df)
    df.to_html(f'{REPORT_PATH}/results_balance.html', index=False)

    uzs.public = True
    uzs.statistics = True
    uzs.balance = True
    uzs.upload_test_cycle_status(zefir_status='pass')
else:
    print('Fail! File "results_balance.txt" not found')
    uzs.upload_test_cycle_status(zefir_status='fail')


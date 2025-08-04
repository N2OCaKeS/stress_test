from allta import Libvirt, LibvirtManager, SystemCommands
import json

class VM:

    def create(info_path, box: str, rc: str, kernel: str):
        lv = Libvirt()
        with open(info_path, "r", encoding="UTF-8") as f:
            json_string = f.read()
            vms_dates: dict = json.loads(json_string)
        vms_list: list = list(vms_dates.keys())
        new_vms_dates = lv.build(box=box, rc=rc, vms=vms_list, vms_dates=vms_dates, kernel=kernel)
        group = {'all':vms_list}

        # Dependeincies
        deps = "htop ssh git resolvconf sysstat"

        # Git
        create_git = """cat > "$OUTFILE" <<'EOF'
#!/bin/python3
import subprocess
from os import getcwd
from sys import exit
from ftplib import FTP

def cmd(command):
    subprocess.run([command], shell=True, check=True)


attention_line = '=' * 130
conf_file = getcwd() + '/gitclone.conf'

#Проверяем директорию запуска
if getcwd() != '/home/u/git':
    print('\n', '\033[1m\033[33mВнимание!!!\033[0m')
    print(attention_line)
    print(f'Текущая директория {getcwd()}')
    print('Запустите скрипт из директории /home/u/git')
    print(attention_line, '\n')
    exit(1)

#Удаляем старый гит
try:
    cmd('sudo rm -r /home/u/git/stress_test')
except Exception as e:
    print('\n', '\033[1m\033[33mВнимание!!!\033[0m')
    print(attention_line)
    print(e)
    print(attention_line, '\n')

#Скачиваем конфиг
def download_conf():
    ftp = FTP('10.177.5.111')
    ftp.login()
    ftp.cwd('stress_reports/stress_test_config')
    with open(conf_file, 'wb') as wf:
        ftp.retrbinary('RETR gitclone.conf', wf.write)
    ftp.quit()
    with open(conf_file, 'r') as r:
        conf = r.read()
    return conf

#Клонируем гит
cmd(download_conf())
EOF
"""

        # Net
        dns = "10.177.180.248, 10.177.128.198"
        gateway = "10.177.103.254"
        mask = "255.255.255.0"
        base_net = 

        commands = {
            "g_all": {
               'set ntp': {
                    'command': f"sudo timedatectl set-ntp true",
                    'signal set': '',
                    'signal get': ''                    
                },                
                'Git file create': {
                    'command': f"{create_git}",
                    'signal set': 'git',
                    'signal get': ''                    
                },
                'Git file privilege': {
                    'command': f"chmod +x /home/u/git/git_clone.py && mkdir /home/u/git/stress_test",
                    'signal set': '',
                    'signal get': ['git' ]                   
                },

                'install depends': {
                    'command': f"sudo apt-get update && sudo apt-get install {deps}",
                    'signal set': 'deps',
                    'signal get': ''                    
                },              
            }
        }

        lv.execute(commands=commands, vms_dates=new_vms_dates, vms_groups=group, username="u", password='1')

        net = {
            vm_name: {
                'Net settings file': {
                    'command': f"""iface=`ip a | grep '2: ' | awk '{{print$2}}' | tr -d ':' | head -n 1` && cat << EOF > /etc/network/interfaces
source /etc/network/interfaces.d/*

# The loopback network interface
auto lo
iface lo inet loopback

auto $iface
iface $iface inet static
        address {vm_data['ip_bridge']}
        netmask {mask}
        gateway {gateway}
        dns-nameservers {dns}
EOF
""",
                    'signal set': 'net',
                    'signal get': ''                  
                },
                'Net restart': {
                    'command': f"sudo systemctl restart networking",
                    'signal set': '',
                    'signal get': ['net']                  
                },  
            }
            for vm_name, vm_data in vms_dates.items()
        }

        lv.execute(commands=net, vms_dates=new_vms_dates, vms_groups=group, username="u", password='1')
        return 0


    def delete(vms: list):
        for vm in vms:
            SystemCommands.cmd_with_returncode(f"sudo virsh -c qemu:///system destroy --domain {vm}")
            SystemCommands.cmd_with_returncode(f"sudo virsh -c qemu:///system undefine --remove-all-storage --delete-storage-volume-snapshots --domain {vm}")
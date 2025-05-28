from allta import Libvirt, LibvirtManager, SystemCommands
from time import sleep


Libvirt.prepare()
kernel = "5.10.190-1-generic"
rc = "1.7.5"

vms = {  # Полный список ВМ
    'virtual-station1': {'host-port': '22',
                  'ip': '10.0.0.11',
                  'sshnum': '',
                  'ip_bridge': '10.177.103.101',
                  'cpu': '16',
                  'ram': '131072'},
    'virtual-station2': {'host-port': '22',
                  'ip': '10.0.0.12',
                  'sshnum': '1',
                  'ip_bridge': '10.177.103.102',
                  'cpu': '16',
                  'ram': '131072'},
    'virtual-station3': {'host-port': '22',
                  'ip': '10.0.0.13',
                  'sshnum': '2',
                  'ip_bridge': '10.177.103.103',
                  'cpu': '16',
                  'ram': '131072'},
    'virtual-station4': {'host-port': '22',
              'ip': '10.0.0.41',
              'sshnum': '3',
              'ip_bridge': '10.177.103.104',
              'cpu': '16',
              'ram': '131072'},
    'work-station1': {'host-port': '22',
              'ip': '10.0.0.41',
              'sshnum': '3',
              'ip_bridge': '10.177.103.201',
              'cpu': '16',
              'ram': '131072'},
    'work-station2': {'host-port': '22',
              'ip': '10.0.0.41',
              'sshnum': '3',
              'ip_bridge': '10.177.103.202',
              'cpu': '16',
              'ram': '131072'},              
}

vms_list = ['virtual-station1', 'virtual-station2', 'virtual-station3', 'virtual-station4', 'work-station1', 'work-station2']

group = {'all': vms_list}
new_vms = Libvirt.build('1.7.5.o', '1.7.5', vms)

scp_prepare = {
    "g_all": [
        {
            'mode': 'push',
            'path_host': './provision/env_provision.sh',
            'path_vm': '/home/u/env_provision.sh'            
        }
    ],        
}

Libvirt.scp(scp_settings=scp_prepare, vms_dates=new_vms, vms_groups=group, username='u', password='1')

prepare = {}

for vm_name in vms:
    prepare[vm_name] = {
        'set hostname': {
            'command': f"sudo hostnamectl set-hostname {vm_name} && if grep -q '^127\\.0\\.1\\.1' /etc/hosts; then sudo sed -i 's/^127\\.0\\.1\\.1.*/127.0.1.1\\{vm_name}/' /etc/hosts; else echo -e '127.0.1.1\\{vm_name}' | sudo tee -a /etc/hosts; fi",
            'signal set': 'hostname',
            'signal get': ''
        },
        'prepare': {
            'command': f"sudo chmod 777 /home/u/env_provision.sh && sudo su -c '/home/u/env_provision.sh {vm_name} {kernel} {rc}'",
            'signal set': 'prepare',
            'signal get': ['hostname']
        },
        'confirm': {
            'command': f"(sleep 2 && sudo shutdown -r now) &",
            'signal set': '',
            'signal get': ['prepare']
        },        
    }

Libvirt.execute(commands=prepare, vms_dates=new_vms, vms_groups=group, username='u', password='1')
sleep(20)

